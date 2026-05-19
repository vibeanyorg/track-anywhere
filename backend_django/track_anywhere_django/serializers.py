from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import serializers

from .models import Account, AuditEvent, AuthIdentity, BookMember, Category, LedgerBook, RecurringItem, Transaction, User


class DjangoUserSerializer(serializers.ModelSerializer):
    groups = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "first_name", "last_name", "is_active", "is_staff", "is_superuser", "groups"]


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ["id", "name"]


class LedgerBookSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerBook
        fields = ["book_id", "name", "kind", "base_currency", "timezone", "status", "template_key", "created_by", "version"]


class BookMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookMember
        fields = ["book_id", "user_id", "role", "status", "scopes", "version"]


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = ["account_id", "book_id", "name", "type", "currency", "institution_type", "subtype", "institution", "version"]


class LedgerUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["user_id", "username", "display_name", "version"]


class AuthIdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthIdentity
        fields = ["identity_id", "provider", "subject", "user_id", "email", "email_verified", "display_name", "picture_url", "status", "version"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["category_id", "book_id", "kind", "name", "path_cache", "status", "version"]


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = ["transaction_id", "book_id", "occurred_at", "purpose", "memo", "category_id", "reversed_by", "version"]


class RecurringItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringItem
        fields = ["recurring_id", "book_id", "name", "kind", "status", "amount", "currency", "recurrence", "reminder_days", "anchor_date", "version"]


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ["event_id", "operation", "actor_id", "actor_type", "entity_ref", "details", "created_at"]
