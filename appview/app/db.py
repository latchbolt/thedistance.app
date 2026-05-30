import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


def get_connection():
    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activities (
                did TEXT NOT NULL,
                rkey TEXT NOT NULL,
                sport_type TEXT NOT NULL,
                title TEXT,
                description TEXT,
                started_at TIMESTAMPTZ NOT NULL,
                elapsed_time INTEGER NOT NULL,
                moving_time INTEGER NOT NULL,
                distance TEXT NOT NULL,
                elevation_gain TEXT,
                avg_speed TEXT,
                max_speed TEXT,
                avg_heart_rate INTEGER,
                max_heart_rate INTEGER,
                avg_cadence INTEGER,
                max_cadence INTEGER,
                avg_power INTEGER,
                max_power INTEGER,
                calories INTEGER,
                polyline TEXT,
                device TEXT,
                source TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (did, rkey)
            )
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_activities_started_at
                ON activities (started_at DESC)
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_activities_did
                ON activities (did)
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cursor (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cursor_value BIGINT NOT NULL
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_auth_requests (
                state TEXT PRIMARY KEY,
                authserver_iss TEXT NOT NULL,
                did TEXT,
                handle TEXT,
                pds_url TEXT,
                pkce_verifier TEXT NOT NULL,
                scope TEXT NOT NULL,
                dpop_authserver_nonce TEXT NOT NULL,
                dpop_private_jwk TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                did TEXT PRIMARY KEY,
                handle TEXT NOT NULL,
                display_name TEXT,
                description TEXT,
                avatar_url TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_sessions (
                did TEXT PRIMARY KEY,
                handle TEXT NOT NULL,
                pds_url TEXT NOT NULL,
                authserver_iss TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                dpop_authserver_nonce TEXT NOT NULL,
                dpop_pds_nonce TEXT,
                dpop_private_jwk TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        )


def upsert_activity(conn, did, rkey, record):
    conn.execute(
        """
        INSERT INTO activities (
            did, rkey, sport_type, title, description, started_at,
            elapsed_time, moving_time, distance, elevation_gain,
            avg_speed, max_speed, avg_heart_rate, max_heart_rate,
            avg_cadence, max_cadence, avg_power, max_power,
            calories, polyline, device, source, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (did, rkey) DO UPDATE SET
            sport_type = EXCLUDED.sport_type,
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            started_at = EXCLUDED.started_at,
            elapsed_time = EXCLUDED.elapsed_time,
            moving_time = EXCLUDED.moving_time,
            distance = EXCLUDED.distance,
            elevation_gain = EXCLUDED.elevation_gain,
            avg_speed = EXCLUDED.avg_speed,
            max_speed = EXCLUDED.max_speed,
            avg_heart_rate = EXCLUDED.avg_heart_rate,
            max_heart_rate = EXCLUDED.max_heart_rate,
            avg_cadence = EXCLUDED.avg_cadence,
            max_cadence = EXCLUDED.max_cadence,
            avg_power = EXCLUDED.avg_power,
            max_power = EXCLUDED.max_power,
            calories = EXCLUDED.calories,
            polyline = EXCLUDED.polyline,
            device = EXCLUDED.device,
            source = EXCLUDED.source,
            created_at = EXCLUDED.created_at,
            indexed_at = NOW()
    """,
        (
            did,
            rkey,
            record["sportType"],
            record.get("title"),
            record.get("description"),
            record["startedAt"],
            record["elapsedTime"],
            record["movingTime"],
            record["distance"],
            record.get("elevationGain"),
            record.get("avgSpeed"),
            record.get("maxSpeed"),
            record.get("avgHeartRate"),
            record.get("maxHeartRate"),
            record.get("avgCadence"),
            record.get("maxCadence"),
            record.get("avgPower"),
            record.get("maxPower"),
            record.get("calories"),
            record.get("polyline"),
            record.get("device"),
            record.get("source"),
            record["createdAt"],
        ),
    )
    conn.commit()


def delete_activity(conn, did, rkey):
    conn.execute("DELETE FROM activities WHERE did = %s AND rkey = %s", (did, rkey))
    conn.commit()


def get_cursor(conn):
    row = conn.execute("SELECT cursor_value FROM cursor WHERE id = 1").fetchone()
    return row["cursor_value"] if row else None


def set_cursor(conn, cursor_value):
    conn.execute(
        """
        INSERT INTO cursor (id, cursor_value) VALUES (1, %s)
        ON CONFLICT (id) DO UPDATE SET cursor_value = EXCLUDED.cursor_value
    """,
        (cursor_value,),
    )
    conn.commit()


# DIDs hidden from the global feed. Their profile pages still work.
HIDDEN_DIDS = [
    "did:plc:te2b5jsybsv5n52yah7ske2e",  # tylergaw-tester1.bsky.social
]


def list_activities(conn, limit=50, offset=0, sport_type=None, did=None):
    query = """
        SELECT a.*,
            p.handle AS owner_handle,
            p.display_name AS owner_display_name,
            p.avatar_url AS owner_avatar_url
        FROM activities a
        LEFT JOIN profiles p ON a.did = p.did
    """
    params = []
    conditions = []

    if did:
        conditions.append("a.did = %s")
        params.append(did)
    elif HIDDEN_DIDS:
        placeholders = ", ".join(["%s"] * len(HIDDEN_DIDS))
        conditions.append(f"a.did NOT IN ({placeholders})")
        params.extend(HIDDEN_DIDS)
    if sport_type:
        conditions.append("a.sport_type = %s")
        params.append(sport_type)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY a.started_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    return conn.execute(query, params).fetchall()


# OAuth auth request helpers


def save_auth_request(
    conn,
    state,
    authserver_iss,
    did,
    handle,
    pds_url,
    pkce_verifier,
    scope,
    dpop_authserver_nonce,
    dpop_private_jwk,
):
    conn.execute(
        """
        INSERT INTO oauth_auth_requests (
            state, authserver_iss, did, handle, pds_url,
            pkce_verifier, scope, dpop_authserver_nonce, dpop_private_jwk
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """,
        (
            state,
            authserver_iss,
            did,
            handle,
            pds_url,
            pkce_verifier,
            scope,
            dpop_authserver_nonce,
            dpop_private_jwk,
        ),
    )
    conn.commit()


def get_auth_request(conn, state):
    return conn.execute(
        "SELECT * FROM oauth_auth_requests WHERE state = %s", (state,)
    ).fetchone()


def delete_auth_request(conn, state):
    conn.execute("DELETE FROM oauth_auth_requests WHERE state = %s", (state,))
    conn.commit()


# OAuth session helpers


def save_oauth_session(
    conn,
    did,
    handle,
    pds_url,
    authserver_iss,
    access_token,
    refresh_token,
    dpop_authserver_nonce,
    dpop_private_jwk,
):
    conn.execute(
        """
        INSERT INTO oauth_sessions (
            did, handle, pds_url, authserver_iss,
            access_token, refresh_token,
            dpop_authserver_nonce, dpop_private_jwk
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (did) DO UPDATE SET
            handle = EXCLUDED.handle,
            pds_url = EXCLUDED.pds_url,
            authserver_iss = EXCLUDED.authserver_iss,
            access_token = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            dpop_authserver_nonce = EXCLUDED.dpop_authserver_nonce,
            dpop_private_jwk = EXCLUDED.dpop_private_jwk
    """,
        (
            did,
            handle,
            pds_url,
            authserver_iss,
            access_token,
            refresh_token,
            dpop_authserver_nonce,
            dpop_private_jwk,
        ),
    )
    conn.commit()


def get_oauth_session(conn, did):
    return conn.execute(
        "SELECT * FROM oauth_sessions WHERE did = %s", (did,)
    ).fetchone()


def update_oauth_session_tokens(
    conn, did, access_token, refresh_token, dpop_authserver_nonce
):
    conn.execute(
        """
        UPDATE oauth_sessions
        SET access_token = %s, refresh_token = %s, dpop_authserver_nonce = %s
        WHERE did = %s
    """,
        (access_token, refresh_token, dpop_authserver_nonce, did),
    )
    conn.commit()


def update_oauth_session_pds_nonce(conn, did, dpop_pds_nonce):
    conn.execute(
        "UPDATE oauth_sessions SET dpop_pds_nonce = %s WHERE did = %s",
        (dpop_pds_nonce, did),
    )
    conn.commit()


def delete_oauth_session(conn, did):
    conn.execute("DELETE FROM oauth_sessions WHERE did = %s", (did,))
    conn.commit()


def upsert_profile(conn, did, handle, display_name, description, avatar_url):
    conn.execute(
        """
        INSERT INTO profiles (did, handle, display_name, description, avatar_url, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (did) DO UPDATE SET
            handle = EXCLUDED.handle,
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            avatar_url = EXCLUDED.avatar_url,
            updated_at = NOW()
    """,
        (did, handle, display_name, description, avatar_url),
    )
    conn.commit()


def get_profile(conn, did):
    return conn.execute(
        "SELECT * FROM profiles WHERE did = %s", (did,)
    ).fetchone()


def has_profile(conn, did):
    row = conn.execute(
        "SELECT 1 FROM profiles WHERE did = %s", (did,)
    ).fetchone()
    return row is not None


def get_activity(conn, did, rkey):
    return conn.execute(
        """
        SELECT a.*,
            p.handle AS owner_handle,
            p.display_name AS owner_display_name,
            p.avatar_url AS owner_avatar_url
        FROM activities a
        LEFT JOIN profiles p ON a.did = p.did
        WHERE a.did = %s AND a.rkey = %s
        """,
        (did, rkey),
    ).fetchone()
