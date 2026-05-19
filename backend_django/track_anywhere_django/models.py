from __future__ import annotations

from django.db import models


class LedgerBook(models.Model):
    book_id = models.CharField(max_length=80, primary_key=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=40)
    base_currency = models.CharField(max_length=16)
    timezone = models.CharField(max_length=80)
    status = models.CharField(max_length=40)
    template_key = models.CharField(max_length=80, blank=True, null=True)
    settings = models.JSONField()
    created_by = models.CharField(max_length=80)
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "ledger_books"
        permissions = [
            ("manage_ledgerbook", "Can manage ledger book"),
            ("post_ledgerbook_transaction", "Can post ledger book transaction"),
            ("review_ledgerbook", "Can review ledger book"),
        ]

    def __str__(self) -> str:
        return self.name


class BookMember(models.Model):
    book_id = models.CharField(max_length=80, primary_key=True)
    user_id = models.CharField(max_length=80)
    role = models.CharField(max_length=40)
    status = models.CharField(max_length=40)
    scopes = models.JSONField()
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "book_members"
        unique_together = [("book_id", "user_id")]

    def __str__(self) -> str:
        return f"{self.book_id}:{self.user_id}"


class Account(models.Model):
    account_id = models.CharField(max_length=80, primary_key=True)
    book_id = models.CharField(max_length=80)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=32)
    currency = models.CharField(max_length=16)
    institution_type = models.CharField(max_length=40, blank=True, null=True)
    subtype = models.CharField(max_length=64, blank=True, null=True)
    institution = models.CharField(max_length=120, blank=True, null=True)
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "accounts"

    def __str__(self) -> str:
        return self.name


class User(models.Model):
    user_id = models.CharField(max_length=80, primary_key=True)
    username = models.CharField(max_length=64)
    display_name = models.CharField(max_length=120)
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "users"

    def __str__(self) -> str:
        return self.username


class AuthIdentity(models.Model):
    identity_id = models.CharField(max_length=80, primary_key=True)
    provider = models.CharField(max_length=40)
    subject = models.CharField(max_length=160)
    user_id = models.CharField(max_length=80)
    email = models.CharField(max_length=240, blank=True, null=True)
    email_verified = models.BooleanField()
    display_name = models.CharField(max_length=120, blank=True, null=True)
    picture_url = models.CharField(max_length=500, blank=True, null=True)
    status = models.CharField(max_length=40)
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "auth_identities"

    def __str__(self) -> str:
        return f"{self.provider}:{self.subject}"


class Category(models.Model):
    category_id = models.CharField(max_length=80, primary_key=True)
    book_id = models.CharField(max_length=80)
    kind = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    path_cache = models.CharField(max_length=180)
    status = models.CharField(max_length=40)
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "categories"

    def __str__(self) -> str:
        return self.path_cache or self.name


class Transaction(models.Model):
    transaction_id = models.CharField(max_length=80, primary_key=True)
    book_id = models.CharField(max_length=80)
    memo = models.CharField(max_length=256)
    occurred_at = models.CharField(max_length=80)
    purpose = models.CharField(max_length=256)
    category_id = models.CharField(max_length=80, blank=True, null=True)
    reversed_by = models.CharField(max_length=80, blank=True, null=True)
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "transactions"

    def __str__(self) -> str:
        return self.purpose


class RecurringItem(models.Model):
    recurring_id = models.CharField(max_length=80, primary_key=True)
    book_id = models.CharField(max_length=80)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=40)
    status = models.CharField(max_length=40)
    amount = models.CharField(max_length=80, blank=True, null=True)
    currency = models.CharField(max_length=16, blank=True, null=True)
    recurrence = models.JSONField()
    reminder_days = models.JSONField()
    anchor_date = models.CharField(max_length=20)
    version = models.IntegerField()

    class Meta:
        managed = False
        db_table = "recurring_items"

    def __str__(self) -> str:
        return self.name


class AuditEvent(models.Model):
    event_id = models.CharField(max_length=80, primary_key=True)
    operation = models.CharField(max_length=120)
    actor_id = models.CharField(max_length=80)
    actor_type = models.CharField(max_length=40)
    entity_ref = models.CharField(max_length=120, blank=True, null=True)
    details = models.JSONField()
    created_at = models.CharField(max_length=80)

    class Meta:
        managed = False
        db_table = "audit_events"

    def __str__(self) -> str:
        return self.operation
