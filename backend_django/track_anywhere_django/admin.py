from __future__ import annotations

from django.contrib import admin

from .models import Account, AuditEvent, AuthIdentity, BookMember, Category, LedgerBook, RecurringItem, Transaction, User


class ReadOnlyAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(LedgerBook)
class LedgerBookAdmin(ReadOnlyAdmin):
    list_display = ["book_id", "name", "kind", "base_currency", "status", "version"]
    search_fields = ["book_id", "name"]
    list_filter = ["kind", "status", "base_currency"]


@admin.register(BookMember)
class BookMemberAdmin(ReadOnlyAdmin):
    list_display = ["book_id", "user_id", "role", "status", "version"]
    search_fields = ["book_id", "user_id"]
    list_filter = ["role", "status"]


@admin.register(Account)
class AccountAdmin(ReadOnlyAdmin):
    list_display = ["account_id", "book_id", "name", "type", "currency", "institution_type", "subtype"]
    search_fields = ["account_id", "name", "institution"]
    list_filter = ["type", "currency", "institution_type", "subtype"]


@admin.register(User)
class UserAdmin(ReadOnlyAdmin):
    list_display = ["user_id", "username", "display_name", "version"]
    search_fields = ["user_id", "username", "display_name"]


@admin.register(AuthIdentity)
class AuthIdentityAdmin(ReadOnlyAdmin):
    list_display = ["identity_id", "provider", "subject", "user_id", "email", "status", "version"]
    search_fields = ["identity_id", "provider", "subject", "email", "user_id"]
    list_filter = ["provider", "status", "email_verified"]


@admin.register(Category)
class CategoryAdmin(ReadOnlyAdmin):
    list_display = ["category_id", "book_id", "kind", "path_cache", "status", "version"]
    search_fields = ["category_id", "name", "path_cache"]
    list_filter = ["book_id", "kind", "status"]


@admin.register(Transaction)
class TransactionAdmin(ReadOnlyAdmin):
    list_display = ["transaction_id", "book_id", "occurred_at", "purpose", "category_id", "reversed_by", "version"]
    search_fields = ["transaction_id", "purpose", "memo"]
    list_filter = ["book_id"]


@admin.register(RecurringItem)
class RecurringItemAdmin(ReadOnlyAdmin):
    list_display = ["recurring_id", "book_id", "name", "kind", "status", "amount", "currency", "anchor_date", "version"]
    search_fields = ["recurring_id", "name", "provider", "reference"]
    list_filter = ["book_id", "kind", "status", "currency"]


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ["event_id", "operation", "actor_id", "actor_type", "entity_ref", "created_at"]
    search_fields = ["event_id", "operation", "actor_id", "entity_ref"]
    list_filter = ["operation", "actor_type"]
