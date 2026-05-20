from __future__ import annotations

from fastapi import APIRouter

from .api_routers import auth, backoffice, books, catalog, credentials, finance, ledger, oauth, recurring, system


router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(auth.router)
router.include_router(oauth.router)
router.include_router(backoffice.router)
router.include_router(books.router)
router.include_router(catalog.router)
router.include_router(ledger.router)
router.include_router(finance.router)
router.include_router(recurring.router)
router.include_router(credentials.router)


__all__ = ["router"]
