import os
import sys

import uvicorn


def start():
    reload = os.getenv("UVICORN_RELOAD", "true").lower() == "true"
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=reload,
    )


def subscribe():
    import asyncio

    from app.subscriber import subscribe as _subscribe

    asyncio.run(_subscribe())


def backfill():
    import logging

    from app.backfill import backfill as _backfill

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) < 2:
        print("Usage: uv run backfill <handle-or-did>")
        sys.exit(1)

    identifier = sys.argv[1]
    result = _backfill(identifier)
    print(f"Backfilled {result['records']} records for {result['did']}, {result['deleted']} orphaned deleted")


def generate_jwk():
    from authlib.jose import JsonWebKey

    key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
    print(key.as_json(is_private=True))
