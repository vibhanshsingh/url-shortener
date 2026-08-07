"""
Two path segments ("/stats" + "/{short_code}"), so this never collides
with the redirect route's single-segment "/{short_code}" pattern —
they can't match the same request no matter what order they're
registered in. We still register this router before redirect_router in
main.py anyway, purely as a good habit, not because it's required here.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_stats_service
from app.schemas.stats import StatsResponse
from app.services.stats_service import StatsService
from app.services.url_service import URLNotFoundError

router = APIRouter(tags=["stats"])


@router.get(
    "/stats/{short_code}",
    response_model=StatsResponse,
    summary="Get click stats for a short code",
)
async def get_stats(
    short_code: str,
    service: StatsService = Depends(get_stats_service),
) -> StatsResponse:
    try:
        return await service.get_stats(short_code)
    except URLNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
