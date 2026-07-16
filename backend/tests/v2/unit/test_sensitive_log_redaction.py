from __future__ import annotations

from track_anywhere.observability.audit import AuditSignal, redact_sensitive
from track_anywhere.observability.metrics import LedgerMetrics


def test_sensitive_fields_are_redacted_recursively() -> None:
    raw = {
        "api_key": "ta_super_secret",
        "setup_key": "ta_private_setup_secret",
        "credential": "bearer secret",
        "description": "private transaction description",
        "purpose": "private transaction purpose",
        "plaintext": "raw protected content",
        "line_memo": "private line memo",
        "ciphertext": "encrypted bytes",
        "nonce": "private nonce bytes",
        "content_hash": "private keyed commitment",
        "memo": "a full private merchant memo",
        "attachment_content": "raw attachment bytes",
        "book_id": "book-safe",
        "nested": {"csrf_token": "csrf-secret", "error_code": "safe_code"},
    }

    safe = redact_sensitive(raw)
    rendered = repr(safe)

    assert "ta_super_secret" not in rendered
    assert "ta_private_setup_secret" not in rendered
    assert "bearer secret" not in rendered
    assert "private transaction description" not in rendered
    assert "private transaction purpose" not in rendered
    assert "raw protected content" not in rendered
    assert "private line memo" not in rendered
    assert "encrypted bytes" not in rendered
    assert "private nonce bytes" not in rendered
    assert "private keyed commitment" not in rendered
    assert "full private merchant memo" not in rendered
    assert "raw attachment bytes" not in rendered
    assert safe["book_id"] == "book-safe"
    assert safe["nested"]["error_code"] == "safe_code"


def test_audit_signal_and_metrics_expose_only_bounded_fields() -> None:
    signal = AuditSignal.p0(
        code="terminal_hash_mismatch",
        book_id="book-1",
        fields={
            "memo": "private",
            "expected_hash": "do-not-log",
            "content_hash": "private-content-commitment",
        },
    )
    metrics = LedgerMetrics()
    metrics.increment(
        "integrity.p0",
        labels={
            "book_id": "book-1",
            "api_key": "secret",
            "setup_key": "setup-secret",
            "description": "private-description",
            "purpose": "private-purpose",
            "plaintext": "private-plaintext",
            "line_memo": "private-line-memo",
            "ciphertext": "private-ciphertext",
            "nonce": "private-nonce",
            "content_hash": "private-content-commitment",
        },
    )

    assert signal.severity == "P0"
    assert signal.fields == {
        "memo": "[REDACTED]",
        "expected_hash": "[REDACTED]",
        "content_hash": "[REDACTED]",
    }
    assert "secret" not in repr(metrics.snapshot())
    assert "setup-secret" not in repr(metrics.snapshot())
    assert "private-description" not in repr(metrics.snapshot())
    assert "private-purpose" not in repr(metrics.snapshot())
    assert "private-plaintext" not in repr(metrics.snapshot())
    assert "private-line-memo" not in repr(metrics.snapshot())
    assert "private-ciphertext" not in repr(metrics.snapshot())
    assert "private-nonce" not in repr(metrics.snapshot())
    assert "private-content-commitment" not in repr(metrics.snapshot())
