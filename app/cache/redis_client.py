"""
Same reasoning as app/core/database.py's engine: one shared client with
its own internal connection pool, created once at import time, reused
across every request — not a new connection per request. redis-py's
async client already pools connections internally, so we just need to
avoid accidentally creating multiple client instances.
"""

import redis.asyncio as redis

from app.core.config import settings

redis_client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    decode_responses=True,  # get back str, not bytes — one less thing every caller has to handle
    socket_timeout=2,
    socket_connect_timeout=2,
    max_connections=20,
)


def get_redis_client() -> redis.Redis:
    """
    FastAPI dependency. Unlike get_db (which yields a per-request
    session that must be closed), this just hands back the shared
    client — Redis connections are cheap and pooled internally, so
    there's no per-request lifecycle to manage here.
    """
    return redis_client
