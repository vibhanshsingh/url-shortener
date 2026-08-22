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

import logging
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.middleware.cors import CORSMiddleware

from app.api.routes.redirect import router as redirect_router
from app.api.routes.shorten import router as shorten_router
from app.api.routes.stats import router as stats_router
from app.cache.redis_client import redis_client
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.events.admin import ensure_topics_exist
from app.events.producer import kafka_producer
from app.middleware.correlation_id import CorrelationIdMiddleware
from app.middleware.metrics import MetricsMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

setup_logging()
logger = logging.getLogger(__name__)


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
# Order matters here: in Starlette, the LAST middleware added ends up
# OUTERMOST, meaning it's the first to see an incoming request. We add
# RateLimitMiddleware first and CorrelationIdMiddleware second, so the
# correlation ID gets set before rate limiting (or anything else) runs
# — otherwise a rate-limited request's logs would have no tracking
# number attached to them, which is exactly the case where you'd want
# one most. MetricsMiddleware times and counts every request that reaches
# the application. CORS is added last so it handles preflight requests
# before they reach the rate limiter and adds headers to all responses.
app.add_middleware(RateLimitMiddleware, redis_client=redis_client)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allowed_origins.split(",")],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(shorten_router)
app.include_router(stats_router)


@app.exception_handler(OperationalError)
@app.exception_handler(DBAPIError)
async def database_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    MILESTONE 13 — plain-language version: if Postgres is down or
    unreachable and something DOES need to hit it (the write path
    always does; the read path only on a cache miss), this catches
    that failure everywhere at once, instead of needing a try/except
    around every single database call in every route. The person
    calling our API gets a clean, honest "service unavailable, try
    again" instead of a raw Python stack trace — which would be both
    confusing AND a security concern (stack traces can leak internal
    details like file paths, query structure, or connection info).

    503, not 500: 503 specifically communicates "this is temporary,
    the dependency is down" — a well-behaved client can reasonably
    retry a 503. A generic 500 gives no such signal.
    """
    logger.error("Database unavailable: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Service temporarily unavailable. Please try again shortly."},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    The safety net of last resort. Anything that reaches here is a bug
    or a failure mode we haven't specifically named yet — the person
    calling the API still gets a clean, generic 500 instead of a raw
    traceback; the FULL detail goes to our own logs (with the
    correlation ID attached, per Milestone 11), which is exactly where
    an on-call engineer would look, not where a random caller should
    ever see it.
    """
    logger.exception("Unhandled exception processing request: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred."},
    )


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "alive"}


@app.get("/metrics")
async def metrics() -> Response:
    # generate_latest() reads every Counter/Histogram defined in
    # app/core/metrics.py and renders them in Prometheus's plain-text
    # exposition format — this is literally what Prometheus scrapes
    # every 15 seconds (see monitoring/prometheus.yml).
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
