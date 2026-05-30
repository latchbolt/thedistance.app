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
);

CREATE INDEX IF NOT EXISTS idx_activities_started_at
    ON activities (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_activities_did
    ON activities (did);

CREATE TABLE IF NOT EXISTS cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cursor_value BIGINT NOT NULL
);

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
);

CREATE TABLE IF NOT EXISTS profiles (
    did TEXT PRIMARY KEY,
    handle TEXT NOT NULL,
    display_name TEXT,
    description TEXT,
    avatar_url TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id TEXT PRIMARY KEY,
    did TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'preview',
    total INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]',
    manifest JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_did
    ON import_jobs (did);
