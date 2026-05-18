from __future__ import annotations

from fastapi import APIRouter

from .api_routers import catalog, credentials, finance, ledger, recurring, system


router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(catalog.router)
router.include_router(ledger.router)
router.include_router(finance.router)
router.include_router(recurring.router)
router.include_router(credentials.router)


__all__ = ["router"]
