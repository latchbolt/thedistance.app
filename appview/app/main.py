import json
import logging
from urllib.parse import urlencode, urlparse

import httpx
from authlib.jose import JsonWebKey
from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.auth import require_auth
from app.backfill import backfill as run_backfill
from app.config import get_settings
from app.db import (
    create_import_job,
    delete_activity,
    delete_auth_request,
    delete_oauth_session,
    get_auth_request,
    get_connection,
    init_db,
    save_auth_request,
    save_oauth_session,
    set_import_job_manifest,
    update_import_job_status,
    update_oauth_session_pds_nonce,
    update_oauth_session_tokens,
    upsert_activity,
)
from app.db import (
    get_activity as _get,
)
from app.db import (
    get_profile,
    list_activities as _list,
    upsert_profile,
)
from app.identity import (
    fetch_profile,
    is_valid_did,
    is_valid_handle,
    resolve_identity,
)
from app.parse import parse_file
from app.strava import process_strava_export
from app.tid import generate_tid
from app.oauth import (
    fetch_authserver_meta,
    initial_token_request,
    is_safe_url,
    pds_authed_request,
    refresh_token_request,
    resolve_pds_authserver,
    revoke_token_request,
    send_par_request,
)

log = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# OAuth configuration
OAUTH_SCOPE = "atproto repo:app.thedistance.activity?action=create repo:app.thedistance.activity?action=update repo:app.thedistance.activity?action=delete"

CLIENT_SECRET_JWK = (
    JsonWebKey.import_key(json.loads(settings.client_secret_jwk))
    if settings.client_secret_jwk
    else None
)
CLIENT_PUB_JWK = (
    json.loads(CLIENT_SECRET_JWK.as_json(is_private=False))
    if CLIENT_SECRET_JWK
    else None
)
if CLIENT_PUB_JWK:
    assert "d" not in CLIENT_PUB_JWK, "Public JWK must not contain private key material"


def compute_client_id(app_url: str) -> tuple[str, str]:
    """Compute the OAuth client_id and redirect_uri from the app URL."""
    parsed = urlparse(app_url)
    if parsed.hostname == "127.0.0.1":
        redirect_uri = f"http://127.0.0.1:{parsed.port}/oauth/callback"
        client_id = "http://localhost?" + urlencode({
            "redirect_uri": redirect_uri,
            "scope": OAUTH_SCOPE,
        })
    else:
        url = app_url.rstrip("/")
        redirect_uri = f"{url}/oauth/callback"
        client_id = f"{url}/oauth-client-metadata.json"
    return client_id, redirect_uri


def row_to_dict(row):
    return dict(row) if row else None


@app.on_event("startup")
def startup():
    init_db()


# --- Activity endpoints ---

@app.get("/api/activities")
def list_activities(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sport_type: str | None = Query(default=None),
):
    conn = get_connection()
    try:
        rows = _list(conn, limit=limit, offset=offset, sport_type=sport_type)
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/activities/{did}")
def list_user_activities(
    did: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sport_type: str | None = Query(default=None),
):
    conn = get_connection()
    try:
        rows = _list(conn, limit=limit, offset=offset, sport_type=sport_type, did=did)
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/activities/{did}/{rkey}")
def get_activity_endpoint(did: str, rkey: str):
    conn = get_connection()
    try:
        row = _get(conn, did, rkey)
        if not row:
            return JSONResponse(status_code=404, content={"error": "Not found"})
        return row_to_dict(row)
    finally:
        conn.close()


# --- Backfill endpoint ---

@app.post("/api/backfill")
def backfill_endpoint(
    req: dict,
    background_tasks: BackgroundTasks,
    session: dict = Depends(require_auth),
):
    identifier = req.get("handle") or req.get("did")
    if not identifier:
        return JSONResponse(
            status_code=400, content={"error": "handle or did is required"}
        )

    with httpx.Client() as client:
        did, _, _ = resolve_identity(client, identifier)

    if did != session["did"]:
        return JSONResponse(
            status_code=403, content={"error": "You can only backfill your own account"}
        )

    background_tasks.add_task(run_backfill, identifier)
    return {"status": "started", "did": did}


# --- Identity endpoints ---

@app.get("/api/resolve/{handle}")
def resolve_handle_endpoint(handle: str):
    with httpx.Client() as client:
        try:
            did, resolved_handle, pds_url = resolve_identity(client, handle)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})

        profile = fetch_profile(client, did, pds_url)

    result = {"did": did, "handle": resolved_handle}
    if profile:
        result["displayName"] = profile["display_name"]
        result["description"] = profile["description"]
        result["avatarUrl"] = profile["avatar_url"]

    return result


