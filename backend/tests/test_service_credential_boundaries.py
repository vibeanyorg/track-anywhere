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
