"""
Milestone 2 scope: prove FastAPI, Postgres, and Redis are wired together
correctly inside Docker Compose. No business logic yet — that starts
in Milestone 3 (schema) and Milestone 5 (create endpoint).

Two endpoints, deliberately different:

- /health/live  -> "is the process running at all?" Never checks
                    dependencies. Used by an orchestrator to decide
                    whether to KILL and restart the container.
- /health/ready  -> "can this instance actually serve traffic?" Checks
                    Postgres and Redis. Used by an orchestrator/load
                    balancer to decide whether to ROUTE traffic here.

Conflating these two is a common mistake: if /health checked the DB and
the DB had a transient blip, an orchestrator might kill and restart
*every* API instance simultaneously — the exact opposite of what you
want during a database hiccup.
"""

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from app.core.config import settings

app = FastAPI(title="URL Shortener", version="0.1.0")


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    checks = {"postgres": False, "redis": False}

    try:
        conn = await asyncpg.connect(settings.database_url, timeout=2)
        await conn.close()
        checks["postgres"] = True
    except Exception:
        pass  # noqa: S110 — deliberately swallow; we only care about the boolean

    try:
        client = redis.Redis(host=settings.redis_host, port=settings.redis_port, socket_timeout=2)
        await client.ping()
        await client.aclose()
        checks["redis"] = True
    except Exception:
        pass  # noqa: S110

    all_healthy = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if all_healthy else "not_ready", "checks": checks},
    )