# --- File parsing endpoints ---

@app.post("/api/parse")
async def parse_files(
    files: list[UploadFile],
    session: dict = Depends(require_auth),
):
    if not files:
        return JSONResponse(status_code=400, content={"error": "No files provided"})

    activities = []
    errors = []

    for f in files:
        try:
            data = await f.read()
            activity = parse_file(f.filename, data)
            activities.append(activity)
        except ValueError as e:
            errors.append({"filename": f.filename, "error": str(e)})
        except Exception as e:
            log.exception("Failed to parse %s", f.filename)
            errors.append({"filename": f.filename, "error": "Failed to parse file"})

    result = {"activities": activities}
    if errors:
        result["errors"] = errors

    return result


# --- Import endpoints ---

@app.post("/api/import/strava")
async def strava_preview(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    session: dict = Depends(require_auth),
):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return JSONResponse(status_code=400, content={"error": "Please upload a .zip file"})

    zip_data = await file.read()

    # Validate the zip before creating a job
    import zipfile
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            if not any(name.endswith("activities.csv") for name in zf.namelist()):
                return JSONResponse(
                    status_code=400,
                    content={"error": "No activities.csv found in zip"},
                )
    except zipfile.BadZipFile:
        return JSONResponse(status_code=400, content={"error": "File is not a valid zip archive"})

    job_id = generate_tid()
    did = session["did"]

    conn = get_connection()
    try:
        create_import_job(conn, job_id, did, "strava")
    finally:
        conn.close()

    background_tasks.add_task(process_strava_export_job, job_id, zip_data, did)

    return {"job_id": job_id}


def process_strava_export_job(job_id: str, zip_data: bytes, did: str):
    conn = get_connection()
    try:
        result = process_strava_export(zip_data, did=did, conn=conn)
        activities = result["activities"]
        duplicates = sum(1 for a in activities if a["duplicate"])
        set_import_job_manifest(conn, job_id, activities, len(activities), duplicates)
    except Exception:
        log.exception("Import job %s failed", job_id)
        conn.rollback()
        update_import_job_status(conn, job_id, "failed")
    finally:
        conn.close()


# --- Record management endpoints ---

def to_camel_case(s):
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


@app.post("/api/activities")
def create_activity_endpoint(
    req: dict,
    session: dict = Depends(require_auth),
):
    record = {to_camel_case(k): v for k, v in req.items()}

    required = ["sportType", "startedAt", "elapsedTime", "movingTime", "distance", "createdAt"]
    missing = [f for f in required if f not in record]
    if missing:
        return JSONResponse(
            status_code=400, content={"error": f"Missing required fields: {', '.join(missing)}"}
        )

    rkey = generate_tid()
    pds_url = session["pds_url"]
    url = f"{pds_url}/xrpc/com.atproto.repo.createRecord"
    body = {
        "repo": session["did"],
        "collection": "app.thedistance.activity",
        "rkey": rkey,
        "record": record,
    }

    try:
        with httpx.Client() as client:
            resp, dpop_pds_nonce = pds_authed_request(
                client=client, method="POST", url=url, session=session, body=body,
            )
    except httpx.TimeoutException:
        log.error("PDS request timed out for %s", session["did"])
        return JSONResponse(status_code=504, content={"error": "PDS request timed out"})
    except httpx.HTTPError as e:
        log.error("PDS request failed for %s: %s", session["did"], e)
        return JSONResponse(status_code=502, content={"error": "Failed to reach PDS"})

    conn = get_connection()
    try:
        update_oauth_session_pds_nonce(conn, session["did"], dpop_pds_nonce)
        if resp.status_code in [200, 201]:
            upsert_activity(conn, session["did"], rkey, record)
    finally:
        conn.close()

    if resp.status_code not in [200, 201]:
        log.error("PDS create error: %s", resp.text)
        return JSONResponse(status_code=resp.status_code, content={"error": "PDS create failed"})

    return {"status": "created", "rkey": rkey, "did": session["did"]}


