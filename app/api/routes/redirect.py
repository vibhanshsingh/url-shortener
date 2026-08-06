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

MILESTONE 8: click events are now published via BackgroundTasks. This
is the concrete mechanism behind "the redirect never waits on
analytics" — FastAPI runs background tasks AFTER the response has
already been sent to the client, so publishing to Kafka adds zero
latency to what the user experiences, even if Kafka itself is slow.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.api.dependencies import get_url_service
from app.events.producer import KafkaEventProducer, get_kafka_producer
from app.events.schemas import ClickEvent
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
    request: Request,
    background_tasks: BackgroundTasks,
    service: URLService = Depends(get_url_service),
    producer: KafkaEventProducer = Depends(get_kafka_producer),
) -> RedirectResponse:
    try:
        target = await service.resolve_for_redirect(short_code)
    except URLNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except URLGoneError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc

    # Built here, in the route, because this is the one place with
    # direct access to the HTTP request's headers/client info — the
    # service layer deliberately never sees a Request object (it has
    # no HTTP concerns, per Milestone 1's layering).
    event = ClickEvent.now(
        short_code=short_code,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        referrer=request.headers.get("referer"),  # yes, "referer" — the header name's historical misspelling
    )
    # add_task, not `await producer.publish_click_event(event)` directly:
    # this schedules the publish to run AFTER the response below has
    # already been sent to the client.
    background_tasks.add_task(producer.publish_click_event, event)

    # DELIBERATE CHOICE, WORTH RECONSIDERING: 301 means browsers may
    # cache this redirect and stop contacting our server on repeat
    # clicks from the same client — which directly undercounts the
    # click analytics this same spec requires. A 302 would guarantee
    # every click reaches us, at the cost of losing the browser-level
    # caching benefit. Implemented as 301 to match spec; flagged here
    # because this is exactly the kind of trade-off worth raising in a
    # design review rather than silently accepting.
    return RedirectResponse(
        url=target.long_url,
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )
