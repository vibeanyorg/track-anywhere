from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from guardian.shortcuts import get_objects_for_user
from rest_framework.permissions import DjangoModelPermissions, IsAdminUser, IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import Account, AuditEvent, AuthIdentity, BookMember, Category, LedgerBook, RecurringItem, Transaction, User
from .serializers import (
    AccountSerializer,
    AuditEventSerializer,
    AuthIdentitySerializer,
    BookMemberSerializer,
    CategorySerializer,
    DjangoUserSerializer,
    GroupSerializer,
    LedgerBookSerializer,
    LedgerUserSerializer,
    RecurringItemSerializer,
    TransactionSerializer,
)


class StaffOnlyModelPermissions(DjangoModelPermissions):
    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_staff and super().has_permission(request, view))


class DjangoUserViewSet(ReadOnlyModelViewSet):
    queryset = get_user_model().objects.order_by("id")
    serializer_class = DjangoUserSerializer
    permission_classes = [IsAdminUser]
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering_fields = ["id", "username", "email"]


class GroupViewSet(ReadOnlyModelViewSet):
    queryset = Group.objects.order_by("name")
    serializer_class = GroupSerializer
    permission_classes = [IsAdminUser]
    search_fields = ["name"]


class LedgerBookViewSet(ReadOnlyModelViewSet):
    serializer_class = LedgerBookSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["kind", "base_currency", "status"]
    search_fields = ["book_id", "name"]
    ordering_fields = ["book_id", "name", "kind", "base_currency"]

    def get_queryset(self):
        queryset = LedgerBook.objects.order_by("book_id")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return queryset
        return get_objects_for_user(
            self.request.user,
            "track_anywhere_django.view_ledgerbook",
            klass=queryset,
            accept_global_perms=False,
        )


class BookMemberViewSet(ReadOnlyModelViewSet):
    queryset = BookMember.objects.order_by("book_id", "user_id")
    serializer_class = BookMemberSerializer
    permission_classes = [StaffOnlyModelPermissions]
    filterset_fields = ["book_id", "user_id", "role", "status"]
    search_fields = ["book_id", "user_id"]


class AccountViewSet(ReadOnlyModelViewSet):
    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["book_id", "type", "currency", "institution_type", "subtype"]
    search_fields = ["account_id", "name", "institution"]
    ordering_fields = ["account_id", "name", "type", "currency"]

    def get_queryset(self):
        queryset = Account.objects.order_by("account_id")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return queryset
        book_ids = get_objects_for_user(
            self.request.user,
            "track_anywhere_django.view_ledgerbook",
            klass=LedgerBook.objects.all(),
            accept_global_perms=False,
        ).values_list("book_id", flat=True)
        return queryset.filter(book_id__in=book_ids)


class LedgerUserViewSet(ReadOnlyModelViewSet):
    queryset = User.objects.order_by("user_id")
    serializer_class = LedgerUserSerializer
    permission_classes = [StaffOnlyModelPermissions]
    search_fields = ["user_id", "username", "display_name"]


class AuthIdentityViewSet(ReadOnlyModelViewSet):
    queryset = AuthIdentity.objects.order_by("provider", "subject")
    serializer_class = AuthIdentitySerializer
    permission_classes = [StaffOnlyModelPermissions]
    filterset_fields = ["provider", "status", "email_verified"]
    search_fields = ["identity_id", "provider", "subject", "email", "user_id"]


class CategoryViewSet(ReadOnlyModelViewSet):
    queryset = Category.objects.order_by("book_id", "kind", "path_cache")
    serializer_class = CategorySerializer
    permission_classes = [StaffOnlyModelPermissions]
    filterset_fields = ["book_id", "kind", "status"]
    search_fields = ["category_id", "name", "path_cache"]


class TransactionViewSet(ReadOnlyModelViewSet):
    queryset = Transaction.objects.order_by("-occurred_at", "transaction_id")
    serializer_class = TransactionSerializer
    permission_classes = [StaffOnlyModelPermissions]
    filterset_fields = ["book_id", "category_id", "reversed_by"]
    search_fields = ["transaction_id", "purpose", "memo"]


class RecurringItemViewSet(ReadOnlyModelViewSet):
    queryset = RecurringItem.objects.order_by("book_id", "name")
    serializer_class = RecurringItemSerializer
    permission_classes = [StaffOnlyModelPermissions]
    filterset_fields = ["book_id", "kind", "status", "currency"]
    search_fields = ["recurring_id", "name"]


class AuditEventViewSet(ReadOnlyModelViewSet):
    queryset = AuditEvent.objects.order_by("-created_at")
    serializer_class = AuditEventSerializer
    permission_classes = [IsAdminUser]
    filterset_fields = ["operation", "actor_id", "actor_type", "entity_ref"]
    search_fields = ["event_id", "operation", "actor_id", "entity_ref"]
