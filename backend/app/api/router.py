"""Top-level API router. Feature routers are included here as they are built."""

from fastapi import APIRouter

from app.api.routes import analytics, credentials, health, lifecycle, master_data, verifications

api_router = APIRouter()
api_router.include_router(health.router)  # /api/health (unversioned)

# Business endpoints: /api/v1 (ADR-016). T04: credential lifecycle, eligibility, audit, rule activation.
v1_router = APIRouter(prefix="/v1")
v1_router.include_router(credentials.router)
v1_router.include_router(verifications.router)
v1_router.include_router(lifecycle.router)
v1_router.include_router(master_data.router)  # T05: create/retrieve master data
v1_router.include_router(analytics.router)  # T07: metrics, anomaly run and evaluation
api_router.include_router(v1_router)
