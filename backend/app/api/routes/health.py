from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.database.session import DatabaseStatus, get_database_status
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(response: Response) -> HealthResponse:
    """Liveness/readiness check that reports the real database state."""
    db_status = get_database_status()
    healthy = db_status != DatabaseStatus.UNAVAILABLE
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="healthy" if healthy else "unhealthy",
        application=get_settings().app_name,
        database=db_status,
    )
