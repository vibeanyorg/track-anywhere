from __future__ import annotations

from fastapi import APIRouter

from .api_routers import auth, auth_device_pages, auth_machine_pages, auth_pages, backoffice, books, catalog, credentials, finance, ledger, oauth, payment_profiles, recurring, system


router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(auth_pages.router)
router.include_router(auth_device_pages.router)
router.include_router(auth_machine_pages.router)
router.include_router(auth.router)
router.include_router(oauth.router)
router.include_router(backoffice.router)
router.include_router(books.router)
router.include_router(catalog.router)
router.include_router(ledger.router)
router.include_router(payment_profiles.router)
router.include_router(finance.router)
router.include_router(recurring.router)
router.include_router(credentials.router)


__all__ = ["router"]