@app.delete("/api/activities/{did}/{rkey}")
def delete_activity_endpoint(
    did: str,
    rkey: str,
    session: dict = Depends(require_auth),
):
    if did != session["did"]:
        return JSONResponse(status_code=403, content={"error": "You can only delete your own records"})

    pds_url = session["pds_url"]
    url = f"{pds_url}/xrpc/com.atproto.repo.deleteRecord"
    body = {
        "repo": session["did"],
        "collection": "app.thedistance.activity",
        "rkey": rkey,
    }

    try:
        with httpx.Client() as client:
            resp, dpop_pds_nonce = pds_authed_request(
                client=client, method="POST", url=url, session=session, body=body,
            )
    except httpx.TimeoutException:
        log.error("PDS request timed out for %s", session["did"])
        return JSONResponse(status_code=504, content={"error": "PDS request timed out"})
    except httpx.HTTPError as e:
        log.error("PDS request failed for %s: %s", session["did"], e)
        return JSONResponse(status_code=502, content={"error": "Failed to reach PDS"})

    conn = get_connection()
    try:
        update_oauth_session_pds_nonce(conn, session["did"], dpop_pds_nonce)
        if resp.status_code in [200, 201]:
            delete_activity(conn, did, rkey)
    finally:
        conn.close()

    if resp.status_code not in [200, 201]:
        log.error("PDS delete error: %s", resp.text)
        return JSONResponse(status_code=resp.status_code, content={"error": "PDS delete failed"})

    return {"status": "deleted"}


# --- OAuth endpoints ---

