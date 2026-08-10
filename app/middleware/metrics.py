"""
THE CARDINALITY GOTCHA, made concrete: after call_next() returns,
Starlette has already resolved which route handled this request, and
stashed it at request.scope["route"]. That route object has a `.path`
attribute holding the ROUTE PATTERN — e.g. "/{short_code}" — not the
actual short code someone requested.

We deliberately use `.path` (the pattern) as our Prometheus label, not
`request.url.path` (the real, unique-per-URL value). Using the real
path would mean every distinct short code ever created becomes its own
permanent time series in Prometheus — a genuine, well-documented way to
quietly take down a Prometheus server at scale. One metric label per
ROUTE, not per REQUEST, full stop.

For a request that matched no route at all (a 404 to a random path,
which can also just be an attacker probing for weaknesses), there's no
`scope["route"]` to read — we fall back to a fixed "unmatched" label
so even a flood of garbage requests to random paths can't create
unbounded label values either.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.metrics import http_request_duration_seconds, http_requests_total


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.monotonic()

        response = await call_next(request)

        duration = time.monotonic() - start_time

        route = request.scope.get("route")
        route_path = route.path if route is not None else "unmatched"

        http_requests_total.labels(
            method=request.method,
            route=route_path,
            status_code=str(response.status_code),
        ).inc()

        http_request_duration_seconds.labels(
            method=request.method, route=route_path
        ).observe(duration)

        return response
