from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_credential_use_cases_are_context_scoped():
    expected_modules = {
        "service_credential_audit.py",
        "service_credential_issuance.py",
        "service_credential_queries.py",
        "service_credential_revocation.py",
        "service_credential_utils.py",
        "service_credentials.py",
    }
    for module in expected_modules:
        assert (BACKEND / module).exists()

    source = (BACKEND / "service_credentials.py").read_text()
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]

    assert [node.name for node in classes] == ["CredentialUseCases"]
    assert len(classes[0].body) == 1
    assert isinstance(classes[0].body[0], ast.Pass)
    assert "IssueCredentialCommand" not in source
    assert "RevokeCredentialCommand" not in source
    assert "secrets.token_urlsafe" not in source


def test_security_failure_audit_uses_change_set_boundary():
    source = (BACKEND / "service_credential_audit.py").read_text()

    assert "self._commit_audit_event(event)" in source
    assert "self.storage.save_audit_event" not in source


def test_auth_directory_state_writes_use_change_set_boundaries():
    storage_auth = (BACKEND / "storage_auth.py").read_text()
    service_identity = (BACKEND / "service_identity.py").read_text()
    credential_issuance = (BACKEND / "service_credential_issuance.py").read_text()

    assert "def save_auth_login_state" not in storage_auth
    assert "def save_credential_issue_state" not in storage_auth
    assert "save_auth_login_state" not in service_identity
    assert "save_credential_issue_state" not in credential_issuance
    assert "self._commit_auth_login_change" in service_identity
    assert "self._commit_book_change()" in credential_issuance


def test_auth_use_cases_do_not_bypass_book_member_dirty_tracking():
    offenders = []
    for filename in ["service_identity.py", "service_credential_issuance.py"]:
        source = (BACKEND / filename).read_text()
        if "books.members[" in source:
            offenders.append(filename)

    assert offenders == []


def test_platform_auth_credentials_use_service_change_set_writer():
    storage_auth = (BACKEND / "storage_auth.py").read_text()
    service_platform_auth = (BACKEND / "service_platform_auth.py").read_text()

    assert "def save_credential(" not in storage_auth
    assert "def save_credential_and_audit_event" not in storage_auth
    assert "credential_writer=self.storage" not in service_platform_auth
    assert "def __init__(self, service)" not in service_platform_auth
    assert "self._service" not in service_platform_auth
    assert "self._commit_credential_change()" in service_platform_auth


def test_platform_auth_grants_use_service_change_set_writer():
    storage_auth = (BACKEND / "storage_auth.py").read_text()
    storage_source = (BACKEND / "storage.py").read_text()
    storage_repositories = (BACKEND / "storage_repositories/security.py").read_text()
    service_platform_auth = (BACKEND / "service_platform_auth.py").read_text()

    assert "AuthStorageWriters" not in storage_source
    assert "class AuthStorageWriters" not in storage_auth
    assert "def save_authorization_grant(" not in storage_auth
    assert "def save_device_grant(" not in storage_auth
    assert "def load_authorization_grant(" in storage_repositories
    assert "def load_device_grant_by_device_hash(" in storage_repositories
    assert "def load_device_grant_by_user_hash(" in storage_repositories
    assert "grant_store=self.storage" not in service_platform_auth
    assert "self._service.storage.load_authorization_grant" not in service_platform_auth
    assert "self._service.storage.load_device_grant" not in service_platform_auth
    assert "uow.platform_grants.load_authorization_grant" in service_platform_auth
    assert "uow.platform_grants.load_device_grant_by_device_hash" in service_platform_auth
    assert "uow.platform_grants.load_device_grant_by_user_hash" in service_platform_auth
    assert "save_authorization_grant_change" not in service_platform_auth
    assert "save_device_grant_change" not in service_platform_auth
    assert "self._commit_authorization_grant_change(grant)" in service_platform_auth
    assert "self._commit_device_grant_change(grant)" in service_platform_auth
