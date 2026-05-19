from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from guardian.shortcuts import assign_perm

from .models import LedgerBook


ROLE_GROUPS = {
    "owner": "Track Anywhere Owners",
    "admin": "Track Anywhere Admins",
    "editor": "Track Anywhere Editors",
    "viewer": "Track Anywhere Viewers",
}

ROLE_BOOK_PERMS = {
    "owner": ["view_ledgerbook", "change_ledgerbook", "delete_ledgerbook", "manage_ledgerbook", "post_ledgerbook_transaction", "review_ledgerbook"],
    "admin": ["view_ledgerbook", "change_ledgerbook", "manage_ledgerbook", "post_ledgerbook_transaction", "review_ledgerbook"],
    "editor": ["view_ledgerbook", "change_ledgerbook", "post_ledgerbook_transaction", "review_ledgerbook"],
    "viewer": ["view_ledgerbook"],
}


def ensure_role_groups() -> None:
    content_type = ContentType.objects.get_for_model(LedgerBook)
    permission_names = {
        "view_ledgerbook": "Can view ledger book",
        "change_ledgerbook": "Can change ledger book",
        "delete_ledgerbook": "Can delete ledger book",
        "manage_ledgerbook": "Can manage ledger book",
        "post_ledgerbook_transaction": "Can post ledger book transaction",
        "review_ledgerbook": "Can review ledger book",
    }
    permissions = {}
    for codename, name in permission_names.items():
        permissions[codename], _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": name},
        )
    for role, group_name in ROLE_GROUPS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for codename in ROLE_BOOK_PERMS[role]:
            group.permissions.add(permissions[codename])


def role_for_user(user) -> str:
    if user.is_superuser:
        return "owner"
    user_group_names = set(user.groups.values_list("name", flat=True))
    for role in ("owner", "admin", "editor", "viewer"):
        if ROLE_GROUPS[role] in user_group_names:
            return role
    return "viewer"


def ensure_user_role(user, role: str) -> None:
    ensure_role_groups()
    group = Group.objects.get(name=ROLE_GROUPS[role])
    user.groups.add(group)


def grant_book_role(user, book: LedgerBook, role: str) -> None:
    ensure_user_role(user, role)
    for codename in ROLE_BOOK_PERMS[role]:
        assign_perm(f"track_anywhere_django.{codename}", user, book)
