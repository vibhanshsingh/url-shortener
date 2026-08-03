"""
IMPORTANT ROUTE ORDERING NOTE (a real Starlette/FastAPI gotcha worth
knowing for interviews): unlike some frameworks, Starlette matches
routes in the ORDER THEY WERE REGISTERED, not by "most specific path
wins." Because `/{short_code}` matches ANY single path segment, if it
were registered before more specific single-segment routes, it would
silently swallow requests meant for them.

We're safe here because:
  1. FastAPI's built-in /docs, /redoc, /openapi.json routes are
     registered inside FastAPI() itself, before we ever call
     include_router() — so they're already first in line.
  2. Our /health/live and /health/ready routes are two path segments
     deep, and `/{short_code}` only matches one segment, so there's no
     overlap regardless of registration order.
  3. This redirect_router is included LAST in main.py, as a deliberate
     safety margin — any future single-segment route we add will only
     be safe if it's registered before this one.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from app.api.dependencies import get_url_service
from app.services.url_service import URLGoneError, URLNotFoundError, URLService

router = APIRouter(tags=["redirect"])


@router.get(
    "/{short_code}",
    summary="Redirect to the long URL for a short code",
    responses={
        status.HTTP_301_MOVED_PERMANENTLY: {"description": "Redirects to the long URL"},
        status.HTTP_404_NOT_FOUND: {"description": "No URL exists for this short code"},
        status.HTTP_410_GONE: {"description": "URL existed but was deactivated or expired"},
    },
)
async def redirect_to_long_url(
    short_code: str,
    service: URLService = Depends(get_url_service),
) -> RedirectResponse:
    try:
        url_row = await service.resolve_for_redirect(short_code)
    except URLNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except URLGoneError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc

    # DELIBERATE CHOICE, WORTH RECONSIDERING: 301 means browsers may
    # cache this redirect and stop contacting our server on repeat
    # clicks from the same client — which directly undercounts the
    # click analytics this same spec requires. A 302 would guarantee
    # every click reaches us, at the cost of losing the browser-level
    # caching benefit. Implemented as 301 to match spec; flagged here
    # because this is exactly the kind of trade-off worth raising in a
    # design review rather than silently accepting.
    return RedirectResponse(
        url=url_row.long_url,
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )
