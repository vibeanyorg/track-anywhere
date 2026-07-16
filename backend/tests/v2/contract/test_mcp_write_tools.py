from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.api.dependencies import build_engine_dependencies
from track_anywhere.application.idempotency import CommandOutcome, CommandResult
from track_anywhere.infrastructure.db.models.auth import (
    BookMemberRecord,
    CredentialRecord,
    OAuthClientRecord,
    UserRecord,
)
from track_anywhere.infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    BookRecord,
)
from track_anywhere.infrastructure.db.models.event_store import BookEventHeadRecord
from track_anywhere.mcp.server import create_mcp_runtime
from track_anywhere.mcp import tools as mcp_tools


RESOURCE = "http://testserver/mcp"
EFFECTIVE_AT = "2026-07-16T09:30:00+00:00"


def test_replayed_write_prefers_the_persisted_transaction_identity() -> None:
    persisted_transaction_id = uuid4()
    recomputed_fallback = uuid4()
    outcome = CommandOutcome(
        result=CommandResult(
            response_schema_version=1,
            status_code=201,
            body={"transaction_id": str(persisted_transaction_id)},
            first_book_position=7,
            last_book_position=7,
        ),
        replayed=True,
    )

    assert (
        mcp_tools._persisted_transaction_id(outcome, recomputed_fallback)
        == persisted_transaction_id
    )


def test_mcp_catalog_tools_bootstrap_an_empty_user_into_a_usable_book(
    pg_engine,
) -> None:
    subject_id = "human:mcp-cold-start"
    write_token = "ta_mcp_catalog_write"
    _seed_catalog_oauth_tokens(
        pg_engine,
        subject_id=subject_id,
        write_token=write_token,
        read_token="ta_mcp_catalog_read",
    )
    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )
    book_request_id = str(uuid4())

    with TestClient(runtime.application) as client:
        created_book = _call_tool(
            client,
            write_token,
            "ledger_create_book",
            {
                "request_id": book_request_id,
                "current_name": "Personal Ledger",
                "base_asset_code": None,
            },
        )
        replayed_book = _call_tool(
            client,
            write_token,
            "ledger_create_book",
            {
                "request_id": book_request_id,
                "current_name": "Personal Ledger",
                "base_asset_code": None,
            },
        )
        book_id = created_book["structuredContent"]["book"]["book_id"]
        asset_request_id = str(uuid4())
        asset_arguments = {
            "book_id": book_id,
            "request_id": asset_request_id,
            "asset_code": "MCPUSD",
            "kind": "currency",
            "ledger_scale": 2,
            "input_scale": 2,
            "display_scale": 2,
            "current_name": "US Dollar",
        }
        created_asset = _call_tool(
            client,
            write_token,
            "ledger_create_asset",
            asset_arguments,
        )
        replayed_asset = _call_tool(
            client,
            write_token,
            "ledger_create_asset",
            asset_arguments,
        )
        conflicting_asset = _call_tool(
            client,
            write_token,
            "ledger_create_asset",
            {
                **asset_arguments,
                "asset_code": "MCPEUR",
                "current_name": "Euro",
            },
        )
        created_account = _call_tool(
            client,
            write_token,
            "ledger_create_account",
            {
                "book_id": book_id,
                "request_id": str(uuid4()),
                "asset_code": "MCPUSD",
                "account_type": "asset",
                "account_subtype": "checking",
                "current_name": "Everyday checking",
            },
        )
        books = _call_tool(client, write_token, "ledger_list_books", {})
        accounts = _call_tool(
            client,
            write_token,
            "ledger_list_accounts",
            {"book_id": book_id},
        )

    assert created_book["isError"] is False
    assert created_book["structuredContent"]["replayed"] is False
    assert replayed_book["isError"] is False
    assert replayed_book["structuredContent"]["replayed"] is True
    assert (
        replayed_book["structuredContent"]["book"]
        == (created_book["structuredContent"]["book"])
    )
    assert created_book["structuredContent"]["book"]["current_name"] == (
        "Personal Ledger"
    )
    assert created_asset["isError"] is False
    assert created_asset["structuredContent"]["asset"]["asset_code"] == "MCPUSD"
    assert created_asset["structuredContent"]["created"] is True
    assert created_asset["structuredContent"]["replayed"] is False
    assert replayed_asset["isError"] is False
    assert replayed_asset["structuredContent"]["replayed"] is True
    assert conflicting_asset["isError"] is True
    assert "idempotency key" in conflicting_asset["content"][0]["text"]
    assert created_account["isError"] is False
    account_body = created_account["structuredContent"]["account"]
    assert account_body["current_name"] == "Everyday checking"
    assert account_body["system_role"] is None
    assert [item["book_id"] for item in books["structuredContent"]["items"]] == [
        book_id
    ]
    assert [item["account_id"] for item in accounts["structuredContent"]["items"]] == [
        account_body["account_id"]
    ]

    parsed_book_id = UUID(book_id)
    with sessionmaker(pg_engine)() as session:
        book = session.get(BookRecord, parsed_book_id)
        membership = session.get(BookMemberRecord, (parsed_book_id, subject_id))
        asset = session.get(AssetRecord, "MCPUSD")
        conflicting_asset_record = session.get(AssetRecord, "MCPEUR")
        account = session.get(
            AccountRecord,
            (parsed_book_id, UUID(account_body["account_id"])),
        )
        head = session.get(BookEventHeadRecord, parsed_book_id)
    assert book is not None
    assert membership is not None
    assert membership.role == "owner"
    assert set(membership.scopes) == {
        "book:read",
        "book:write",
        "ledger:read",
        "ledger:write",
    }
    assert asset is not None
    assert conflicting_asset_record is None
    assert account is not None
    assert head is not None and head.last_position == 0


def test_mcp_catalog_tools_require_book_write_without_mutating_state(pg_engine) -> None:
    subject_id = "human:mcp-catalog-read-only"
    read_token = "ta_mcp_catalog_read_only"
    _seed_catalog_oauth_tokens(
        pg_engine,
        subject_id=subject_id,
        write_token="ta_mcp_catalog_unused_write",
        read_token=read_token,
        ledger_only_token="ta_mcp_ledger_read_only",
    )
    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )

    with TestClient(runtime.application) as client:
        denied = _call_tool(
            client,
            read_token,
            "ledger_create_book",
            {
                "request_id": str(uuid4()),
                "current_name": "Must not exist",
                "base_asset_code": None,
            },
        )
        books_denied = _call_tool(
            client,
            "ta_mcp_ledger_read_only",
            "ledger_list_books",
            {},
        )

    assert denied["isError"] is True
    assert "book:write" in denied["content"][0]["text"]
    assert "recreate" in denied["content"][0]["text"].lower()
    challenge = denied["_meta"]["mcp/www_authenticate"][0]
    assert 'error="insufficient_scope"' in challenge
    assert 'error_description="' in challenge
    assert 'scope="book:read book:write ledger:read"' in challenge
    assert books_denied["isError"] is True
    books_challenge = books_denied["_meta"]["mcp/www_authenticate"][0]
    assert 'error="insufficient_scope"' in books_challenge
    assert 'error_description="' in books_challenge
    assert 'scope="book:read ledger:read"' in books_challenge
    with sessionmaker(pg_engine)() as session:
        assert session.scalar(select(BookRecord).limit(1)) is None


def test_mcp_catalog_write_reports_pending_without_leaking_readback_errors(
    pg_engine,
    monkeypatch,
) -> None:
    subject_id = "human:mcp-catalog-pending"
    write_token = "ta_mcp_catalog_pending"
    _seed_catalog_oauth_tokens(
        pg_engine,
        subject_id=subject_id,
        write_token=write_token,
        read_token="ta_mcp_catalog_pending_read",
    )
    original_read = mcp_tools._read_created_book
    read_count = 0

    def fail_only_after_commit(*args, **kwargs):
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            raise SQLAlchemyError("private-ledger-value-must-not-leak")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(mcp_tools, "_read_created_book", fail_only_after_commit)
    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )
    request_id = str(uuid4())
    arguments = {
        "request_id": request_id,
        "current_name": "Pending readback Book",
        "base_asset_code": None,
    }

    with TestClient(runtime.application) as client:
        pending = _call_tool(
            client,
            write_token,
            "ledger_create_book",
            arguments,
        )
        verified_retry = _call_tool(
            client,
            write_token,
            "ledger_create_book",
            arguments,
        )

    assert pending["isError"] is False
    pending_body = pending["structuredContent"]
    assert pending_body["committed"] is True
    assert pending_body["verification_status"] == "pending"
    assert pending_body["book"] is None
    assert "private-ledger-value-must-not-leak" not in str(pending)
    assert request_id in pending_body["retry_guidance"]
    assert verified_retry["isError"] is False
    assert verified_retry["structuredContent"]["verification_status"] == "verified"
    assert verified_retry["structuredContent"]["replayed"] is True
    with sessionmaker(pg_engine)() as session:
        assert len(tuple(session.scalars(select(BookRecord)))) == 1


def test_mcp_semantic_writes_are_scoped_idempotent_and_directionally_safe(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(
        pg_engine,
        scenario,
        credit_account_type="expense",
    )
    target_account_id = uuid4()
    card_account_id = uuid4()
    _seed_write_surface(
        pg_engine,
        scenario,
        target_account_id=target_account_id,
        card_account_id=card_account_id,
    )
    write_token = "ta_mcp_write_contract"
    read_token = "ta_mcp_read_contract"
    _seed_oauth_tokens(pg_engine, scenario, write_token, read_token)

    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )
    membership_runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )
    with TestClient(membership_runtime.application) as client:
        expense_request_id = uuid4()
        expense = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(expense_request_id),
                "source_account_id": str(scenario.debit_account_id),
                "expense_account_id": str(scenario.credit_account_id),
                "asset_code": "USD",
                "amount": "12.34",
                "effective_at": EFFECTIVE_AT,
            },
        )
        replay = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(expense_request_id),
                "source_account_id": str(scenario.debit_account_id),
                "expense_account_id": str(scenario.credit_account_id),
                "asset_code": "USD",
                "amount": "12.34",
                "effective_at": EFFECTIVE_AT,
            },
        )
        conflict = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(expense_request_id),
                "source_account_id": str(scenario.debit_account_id),
                "expense_account_id": str(scenario.credit_account_id),
                "asset_code": "USD",
                "amount": "99.99",
                "effective_at": EFFECTIVE_AT,
            },
        )
        transfer = _call_tool(
            client,
            write_token,
            "ledger_record_transfer",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(uuid4()),
                "source_account_id": str(scenario.debit_account_id),
                "target_account_id": str(target_account_id),
                "asset_code": "USD",
                "amount": "2.00",
                "effective_at": "2026-07-16T09:31:00+00:00",
            },
        )
        charge = _call_tool(
            client,
            write_token,
            "ledger_record_credit_card_charge",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(uuid4()),
                "card_account_id": str(card_account_id),
                "expense_account_id": str(scenario.credit_account_id),
                "asset_code": "USD",
                "amount": "10.00",
                "effective_at": "2026-07-16T09:32:00+00:00",
            },
        )
        payment = _call_tool(
            client,
            write_token,
            "ledger_record_credit_card_payment",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(uuid4()),
                "source_account_id": str(scenario.debit_account_id),
                "card_account_id": str(card_account_id),
                "asset_code": "USD",
                "amount": "4.00",
                "effective_at": "2026-07-16T09:33:00+00:00",
            },
        )
        read_only_attempt = _call_tool(
            client,
            read_token,
            "ledger_record_expense",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(uuid4()),
                "source_account_id": str(scenario.debit_account_id),
                "expense_account_id": str(scenario.credit_account_id),
                "asset_code": "USD",
                "amount": "1.00",
                "effective_at": "2026-07-16T09:34:00+00:00",
            },
        )
        wrong_account_type = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(uuid4()),
                "source_account_id": str(scenario.debit_account_id),
                "expense_account_id": str(target_account_id),
                "asset_code": "USD",
                "amount": "1.00",
                "effective_at": "2026-07-16T09:34:00+00:00",
            },
        )

    assert expense["isError"] is False
    expense_body = expense["structuredContent"]
    assert expense_body["replayed"] is False
    assert expense_body["first_book_position"] == 1
    assert _posting_pairs(expense_body) == [
        (str(scenario.credit_account_id), "debit", "1234"),
        (str(scenario.debit_account_id), "credit", "1234"),
    ]
    assert replay["isError"] is False
    assert replay["structuredContent"]["replayed"] is True
    assert replay["structuredContent"]["transaction"] == expense_body["transaction"]
    assert conflict["isError"] is True
    assert "idempotency key" in conflict["content"][0]["text"]
    assert "outcome is unknown" not in conflict["content"][0]["text"]

    assert transfer["isError"] is False
    assert _posting_pairs(transfer["structuredContent"]) == [
        (str(target_account_id), "debit", "200"),
        (str(scenario.debit_account_id), "credit", "200"),
    ]
    assert charge["isError"] is False
    assert _posting_pairs(charge["structuredContent"]) == [
        (str(scenario.credit_account_id), "debit", "1000"),
        (str(card_account_id), "credit", "1000"),
    ]
    assert (
        charge["structuredContent"]["transaction"]["credit_card_relation"]["intent"]
        == "charge"
    )
    assert payment["isError"] is False
    assert _posting_pairs(payment["structuredContent"]) == [
        (str(card_account_id), "debit", "400"),
        (str(scenario.debit_account_id), "credit", "400"),
    ]
    assert (
        payment["structuredContent"]["transaction"]["credit_card_relation"]["intent"]
        == "payment"
    )
    assert read_only_attempt["isError"] is True
    assert "ledger:write" in read_only_attempt["content"][0]["text"]
    challenge = read_only_attempt["_meta"]["mcp/www_authenticate"][0]
    assert 'error="insufficient_scope"' in challenge
    assert 'error_description="' in challenge
    assert 'scope="ledger:read ledger:write"' in challenge
    assert wrong_account_type["isError"] is True
    assert "expense account" in wrong_account_type["content"][0]["text"]
    assert _book_head(pg_engine, scenario) == 4

    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update book_members set scopes='[\"ledger:read\"]' "
                "where book_id=:book_id and user_id=:user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
    with TestClient(runtime.application) as client:
        membership_denied = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            {
                "book_id": str(scenario.book_id),
                "request_id": str(uuid4()),
                "source_account_id": str(scenario.debit_account_id),
                "expense_account_id": str(scenario.credit_account_id),
                "asset_code": "USD",
                "amount": "1.00",
                "effective_at": "2026-07-16T09:35:00+00:00",
            },
        )
    assert membership_denied["isError"] is True
    assert "not writable" in membership_denied["content"][0]["text"]
    assert "outcome is unknown" not in membership_denied["content"][0]["text"]
    assert _book_head(pg_engine, scenario) == 4


@pytest.mark.parametrize(
    ("tool_name", "system_account_argument"),
    [
        ("ledger_record_expense", "source_account_id"),
        ("ledger_record_expense", "expense_account_id"),
        ("ledger_record_transfer", "source_account_id"),
        ("ledger_record_transfer", "target_account_id"),
        ("ledger_record_credit_card_charge", "card_account_id"),
        ("ledger_record_credit_card_charge", "expense_account_id"),
        ("ledger_record_credit_card_payment", "source_account_id"),
        ("ledger_record_credit_card_payment", "card_account_id"),
    ],
)
def test_mcp_semantic_writes_reject_system_managed_accounts(
    pg_engine,
    tool_name: str,
    system_account_argument: str,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(
        pg_engine,
        scenario,
        credit_account_type="expense",
    )
    target_account_id = uuid4()
    card_account_id = uuid4()
    _seed_write_surface(
        pg_engine,
        scenario,
        target_account_id=target_account_id,
        card_account_id=card_account_id,
    )
    write_token = f"ta_mcp_system_role_{tool_name}_{system_account_argument}"
    _seed_oauth_tokens(
        pg_engine,
        scenario,
        write_token,
        f"{write_token}_read",
    )
    system_account_id = uuid4()
    system_account_type = (
        "liability"
        if system_account_argument == "card_account_id"
        else "expense"
        if system_account_argument == "expense_account_id"
        else "asset"
    )
    system_account_subtype = (
        "credit_card" if system_account_argument == "card_account_id" else None
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts (book_id, account_id, asset_code, "
                "account_type, account_subtype, system_role, current_name, status) "
                "values (:book_id, :account_id, 'USD', :account_type, "
                ":account_subtype, 'fx_trading', 'Internal FX account', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": system_account_id,
                "account_type": system_account_type,
                "account_subtype": system_account_subtype,
            },
        )
    arguments_by_tool = {
        "ledger_record_expense": {
            "book_id": str(scenario.book_id),
            "request_id": str(uuid4()),
            "source_account_id": str(scenario.debit_account_id),
            "expense_account_id": str(scenario.credit_account_id),
            "asset_code": "USD",
            "amount": "1.00",
            "effective_at": EFFECTIVE_AT,
        },
        "ledger_record_transfer": {
            "book_id": str(scenario.book_id),
            "request_id": str(uuid4()),
            "source_account_id": str(scenario.debit_account_id),
            "target_account_id": str(target_account_id),
            "asset_code": "USD",
            "amount": "1.00",
            "effective_at": EFFECTIVE_AT,
        },
        "ledger_record_credit_card_charge": {
            "book_id": str(scenario.book_id),
            "request_id": str(uuid4()),
            "card_account_id": str(card_account_id),
            "expense_account_id": str(scenario.credit_account_id),
            "asset_code": "USD",
            "amount": "1.00",
            "effective_at": EFFECTIVE_AT,
        },
        "ledger_record_credit_card_payment": {
            "book_id": str(scenario.book_id),
            "request_id": str(uuid4()),
            "source_account_id": str(scenario.debit_account_id),
            "card_account_id": str(card_account_id),
            "asset_code": "USD",
            "amount": "1.00",
            "effective_at": EFFECTIVE_AT,
        },
    }
    arguments = arguments_by_tool[tool_name]
    arguments[system_account_argument] = str(system_account_id)
    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )

    with TestClient(runtime.application) as client:
        result = _call_tool(client, write_token, tool_name, arguments)

    assert result["isError"] is True
    assert "system-managed account" in result["content"][0]["text"]
    assert _book_head(pg_engine, scenario) == 0


def test_committed_write_survives_fresh_session_readback_failure_and_replays_once(
    pg_engine,
    monkeypatch,
    caplog,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(
        pg_engine,
        scenario,
        credit_account_type="expense",
    )
    _seed_write_surface(
        pg_engine,
        scenario,
        target_account_id=uuid4(),
        card_account_id=uuid4(),
    )
    write_token = "ta_mcp_readback_failure"
    _seed_oauth_tokens(
        pg_engine,
        scenario,
        write_token,
        "ta_mcp_readback_failure_read",
    )
    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )
    request_id = uuid4()
    arguments = _expense_arguments(scenario, request_id)
    original_readback = mcp_tools.get_journal_transaction
    sensitive_detail = "SELECT secret_sql FROM private_ledger WHERE amount=1234"

    def fail_readback(*_args, **_kwargs):
        raise SQLAlchemyError(sensitive_detail)

    caplog.set_level("ERROR", logger="track_anywhere.mcp.tools")
    monkeypatch.setattr(mcp_tools, "get_journal_transaction", fail_readback)
    with TestClient(runtime.application) as client:
        first = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            arguments,
        )
        replay_while_pending = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            arguments,
        )
        monkeypatch.setattr(
            mcp_tools,
            "get_journal_transaction",
            original_readback,
        )
        verified_replay = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            arguments,
        )

    assert first["isError"] is False
    first_body = first["structuredContent"]
    assert first_body["committed"] is True
    assert first_body["replayed"] is False
    assert first_body["verification_status"] == "pending"
    assert first_body["transaction"] is None
    assert first_body["first_book_position"] == 1
    assert first_body["last_book_position"] == 1
    assert str(request_id) in first_body["retry_guidance"]
    assert "exact same arguments" in first_body["retry_guidance"]

    pending_body = replay_while_pending["structuredContent"]
    assert replay_while_pending["isError"] is False
    assert pending_body["committed"] is True
    assert pending_body["replayed"] is True
    assert pending_body["verification_status"] == "pending"
    assert pending_body["transaction"] is None
    assert pending_body["transaction_id"] == first_body["transaction_id"]
    assert pending_body["first_book_position"] == 1
    assert pending_body["last_book_position"] == 1

    verified_body = verified_replay["structuredContent"]
    assert verified_replay["isError"] is False
    assert verified_body["committed"] is True
    assert verified_body["replayed"] is True
    assert verified_body["verification_status"] == "verified"
    assert verified_body["transaction"] is not None
    assert verified_body["transaction_id"] == first_body["transaction_id"]
    assert verified_body["first_book_position"] == 1
    assert verified_body["last_book_position"] == 1
    assert _book_head(pg_engine, scenario) == 1
    assert str(request_id) in caplog.text
    assert sensitive_detail not in caplog.text


@pytest.mark.parametrize("failure_kind", ["sqlalchemy", "unexpected"])
def test_post_commit_failure_is_redacted_and_exact_retry_does_not_duplicate(
    pg_engine,
    monkeypatch,
    caplog,
    failure_kind: str,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(
        pg_engine,
        scenario,
        credit_account_type="expense",
    )
    _seed_write_surface(
        pg_engine,
        scenario,
        target_account_id=uuid4(),
        card_account_id=uuid4(),
    )
    write_token = f"ta_mcp_unknown_{failure_kind}"
    _seed_oauth_tokens(
        pg_engine,
        scenario,
        write_token,
        f"ta_mcp_unknown_{failure_kind}_read",
    )
    runtime = create_mcp_runtime(
        build_engine_dependencies(
            pg_engine,
            expected_runtime_role=pg_engine.url.username,
        ),
        "http://testserver",
    )
    request_id = uuid4()
    arguments = _expense_arguments(scenario, request_id)
    original_execute = mcp_tools.execute_record_expense
    sensitive_detail = (
        f"{failure_kind} SELECT secret_sql FROM private_ledger amount=1234"
    )

    def commit_then_lose_response(command, **kwargs):
        original_execute(command, **kwargs)
        if failure_kind == "sqlalchemy":
            raise SQLAlchemyError(sensitive_detail)
        raise _UnexpectedWriteFailure(sensitive_detail)

    caplog.set_level("ERROR", logger="track_anywhere.mcp.tools")
    monkeypatch.setattr(
        mcp_tools,
        "execute_record_expense",
        commit_then_lose_response,
    )
    with TestClient(runtime.application) as client:
        uncertain = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            arguments,
        )
        monkeypatch.setattr(
            mcp_tools,
            "execute_record_expense",
            original_execute,
        )
        replay = _call_tool(
            client,
            write_token,
            "ledger_record_expense",
            arguments,
        )

    assert uncertain["isError"] is True
    uncertain_text = uncertain["content"][0]["text"]
    assert "Ledger write outcome is unknown" in uncertain_text
    assert str(request_id) in uncertain_text
    assert "exact same arguments" in uncertain_text
    assert sensitive_detail not in uncertain_text
    assert str(request_id) in caplog.text
    assert sensitive_detail not in caplog.text

    assert replay["isError"] is False
    replay_body = replay["structuredContent"]
    assert replay_body["committed"] is True
    assert replay_body["replayed"] is True
    assert replay_body["verification_status"] == "verified"
    assert replay_body["transaction"] is not None
    assert replay_body["first_book_position"] == 1
    assert replay_body["last_book_position"] == 1
    assert _book_head(pg_engine, scenario) == 1


class _UnexpectedWriteFailure(RuntimeError):
    pass


def _seed_write_surface(
    engine,
    scenario: JournalScenario,
    *,
    target_account_id,
    card_account_id,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                'update book_members set scopes=\'["ledger:read","ledger:write"]\' '
                "where book_id=:book_id and user_id=:user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
        connection.execute(
            text(
                "insert into accounts (book_id, account_id, asset_code, "
                "account_type, account_subtype, current_name, status) values "
                "(:book_id, :target_id, 'USD', 'asset', null, 'Savings', 'active'), "
                "(:book_id, :card_id, 'USD', 'liability', 'credit_card', "
                "'Credit card', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "target_id": target_account_id,
                "card_id": card_account_id,
            },
        )


def _seed_oauth_tokens(
    engine,
    scenario: JournalScenario,
    write_token: str,
    read_token: str,
) -> None:
    now = datetime.now(UTC)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        client = OAuthClientRecord(
            client_id="client_mcp_write_contract",
            client_name="MCP write contract",
            client_type="public",
            client_secret_hash=None,
            scopes=["ledger:read", "ledger:write"],
            status="active",
        )
        session.add(client)
        session.flush([client])
        for raw_token, scopes in (
            (write_token, ["ledger:read", "ledger:write"]),
            (read_token, ["ledger:read"]),
        ):
            session.add(
                CredentialRecord(
                    credential_id=uuid4(),
                    token_hash=sha256(raw_token.encode()).digest(),
                    jti=uuid4(),
                    actor_subject_id=scenario.actor_subject_id,
                    actor_type="human",
                    auth_kind="pkce",
                    book_id=None,
                    oauth_client_id="client_mcp_write_contract",
                    resource=RESOURCE,
                    refresh_family_id=uuid4(),
                    scopes=scopes,
                    issued_at=now,
                    expires_at=now + timedelta(hours=1),
                    revoked_at=None,
                    last_used_at=None,
                )
            )


def _call_tool(client, token: str, name: str, arguments: dict[str, object]):
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


def _seed_catalog_oauth_tokens(
    engine,
    *,
    subject_id: str,
    write_token: str,
    read_token: str,
    ledger_only_token: str | None = None,
) -> None:
    now = datetime.now(UTC)
    client_id = f"client-{subject_id}"
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            UserRecord(
                user_id=subject_id,
                subject_type="human",
                current_display_name="MCP cold start",
                status="active",
            )
        )
        session.add(
            OAuthClientRecord(
                client_id=client_id,
                client_name="MCP catalog contract",
                client_type="public",
                client_secret_hash=None,
                scopes=["book:read", "book:write", "ledger:read", "ledger:write"],
                status="active",
            )
        )
        session.flush()
        token_scopes = [
            (
                write_token,
                ["book:read", "book:write", "ledger:read"],
            ),
            (read_token, ["book:read", "ledger:read"]),
        ]
        if ledger_only_token is not None:
            token_scopes.append((ledger_only_token, ["ledger:read"]))
        for raw_token, scopes in token_scopes:
            session.add(
                CredentialRecord(
                    credential_id=uuid4(),
                    token_hash=sha256(raw_token.encode()).digest(),
                    jti=uuid4(),
                    actor_subject_id=subject_id,
                    actor_type="human",
                    auth_kind="pkce",
                    book_id=None,
                    oauth_client_id=client_id,
                    resource=RESOURCE,
                    refresh_family_id=uuid4(),
                    scopes=scopes,
                    issued_at=now,
                    expires_at=now + timedelta(hours=1),
                    revoked_at=None,
                    last_used_at=None,
                )
            )


def _expense_arguments(
    scenario: JournalScenario,
    request_id,
) -> dict[str, str]:
    return {
        "book_id": str(scenario.book_id),
        "request_id": str(request_id),
        "source_account_id": str(scenario.debit_account_id),
        "expense_account_id": str(scenario.credit_account_id),
        "asset_code": "USD",
        "amount": "12.34",
        "effective_at": EFFECTIVE_AT,
    }


def _posting_pairs(body: dict[str, object]) -> list[tuple[str, str, str]]:
    return [
        (posting["account_id"], posting["side"], posting["units"])
        for posting in body["transaction"]["postings"]
    ]


def _book_head(engine, scenario: JournalScenario) -> int:
    with sessionmaker(engine)() as session:
        return session.scalar(
            select(BookEventHeadRecord.last_position).where(
                BookEventHeadRecord.book_id == scenario.book_id
            )
        )
