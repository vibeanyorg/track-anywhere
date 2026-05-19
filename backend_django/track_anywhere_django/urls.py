from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .views import (
    AccountViewSet,
    AuditEventViewSet,
    AuthIdentityViewSet,
    BookMemberViewSet,
    CategoryViewSet,
    DjangoUserViewSet,
    GroupViewSet,
    LedgerBookViewSet,
    LedgerUserViewSet,
    RecurringItemViewSet,
    TransactionViewSet,
)


router = DefaultRouter()
router.register("django-users", DjangoUserViewSet, basename="django-user")
router.register("groups", GroupViewSet, basename="group")
router.register("books", LedgerBookViewSet, basename="book")
router.register("book-members", BookMemberViewSet, basename="book-member")
router.register("accounts", AccountViewSet, basename="account")
router.register("ledger-users", LedgerUserViewSet, basename="ledger-user")
router.register("auth-identities", AuthIdentityViewSet, basename="auth-identity")
router.register("categories", CategoryViewSet, basename="category")
router.register("transactions", TransactionViewSet, basename="transaction")
router.register("recurring-items", RecurringItemViewSet, basename="recurring-item")
router.register("audit-events", AuditEventViewSet, basename="audit-event")
