"""
Milestone 8 adds a `lifespan` context manager — FastAPI's mechanism for
running startup/shutdown code around the app's lifetime. We need this
now because, unlike Postgres and Redis (where each request just opens
what it needs via connection pooling), the Kafka producer maintains a
persistent connection that must be explicitly started once and stopped
once, not per-request. `ensure_topics_exist()` also runs here — so the
topics are guaranteed to exist before the app starts accepting traffic.

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

from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from app.api.routes.redirect import router as redirect_router
from app.api.routes.shorten import router as shorten_router
from app.api.routes.stats import router as stats_router
from app.core.config import settings
from app.events.admin import ensure_topics_exist
from app.events.producer import kafka_producer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup, runs once before the app accepts any traffic.
    await ensure_topics_exist()
    await kafka_producer.start()
    yield
    # Shutdown, runs once as the app is stopping — releases the Kafka
    # connection cleanly rather than letting it die mid-flight.
    await kafka_producer.stop()


app = FastAPI(title="URL Shortener", version="0.1.0", lifespan=lifespan)
app.include_router(shorten_router)
app.include_router(stats_router)


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    checks = {"postgres": False, "redis": False, "kafka": False}

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

    try:
        # Checks the app's already-running producer connection rather
        # than opening a new one per health check — cheaper, and it
        # reflects the actual connection the app depends on.
        checks["kafka"] = kafka_producer.is_connected()
    except Exception:
        pass  # noqa: S110

    all_healthy = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if all_healthy else "not_ready", "checks": checks},
    )


# Registered LAST, deliberately: "/{short_code}" matches any single
# path segment, so anything registered after this point risks being
# silently shadowed by it. See the docstring in
# app/api/routes/redirect.py for the full explanation of Starlette's
# registration-order route matching.
app.include_router(redirect_router)