@app.get("/oauth-client-metadata.json")
def oauth_client_metadata():
    app_url = settings.app_url.rstrip("/")
    client_id = f"{app_url}/oauth-client-metadata.json"
    return {
        "client_id": client_id,
        "dpop_bound_access_tokens": True,
        "application_type": "web",
        "redirect_uris": [f"{app_url}/oauth/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": OAUTH_SCOPE,
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_signing_alg": "ES256",
        "jwks_uri": f"{app_url}/oauth/jwks.json",
        "client_name": "The Distance",
        "client_uri": app_url,
    }


@app.get("/oauth/jwks.json")
def oauth_jwks():
    if not CLIENT_PUB_JWK:
        return JSONResponse(status_code=503, content={"error": "OAuth not configured"})
    return {"keys": [CLIENT_PUB_JWK]}


@app.post("/oauth/login")
def oauth_login(req: dict):
    if not CLIENT_SECRET_JWK:
        return JSONResponse(status_code=503, content={"error": "OAuth not configured"})

    username = req.get("username", "").strip()
    if not username:
        return JSONResponse(status_code=400, content={"error": "username is required"})

    # Strip @ prefix if present
    if is_valid_handle(username.removeprefix("@")):
        username = username.removeprefix("@")

    with httpx.Client() as client:
        if is_valid_handle(username) or is_valid_did(username):
            login_hint = username
            try:
                did, handle, pds_url = resolve_identity(client, username)
            except ValueError as e:
                return JSONResponse(status_code=400, content={"error": str(e)})

            try:
                authserver_url = resolve_pds_authserver(client, pds_url)
            except Exception as e:
                log.error("Failed to resolve auth server for PDS %s: %s", pds_url, e)
                return JSONResponse(
                    status_code=400,
                    content={"error": "Failed to resolve authorization server"},
                )
        elif username.startswith("https://") and is_safe_url(username):
            did, handle, pds_url = None, None, None
            login_hint = None
            try:
                authserver_url = resolve_pds_authserver(client, username)
            except Exception:
                authserver_url = username.rstrip("/")
        else:
            return JSONResponse(
                status_code=400,
                content={"error": "Not a valid handle, DID, or auth server URL"},
            )

        try:
            authserver_meta = fetch_authserver_meta(client, authserver_url)
        except Exception as e:
            log.error("Failed to fetch auth server metadata: %s", e)
            return JSONResponse(
                status_code=400,
                content={"error": "Failed to fetch authorization server metadata"},
            )

        dpop_private_jwk = JsonWebKey.generate_key("EC", "P-256", is_private=True)
        client_id, redirect_uri = compute_client_id(settings.app_url)

        pkce_verifier, state, dpop_authserver_nonce, resp = send_par_request(
            client=client,
            authserver_url=authserver_url,
            authserver_meta=authserver_meta,
            login_hint=login_hint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=OAUTH_SCOPE,
            client_secret_jwk=CLIENT_SECRET_JWK,
            dpop_private_jwk=dpop_private_jwk,
        )

    if resp.status_code == 400:
        log.error("PAR HTTP 400: %s", resp.json())
    resp.raise_for_status()

    par_request_uri = resp.json()["request_uri"]

    conn = get_connection()
    try:
        save_auth_request(
            conn, state, authserver_meta["issuer"], did, handle, pds_url,
            pkce_verifier, OAUTH_SCOPE, dpop_authserver_nonce,
            dpop_private_jwk.as_json(is_private=True),
        )
    finally:
        conn.close()

    auth_url = authserver_meta["authorization_endpoint"]
    if not is_safe_url(auth_url):
        return JSONResponse(status_code=400, content={"error": "Unsafe authorization URL"})
    qparam = urlencode({"client_id": client_id, "request_uri": par_request_uri})
    return {"redirect_url": f"{auth_url}?{qparam}"}


@app.get("/oauth/callback")
def oauth_callback(request: Request):
    if not CLIENT_SECRET_JWK:
        return JSONResponse(status_code=503, content={"error": "OAuth not configured"})

    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", "")
        return JSONResponse(status_code=400, content={"error": f"{error}: {desc}"})

    state = request.query_params.get("state")
    authserver_iss = request.query_params.get("iss")
    code = request.query_params.get("code")

    if not all([state, authserver_iss, code]):
        return JSONResponse(status_code=400, content={"error": "Missing callback parameters"})

    conn = get_connection()
    try:
        auth_req = get_auth_request(conn, state)
        if not auth_req:
            return JSONResponse(status_code=400, content={"error": "OAuth request not found"})

        delete_auth_request(conn, state)

        if auth_req["authserver_iss"] != authserver_iss:
            return JSONResponse(
                status_code=400, content={"error": "Authorization server mismatch"}
            )

        client_id, redirect_uri = compute_client_id(settings.app_url)

        with httpx.Client() as client:
            tokens, dpop_authserver_nonce = initial_token_request(
                client=client,
                auth_request=auth_req,
                code=code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                client_secret_jwk=CLIENT_SECRET_JWK,
            )

            log.info("Token response scopes: %s", tokens.get("scope"))

            if auth_req["did"]:
                did = auth_req["did"]
                handle = auth_req["handle"]
                pds_url = auth_req["pds_url"]
                if tokens["sub"] != did:
                    return JSONResponse(
                        status_code=400, content={"error": "DID mismatch in token response"}
                    )
            else:
                did = tokens["sub"]
                if not is_valid_did(did):
                    return JSONResponse(
                        status_code=400, content={"error": "Invalid DID in token response"}
                    )
                did, handle, pds_url = resolve_identity(client, did)
                verified_authserver = resolve_pds_authserver(client, pds_url)
                if verified_authserver != authserver_iss:
                    return JSONResponse(
                        status_code=400, content={"error": "Authorization server mismatch"}
                    )

            profile = fetch_profile(client, did, pds_url)

        save_oauth_session(
            conn, did, handle, pds_url, authserver_iss,
            tokens["access_token"], tokens["refresh_token"],
            dpop_authserver_nonce, auth_req["dpop_private_jwk"],
        )

        if profile:
            upsert_profile(
                conn, did, handle,
                profile["display_name"],
                profile["description"],
                profile["avatar_url"],
            )
        else:
            upsert_profile(conn, did, handle, None, None, None)
    finally:
        conn.close()

    request.session["user_did"] = did
    request.session["user_handle"] = handle

    return RedirectResponse(url=f"{settings.frontend_url}/profile/{handle}", status_code=302)


@app.post("/oauth/refresh")
def oauth_refresh(request: Request, session: dict = Depends(require_auth)):
    client_id, _ = compute_client_id(settings.app_url)

    with httpx.Client() as client:
        tokens, dpop_authserver_nonce = refresh_token_request(
            client=client,
            session=session,
            client_id=client_id,
            client_secret_jwk=CLIENT_SECRET_JWK,
        )

    conn = get_connection()
    try:
        update_oauth_session_tokens(
            conn, session["did"],
            tokens["access_token"], tokens["refresh_token"],
            dpop_authserver_nonce,
        )
    finally:
        conn.close()

    return {"status": "refreshed"}


@app.post("/oauth/logout")
def oauth_logout(request: Request, session: dict = Depends(require_auth)):
    client_id, _ = compute_client_id(settings.app_url)

    with httpx.Client() as client:
        try:
            revoke_token_request(
                client=client,
                session=session,
                client_id=client_id,
                client_secret_jwk=CLIENT_SECRET_JWK,
            )
        except Exception as e:
            log.error("Error during token revocation: %s", e)

    conn = get_connection()
    try:
        delete_oauth_session(conn, session["did"])
    finally:
        conn.close()

    request.session.clear()
    return {"status": "logged out"}


@app.get("/oauth/me")
def oauth_me(session: dict = Depends(require_auth)):
    did = session["did"]
    handle = session["handle"]

    conn = get_connection()
    try:
        profile = get_profile(conn, did)
    finally:
        conn.close()

    result = {"did": did, "handle": handle}
    if profile:
        result["displayName"] = profile["display_name"]
        result["description"] = profile["description"]
        result["avatarUrl"] = profile["avatar_url"]

    return result
