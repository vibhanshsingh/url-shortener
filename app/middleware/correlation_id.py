"""
Plain-language version: for every request, this does three things.

  1. Get (or make) a tracking number for this request.
  2. Stick it on the "sticky note" (correlation_id_var from
     logging_config.py) so every log line written while handling this
     request picks it up automatically.
  3. Log one line when the request starts, one when it finishes — a
     simple built-in access log, both carrying the same tracking
     number, so you can see "this request started here, finished
     there" in the logs.

If the CALLER already sent an X-Request-ID header (common when one
service calls another, and wants the whole chain traceable under one
ID), we reuse it instead of making a new one — that's what lets a
tracking number follow a request across multiple services, not just
within this one.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging_config import correlation_id_var

logger = logging.getLogger("access")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        correlation_id_var.set(correlation_id)

        start_time = time.monotonic()
        logger.info("Request started: %s %s", request.method, request.url.path)

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        logger.info(
            "Request finished: %s %s -> %d (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        # Sent back to the caller too — so if a user reports a
        # problem, they (or the frontend team) can hand you this exact
        # ID and you go straight to the right log lines.
        response.headers["X-Request-ID"] = correlation_id
        return response
