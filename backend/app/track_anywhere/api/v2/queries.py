"""Compatibility facade for the split V2 query route modules."""

from __future__ import annotations

from .query_routes import (
    BOOK_READ_SCOPE,
    AccountListResponse,
    AccountResponse,
    AssetListResponse,
    AssetResponse,
    BalanceItemResponse,
    BalanceSnapshotResponse,
    BookListResponse,
    BookReadAuthorizer,
    BookResponse,
    CategoryListResponse,
    CategoryResponse,
    CreditCardRelationResponse as CreditCardRelationResponse,
    JournalItemResponse,
    JournalPageResponse,
    JournalPostingResponse,
    ReportingLineResponse,
    ReportingLinesResponse,
    authorize_book_read,
    create_query_router,
)


__all__ = [
    "BOOK_READ_SCOPE",
    "AccountListResponse",
    "AccountResponse",
    "AssetListResponse",
    "AssetResponse",
    "BalanceItemResponse",
    "BalanceSnapshotResponse",
    "BookListResponse",
    "BookResponse",
    "BookReadAuthorizer",
    "CategoryListResponse",
    "CategoryResponse",
    "JournalItemResponse",
    "JournalPageResponse",
    "JournalPostingResponse",
    "ReportingLineResponse",
    "ReportingLinesResponse",
    "authorize_book_read",
    "create_query_router",
]
