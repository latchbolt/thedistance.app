# [The Distance](https://thedistance.app)

Decentralized physical activity tracker on the [AT Protocol](https://atproto.com).

> [!WARNING]
>
> 1. This is very work-in-progress. Functionality is limited and things will be broken
> 2. **ACTIVITY DATA IS PUBLIC**. This saves activities with location data to a PDS which is accessible to the world. There are ongoing conversations about being able to have private data on The Atmosphere, but it does not exist yet.

## Technology overview

This is a monorepo, it's made up of: A Jetstream subscriber, a REST API, and the website.

All three deployed on Render. See `render.yaml` for details.

- `appview`: Jetstream subscriber and API server, deployed as a background worker and web service, respectively
- `web`: Frontend built with 11ty (with WebC)
- `lexicons`: AT Protocol lexicon schemas. [On Lexicon Garden](https://lexicon.garden/identity/did:plc:x52h4ttzfk5rxxdmzinoevgo)

## Local development

### System requirements

- [Python 3.12+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/) — Python package manager
- [PostgreSQL](https://www.postgresql.org/) — running locally, no Docker

You will need a local Postgres database, named `thedistance`, created and running. Set the connection string in your `.env` file (see AppView setup below).

## AppView

The appview is made up of two processes that run simultaneously: a **subscriber** and an **API server**. The subscriber connects to Jetstream and listens for records on the network. When it sees one created, updated, or deleted, it writes the change to Postgres. The API server reads from that same database and serves the indexed data over HTTP. Both processes need to be running at the same time.

### Setup

```
cd appview
uv sync
```

Copy the example env file and fill in your Postgres connection string:

```
cp .env.example .env
```

Generate a session secret key and add it to `.env` as `SESSION_SECRET_KEY`:

```
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate the OAuth client signing key and add it to `.env` as `CLIENT_SECRET_JWK`:

```
uv run generate-jwk
```

### Run the API server

```
uv run start
```

Serves activity data on `http://127.0.0.1:8000`.

### Run the subscriber

```
uv run subscribe
```

### Backfill

The subscriber only indexes records as they are created or updated in real time. If a user already has `app.thedistance.activity` records on their PDS from before the subscriber was running, those records will not be in the database. The backfill command fetches all existing records from a user's PDS and indexes them. It also deletes any local records that no longer exist on the PDS, keeping the database in sync with the source of truth. It also fetches and stores the user's profile if one is not already indexed.

```
cd appview
uv run backfill <handle-or-did>
```

There is also an API endpoint `POST /api/backfill` that accepts `{"handle": "..."}` or `{"did": "..."}`, restricted to the authenticated user's own account.

### Tests

```
cd appview
uv run pytest
```

Test fixture files (FIT, etc.) live in `tests/fixtures/`.

## Frontend

The frontend uses [Eleventy](https://www.11ty.dev/) with [WebC](https://www.11ty.dev/docs/languages/webc/) for templating. Source files live in `web/src/` and build to `web/_site/`.

```
cd web
pnpm i && pnpm start
```

Access at `http://127.0.0.1:8001`. **Note**: You cannot use `localhost`.
