"""v1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1 import calculations, components, health, mixtures

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(components.router, prefix="/components", tags=["components"])
api_router.include_router(calculations.router, prefix="/calculations", tags=["calculations"])
api_router.include_router(mixtures.router, prefix="/mixtures", tags=["mixtures"])
