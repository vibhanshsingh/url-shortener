"""
Notice how thin this route is. It does exactly three things: extract
the caller's IP, call the service, and translate the result into an
HTTP response. All the actual decisions (idempotency, validation,
encoding) already happened in the service layer — that's the payoff of
the layering we set up in Milestone 1.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import get_url_service
from app.core.config import settings
from app.schemas.url import ShortenRequest, ShortenResponse
from app.services.url_service import SelfReferentialURLError, URLService

router = APIRouter(tags=["shorten"])


@router.post(
    "/shorten",
    response_model=ShortenResponse,
    summary="Create a short URL",
)
async def shorten_url(
    payload: ShortenRequest,
    request: Request,
    response: Response,
    service: URLService = Depends(get_url_service),
) -> ShortenResponse:
    try:
        # request.client can be None in some test/proxy setups —
        # handled explicitly rather than letting an AttributeError
        # surface as an opaque 500.
        caller_ip = request.client.host if request.client else None

        url_row, already_existed = await service.create_short_url(
            long_url=str(payload.url),
            created_by_ip=caller_ip,
        )
    except SelfReferentialURLError as exc:
        # 400, not 422: the request was syntactically valid (Pydantic
        # already confirmed it's a real URL) — this is a semantic
        # business-rule rejection, which is what 400 Bad Request means
        # in REST convention, distinct from 422's "failed schema
        # validation."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # 200 for "this already existed, here's the same code as before"
    # vs 201 for "this is genuinely new" — both are correct REST usage;
    # returning 201 unconditionally would be misleading to a client
    # that's watching for "was this actually just created."
    response.status_code = status.HTTP_200_OK if already_existed else status.HTTP_201_CREATED

    return ShortenResponse(
        short_code=url_row.short_code,
        short_url=f"{settings.base_url}/{url_row.short_code}",
        long_url=url_row.long_url,
        created_at=url_row.created_at,
        already_existed=already_existed,
    )
