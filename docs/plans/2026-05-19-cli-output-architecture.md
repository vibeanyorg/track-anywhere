# CLI Output Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild the Track Anywhere CLI output path so every command is human-friendly by default, agent-friendly with `--json`, and routed through a typed command runtime.

**Architecture:** Add a CLI outcome/runtime boundary that all commands use. Click remains the terminal binding layer, command executors return structured results, JSON rendering emits one stable envelope, and Rich presenters render human output from the same outcome.

**Tech Stack:** Python 3.12, Click, Rich, pytest, existing `track_anywhere_cli` modules.

---

## Ground Rules

- Do not change backend domain behavior.
- Do not add dependencies.
- Keep each migration step test-first.
- Preserve existing exit codes from `cli/track_anywhere_cli/exit_codes.py`.
- Do not stage or revert unrelated dirty worktree changes.
- Commit after each completed task using the repository Lore commit protocol.

## Target Files

Create:

- `cli/track_anywhere_cli/output.py`
- `cli/track_anywhere_cli/runtime.py`
- `cli/track_anywhere_cli/interaction.py`
- `cli/track_anywhere_cli/presenters.py`
- `cli/tests/test_cli_output_contract.py`
- `cli/tests/test_cli_output_architecture.py`

Modify:

- `cli/track_anywhere_cli/click_common.py`
- `cli/track_anywhere_cli/click_app.py`
- `cli/track_anywhere_cli/click_catalog.py`
- `cli/track_anywhere_cli/click_ledger.py`
- `cli/track_anywhere_cli/click_investment.py`
- `cli/track_anywhere_cli/click_recurring.py`
- `cli/track_anywhere_cli/renderers.py`
- `cli/track_anywhere_cli/config.py`
- `cli/track_anywhere_cli/commands.py`
- `cli/track_anywhere_cli/main.py`
- `cli/tests/test_cli.py`
- `cli/tests/test_cli_accounts.py`
- `cli/tests/test_cli_catalog.py`
- `cli/tests/test_cli_ledger.py`
- `cli/tests/test_cli_investments.py`
- `cli/tests/test_cli_recurring.py`
- `contract_tests/test_cli_conformance.py`
- `README.md`

Delete after migration:

- `cli/track_anywhere_cli/parser.py`
- `cli/track_anywhere_cli/parser_recurring.py`

## Task 1: Add Outcome Types And JSON Envelope

**Files:**

- Create: `cli/track_anywhere_cli/output.py`
- Create: `cli/tests/test_cli_output_contract.py`

**Step 1: Write the failing JSON envelope tests**

Add:

```python
from __future__ import annotations

import json

from track_anywhere_cli.output import CliDiagnostic, CliOutcome, outcome_to_json_document
from track_anywhere_cli.exit_codes import EXIT_SUCCESS, EXIT_AUTH


def test_success_outcome_json_envelope():
    outcome = CliOutcome(
        command_path="account.list",
        status=200,
        data={"accounts": []},
        diagnostics=[],
        exit_code=EXIT_SUCCESS,
    )

    payload = json.loads(outcome_to_json_document(outcome))

    assert payload == {
        "ok": True,
        "command": "account.list",
        "status": 200,
        "data": {"accounts": []},
        "diagnostics": [],
    }


def test_error_outcome_json_envelope():
    outcome = CliOutcome(
        command_path="auth.status",
        status=401,
        data={"detail": "not authenticated"},
        diagnostics=[
            CliDiagnostic(
                level="error",
                message="Authentication is required.",
                code="auth_required",
            )
        ],
        exit_code=EXIT_AUTH,
    )

    payload = json.loads(outcome_to_json_document(outcome))

    assert payload["ok"] is False
    assert payload["command"] == "auth.status"
    assert payload["diagnostics"][0]["code"] == "auth_required"
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py -q
```

Expected: FAIL because `track_anywhere_cli.output` does not exist.

**Step 3: Implement minimal outcome types**

Create `cli/track_anywhere_cli/output.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class CliDiagnostic:
    level: Literal["info", "warning", "error"]
    message: str
    code: str | None = None
    detail: Any | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"level": self.level, "message": self.message}
        if self.code is not None:
            payload["code"] = self.code
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class CliOutcome:
    command_path: str
    status: int
    data: Any
    diagnostics: list[CliDiagnostic]
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.status < 400


@dataclass(frozen=True)
class CommandResult:
    status: int
    data: Any
    diagnostics: list[CliDiagnostic] | None = None


def outcome_payload(outcome: CliOutcome) -> dict[str, Any]:
    return {
        "ok": outcome.ok,
        "command": outcome.command_path,
        "status": outcome.status,
        "data": outcome.data,
        "diagnostics": [item.to_json() for item in outcome.diagnostics],
    }


def outcome_to_json_document(outcome: CliOutcome) -> str:
    return json.dumps(outcome_payload(outcome), indent=2, sort_keys=True)
```

**Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add cli/track_anywhere_cli/output.py cli/tests/test_cli_output_contract.py
git commit -m "Give CLI output a stable outcome envelope" \
  -m "The CLI needs one machine contract before command groups can migrate, so this introduces the typed outcome and diagnostic payload used by JSON mode."
```

Add Lore trailers to the commit body:

```text
Confidence: high
Scope-risk: narrow
Tested: uv run pytest cli/tests/test_cli_output_contract.py -q
```

## Task 2: Add Runtime Rendering Boundary

**Files:**

- Create: `cli/track_anywhere_cli/runtime.py`
- Modify: `cli/track_anywhere_cli/renderers.py`
- Modify: `cli/track_anywhere_cli/click_common.py`
- Modify: `cli/tests/test_cli_output_contract.py`

**Step 1: Write failing runtime tests**

Append:

```python
from track_anywhere_cli.runtime import build_outcome


def test_build_outcome_maps_status_to_exit_code():
    outcome = build_outcome("account.show", 404, {"detail": "missing"})

    assert outcome.command_path == "account.show"
    assert outcome.status == 404
    assert outcome.exit_code == 8
    assert outcome.ok is False


def test_render_json_writes_one_envelope(capsys):
    from track_anywhere_cli.renderers import emit_outcome

    outcome = CliOutcome(
        command_path="account.list",
        status=200,
        data={"accounts": []},
        diagnostics=[],
        exit_code=0,
    )

    emit_outcome(outcome, json_mode=True, no_color=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "account.list"
    assert payload["data"] == {"accounts": []}
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py -q
```

Expected: FAIL because `runtime.py` and `emit_outcome` do not exist.

**Step 3: Implement runtime outcome construction**

Create `cli/track_anywhere_cli/runtime.py`:

```python
from __future__ import annotations

from typing import Any

from .exit_codes import EXIT_SUCCESS
from .http import exit_for_status
from .output import CliDiagnostic, CliOutcome


def diagnostics_for_status(status: int, data: Any) -> list[CliDiagnostic]:
    if status < 400:
        return []
    code = {
        401: "auth_required",
        403: "policy_denied",
        404: "not_found",
        409: "conflict",
        400: "security_precondition",
    }.get(status, "request_failed")
    detail = data.get("detail") if isinstance(data, dict) else data
    return [
        CliDiagnostic(
            level="error",
            code=code,
            message=str(detail or "Command failed."),
            detail=data,
        )
    ]


def build_outcome(
    command_path: str,
    status: int,
    data: Any,
    diagnostics: list[CliDiagnostic] | None = None,
) -> CliOutcome:
    all_diagnostics = [*diagnostics_for_status(status, data), *(diagnostics or [])]
    return CliOutcome(
        command_path=command_path,
        status=status,
        data=data,
        diagnostics=all_diagnostics,
        exit_code=EXIT_SUCCESS if status < 400 else exit_for_status(status, data),
    )
```

Modify `cli/track_anywhere_cli/renderers.py` to add `emit_outcome`:

```python
from .output import CliOutcome, outcome_to_json_document


def emit_outcome(outcome: CliOutcome, *, json_mode: bool, no_color: bool) -> None:
    if json_mode:
        print(outcome_to_json_document(outcome))
        return
    console = Console(no_color=no_color)
    renderable = _render_human(outcome.data, outcome.command_path)
    console.print(renderable)
```

Keep `emit_result` as a compatibility shim during migration:

```python
def emit_result(data: Any, *, json_mode: bool, no_color: bool, command_path: str = "") -> None:
    from .runtime import build_outcome

    emit_outcome(build_outcome(command_path, 200, data), json_mode=json_mode, no_color=no_color)
```

**Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add cli/track_anywhere_cli/runtime.py cli/track_anywhere_cli/renderers.py cli/tests/test_cli_output_contract.py
git commit -m "Route CLI rendering through outcomes"
```

Lore trailers:

```text
Confidence: high
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli_output_contract.py -q
```

## Task 3: Migrate API Runtime To JSON Envelope

**Files:**

- Modify: `cli/track_anywhere_cli/click_common.py`
- Modify: `cli/tests/test_cli_ledger.py`
- Modify: `cli/tests/test_cli_accounts.py`
- Modify: `cli/tests/test_cli_catalog.py`

**Step 1: Write failing envelope assertions for one API command**

In `cli/tests/test_cli_ledger.py`, change the JSON assertion in
`test_tx_record_posts_agent_friendly_payload`:

```python
payload = json.loads(capsys.readouterr().out)
assert payload["ok"] is True
assert payload["command"] == "tx.record"
assert payload["data"]["transaction"]["purpose"] == "lunch"
```

In `cli/tests/test_cli_accounts.py`, add a similar assertion to one account
read test using `capsys`.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest cli/tests/test_cli_ledger.py::test_tx_record_posts_agent_friendly_payload cli/tests/test_cli_accounts.py -q
```

Expected: FAIL because stdout is still raw payload JSON for many paths.

**Step 3: Update `run_api` to emit outcomes**

In `cli/track_anywhere_cli/click_common.py`:

```python
from .renderers import emit_outcome
from .runtime import build_outcome
```

Replace the raw emit in `run_api`:

```python
outcome = build_outcome(command_path, status, data)
emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
return outcome.exit_code
```

When auth resolution fails, build and emit an outcome instead of
`click.echo`:

```python
outcome = build_outcome(
    command_path,
    401,
    {"detail": str(exc)},
)
emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
return outcome.exit_code
```

**Step 4: Update affected JSON tests to read `data`**

For all CLI tests migrated in this task, replace direct raw payload reads:

```python
json.loads(capsys.readouterr().out)["transaction"]
```

with:

```python
json.loads(capsys.readouterr().out)["data"]["transaction"]
```

**Step 5: Run tests to verify they pass**

Run:

```bash
uv run pytest cli/tests/test_cli_ledger.py cli/tests/test_cli_accounts.py cli/tests/test_cli_catalog.py -q
```

Expected: PASS after all assertions in these files use the envelope.

**Step 6: Commit**

```bash
git add cli/track_anywhere_cli/click_common.py cli/tests/test_cli_ledger.py cli/tests/test_cli_accounts.py cli/tests/test_cli_catalog.py
git commit -m "Make API CLI commands emit the JSON envelope"
```

Lore trailers:

```text
Rejected: Keep raw API payloads in JSON mode | leaves agents without a uniform success and diagnostics contract
Confidence: high
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli_ledger.py cli/tests/test_cli_accounts.py cli/tests/test_cli_catalog.py -q
```

## Task 4: Add Rich Presenter Registry And Human Fallback Guard

**Files:**

- Create: `cli/track_anywhere_cli/presenters.py`
- Modify: `cli/track_anywhere_cli/renderers.py`
- Modify: `cli/tests/test_cli_output_contract.py`
- Create: `cli/tests/test_cli_output_architecture.py`

**Step 1: Write failing presenter tests**

Add to `cli/tests/test_cli_output_contract.py`:

```python
from rich.table import Table

from track_anywhere_cli.presenters import presenter_for


def test_account_list_has_explicit_presenter():
    presenter = presenter_for("account.list")
    renderable = presenter({"accounts": []})

    assert not isinstance(renderable, dict)


def test_unknown_presenter_fails():
    import pytest

    with pytest.raises(KeyError):
        presenter_for("unknown.command")
```

Create `cli/tests/test_cli_output_architecture.py`:

```python
from __future__ import annotations

from pathlib import Path


def test_no_raw_human_dict_fallback_in_renderer():
    text = Path("cli/track_anywhere_cli/renderers.py").read_text()

    assert "return data" not in text
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py cli/tests/test_cli_output_architecture.py -q
```

Expected: FAIL because presenters do not exist and renderer still returns data.

**Step 3: Implement presenter registry**

Create `cli/track_anywhere_cli/presenters.py`:

```python
from __future__ import annotations

from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

Presenter = Callable[[Any], Any]


def empty_panel(title: str, message: str) -> Panel:
    return Panel(message, title=title)


def account_list(data: Any) -> Table | Panel:
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    if not accounts:
        return empty_panel("Accounts", "No accounts found.")
    table = Table(title="Accounts")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Currency")
    table.add_column("Balance", justify="right")
    for account in accounts:
        table.add_row(
            str(account.get("account_id", "")),
            str(account.get("name", "")),
            str(account.get("type", "")),
            str(account.get("currency", "")),
            str(account.get("balance", account.get("current_balance", ""))),
        )
    return table


def success_panel(title: str) -> Presenter:
    def present(data: Any) -> Panel:
        return Panel(str(data), title=title)

    return present


PRESENTERS: dict[str, Presenter] = {
    "account.list": account_list,
    "account.find": account_list,
    "tx.record": success_panel("Transaction recorded"),
}


def presenter_for(command_path: str) -> Presenter:
    return PRESENTERS[command_path]
```

**Step 4: Wire renderer to presenter registry**

In `renderers.py`, replace `_render_human` fallback:

```python
from rich.panel import Panel
from .presenters import presenter_for


def _render_human(data: Any, command_path: str):
    if isinstance(data, str):
        return data
    try:
        return presenter_for(command_path)(data)
    except KeyError:
        return Panel("No human presenter registered.", title=command_path or "Command")
```

After this task, later tasks should register every public command and remove the
generic missing-presenter panel from normal command paths.

**Step 5: Run tests to verify they pass**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py cli/tests/test_cli_output_architecture.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add cli/track_anywhere_cli/presenters.py cli/track_anywhere_cli/renderers.py cli/tests/test_cli_output_contract.py cli/tests/test_cli_output_architecture.py
git commit -m "Give human CLI output an explicit presenter registry"
```

Lore trailers:

```text
Confidence: medium
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli_output_contract.py cli/tests/test_cli_output_architecture.py -q
```

## Task 5: Move Config Warnings Into Diagnostics

**Files:**

- Modify: `cli/track_anywhere_cli/config.py`
- Modify: `cli/track_anywhere_cli/click_common.py`
- Modify: `cli/tests/test_cli.py`

**Step 1: Write failing diagnostic tests**

In `cli/tests/test_cli.py`, update
`test_cli_rejects_env_token_without_insecure_opt_in` to assert JSON diagnostics
when `--json` is present:

```python
def test_cli_rejects_env_token_without_insecure_opt_in(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    assert main(["capture", "spent 38", "--idempotency-key", "k", "--json"]) == EXIT_AUTH

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "auth_required"
```

Add a test for insecure automation warning:

```python
def test_env_token_warning_is_structured(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    def fake_request(config, method, path, payload=None, key=None):
        return 200, {"draft": {"draft_id": "draft_1"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--insecure-automation", "capture", "spent 38", "--idempotency-key", "k", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostics"][0]["level"] == "warning"
    assert payload["diagnostics"][0]["code"] == "insecure_env_token"
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest cli/tests/test_cli.py::test_cli_rejects_env_token_without_insecure_opt_in cli/tests/test_cli.py::test_env_token_warning_is_structured -q
```

Expected: FAIL because `resolve_token` prints warnings directly and cannot
return diagnostics.

**Step 3: Introduce token resolution result**

In `config.py`:

```python
@dataclass(frozen=True)
class TokenResolution:
    token: str | None
    diagnostics: list[Any]
```

Add:

```python
def resolve_token_with_diagnostics(args: argparse.Namespace) -> TokenResolution:
    if args.token:
        return TokenResolution(args.token, [])
    env_token = os.getenv("TRACK_ANYWHERE_TOKEN")
    if env_token:
        if not args.insecure_automation:
            raise RuntimeError("TRACK_ANYWHERE_TOKEN requires --insecure-automation; prefer OS keyring")
        from .output import CliDiagnostic

        return TokenResolution(
            env_token,
            [
                CliDiagnostic(
                    level="warning",
                    code="insecure_env_token",
                    message="Using TRACK_ANYWHERE_TOKEN with --insecure-automation.",
                )
            ],
        )
    return TokenResolution(TokenStore().load(), [])
```

Keep `resolve_token()` as a compatibility wrapper:

```python
def resolve_token(args: argparse.Namespace) -> str | None:
    return resolve_token_with_diagnostics(args).token
```

**Step 4: Attach diagnostics in `run_api`**

In `click_common.run_api`, call `resolve_token_with_diagnostics(args)` and pass
its diagnostics into `build_outcome()`.

**Step 5: Run tests to verify they pass**

Run:

```bash
uv run pytest cli/tests/test_cli.py -q
```

Expected: PASS after existing assertions are adjusted for JSON envelope.

**Step 6: Commit**

```bash
git add cli/track_anywhere_cli/config.py cli/track_anywhere_cli/click_common.py cli/tests/test_cli.py
git commit -m "Return CLI auth warnings as structured diagnostics"
```

Lore trailers:

```text
Rejected: Keep stderr warnings for automation | breaks the one-document JSON contract
Confidence: high
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli.py -q
```

## Task 6: Migrate Auth And Data Local Commands

**Files:**

- Create: `cli/track_anywhere_cli/interaction.py`
- Modify: `cli/track_anywhere_cli/click_app.py`
- Modify: `cli/tests/test_cli.py`

**Step 1: Write failing local command envelope tests**

Update auth/data tests to assert envelope:

```python
def test_auth_dev_token_saves_local_token(monkeypatch, capsys):
    ...
    assert main(["auth", "dev-token", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.dev_token"
    assert payload["data"]["token"] == "owner-token"
```

For `auth login`:

```python
assert payload["command"] == "auth.login"
assert payload["data"]["token_saved"] is True
```

For `data backup`:

```python
payload = json.loads(capsys.readouterr().out)
backup_path = Path(payload["data"]["backup"]["backup_path"])
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest cli/tests/test_cli.py -q
```

Expected: FAIL because local commands still emit mixed raw strings and payloads.

**Step 3: Add interaction channel**

Create `cli/track_anywhere_cli/interaction.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import webbrowser

import click


class Interaction(Protocol):
    def open_url(self, url: str) -> None: ...
    def prompt(self, label: str, *, secret: bool = False) -> str: ...


@dataclass(frozen=True)
class ClickInteraction:
    open_browser: bool = True

    def open_url(self, url: str) -> None:
        if self.open_browser:
            webbrowser.open(url)

    def prompt(self, label: str, *, secret: bool = False) -> str:
        return click.prompt(label, hide_input=secret, err=True)
```

**Step 4: Migrate local commands to outcomes**

In `click_app.py`, replace string/raw branching with `build_outcome` and
`emit_outcome`.

For successful manual token login:

```python
outcome = build_outcome(
    "auth.login",
    200,
    {"authenticated": True, "token_saved": True},
)
emit_outcome(outcome, json_mode=state.json_mode or json_mode, no_color=state.no_color or no_color)
return outcome.exit_code
```

For callback mismatch:

```python
outcome = build_outcome(
    "auth.login",
    400,
    {"detail": str(exc)},
)
emit_outcome(...)
return outcome.exit_code
```

**Step 5: Run tests to verify they pass**

Run:

```bash
uv run pytest cli/tests/test_cli.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add cli/track_anywhere_cli/interaction.py cli/track_anywhere_cli/click_app.py cli/tests/test_cli.py
git commit -m "Move local CLI commands onto the shared outcome runtime"
```

Lore trailers:

```text
Confidence: medium
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli.py -q
```

## Task 7: Register Presenters For Catalog And Account Commands

**Files:**

- Modify: `cli/track_anywhere_cli/presenters.py`
- Modify: `cli/tests/test_cli_accounts.py`
- Modify: `cli/tests/test_cli_catalog.py`
- Modify: `cli/tests/test_cli_output_contract.py`

**Step 1: Write failing presenter coverage tests**

Add:

```python
PUBLIC_CATALOG_COMMANDS = [
    "summary.accounts",
    "summary.categories",
    "user.create",
    "user.list",
    "category.create",
    "category.list",
    "category.find",
    "category.show",
    "credit_card.list",
    "credit_card.show",
    "credit_card.update",
    "account.create",
    "account.list",
    "account.find",
    "account.show",
    "account.update",
    "account.balance",
    "account.adjust",
]


def test_catalog_and_account_commands_have_presenters():
    from track_anywhere_cli.presenters import presenter_for

    for command_path in PUBLIC_CATALOG_COMMANDS:
        assert presenter_for(command_path)
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py -q
```

Expected: FAIL because most presenters are missing.

**Step 3: Implement catalog/account presenters**

In `presenters.py`, add helper functions:

```python
def object_summary(title: str, fields: list[tuple[str, Any]]) -> Table:
    table = Table(title=title, show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    for label, value in fields:
        table.add_row(label, "" if value is None else str(value))
    return table


def accounts_summary(data: Any) -> Table | Panel:
    groups = data.get("groups", []) if isinstance(data, dict) else []
    if not groups:
        return empty_panel("Account summary", "No account summary rows found.")
    table = Table(title="Account summary")
    table.add_column("Group")
    table.add_column("Currency")
    table.add_column("Assets", justify="right")
    table.add_column("Liabilities", justify="right")
    table.add_column("Net", justify="right")
    for row in groups:
        table.add_row(
            str(row.get("group", row.get("key", ""))),
            str(row.get("currency", "")),
            str(row.get("asset_amount", "")),
            str(row.get("liability_amount", "")),
            str(row.get("net_amount", row.get("amount", ""))),
        )
    return table
```

Register all command paths from the coverage test. Use simple panels for
mutations at first, then refine if needed.

**Step 4: Add human output assertions**

For one account list, one summary, and one category command, call without
`--json` and assert:

```python
output = capsys.readouterr().out
assert "{" not in output.lstrip()[:1]
assert "Accounts" in output
```

**Step 5: Run tests**

Run:

```bash
uv run pytest cli/tests/test_cli_accounts.py cli/tests/test_cli_catalog.py cli/tests/test_cli_output_contract.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add cli/track_anywhere_cli/presenters.py cli/tests/test_cli_accounts.py cli/tests/test_cli_catalog.py cli/tests/test_cli_output_contract.py
git commit -m "Give account and catalog commands explicit human presenters"
```

Lore trailers:

```text
Confidence: medium
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli_accounts.py cli/tests/test_cli_catalog.py cli/tests/test_cli_output_contract.py -q
```

## Task 8: Register Presenters For Ledger, Investment, And Recurring Commands

**Files:**

- Modify: `cli/track_anywhere_cli/presenters.py`
- Modify: `cli/tests/test_cli_ledger.py`
- Modify: `cli/tests/test_cli_investments.py`
- Modify: `cli/tests/test_cli_recurring.py`
- Modify: `cli/tests/test_cli_output_contract.py`

**Step 1: Write failing presenter coverage tests**

Add command paths:

```python
PUBLIC_OPERATION_COMMANDS = [
    "capture",
    "draft.confirm",
    "tx.record",
    "tx.list",
    "tx.show",
    "tx.reverse",
    "expense.record",
    "income.record",
    "balance.adjust",
    "balance",
    "investment.event",
    "investment.performance",
    "recurring.create",
    "recurring.list",
    "recurring.show",
    "recurring.update",
    "recurring.reminders",
    "recurring.draft_due",
]
```

Assert `presenter_for()` resolves each path.

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py -q
```

Expected: FAIL because operation presenters are missing.

**Step 3: Implement presenters**

Move the existing recurring table renderers from `renderers.py` into
`presenters.py` and register them.

Add transaction helpers:

```python
def transactions_table(data: Any) -> Table | Panel:
    transactions = data.get("transactions", []) if isinstance(data, dict) else []
    if not transactions:
        return empty_panel("Transactions", "No transactions found.")
    table = Table(title="Transactions")
    table.add_column("ID")
    table.add_column("Occurred")
    table.add_column("Purpose")
    table.add_column("Amount", justify="right")
    for tx in transactions:
        amount = tx.get("amount") or tx.get("total_amount") or ""
        table.add_row(
            str(tx.get("transaction_id", "")),
            str(tx.get("occurred_at", "")),
            str(tx.get("purpose", tx.get("memo", ""))),
            str(amount),
        )
    return table
```

Use success panels for mutations where the backend response shape is still
compact.

**Step 4: Update JSON tests to use envelope**

Update all remaining CLI JSON test assertions in:

- `cli/tests/test_cli_investments.py`
- `cli/tests/test_cli_recurring.py`
- any remaining raw JSON assertions in `cli/tests/test_cli_ledger.py`

Pattern:

```python
payload = json.loads(capsys.readouterr().out)
assert payload["data"]["recurring_item"]["recurring_id"] == "rec_1"
```

**Step 5: Run tests**

Run:

```bash
uv run pytest cli/tests/test_cli_ledger.py cli/tests/test_cli_investments.py cli/tests/test_cli_recurring.py cli/tests/test_cli_output_contract.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add cli/track_anywhere_cli/presenters.py cli/tests/test_cli_ledger.py cli/tests/test_cli_investments.py cli/tests/test_cli_recurring.py cli/tests/test_cli_output_contract.py
git commit -m "Give operation commands explicit human presenters"
```

Lore trailers:

```text
Confidence: medium
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli_ledger.py cli/tests/test_cli_investments.py cli/tests/test_cli_recurring.py cli/tests/test_cli_output_contract.py -q
```

## Task 9: Introduce Typed Command Specs

**Files:**

- Modify: `cli/track_anywhere_cli/runtime.py`
- Modify: `cli/track_anywhere_cli/commands.py`
- Modify: `cli/track_anywhere_cli/click_common.py`
- Modify: `contract_tests/test_cli_conformance.py`

**Step 1: Write failing command spec tests**

Add to `cli/tests/test_cli_output_contract.py`:

```python
from track_anywhere_cli.commands import command_paths


def test_command_registry_contains_public_paths():
    paths = set(command_paths())

    assert "account.list" in paths
    assert "tx.record" in paths
    assert "auth.login" in paths
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py::test_command_registry_contains_public_paths -q
```

Expected: FAIL because the registry does not exist.

**Step 3: Add command spec structure**

In `runtime.py`:

```python
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

InputT = TypeVar("InputT")


@dataclass(frozen=True)
class RuntimeContext:
    config: CliConfig
    requester: Requester


@dataclass(frozen=True)
class CliCommandSpec(Generic[InputT]):
    command_path: str
    requires_auth: bool
    execute: Callable[[InputT, RuntimeContext], CommandResult]
```

In `commands.py`, add an initial registry mapping command paths to the existing
dispatch path. During migration, specs may wrap existing handlers.

```python
def command_paths() -> list[str]:
    return sorted(PUBLIC_COMMAND_PATHS)
```

Start with a constant set equal to all known command paths, then replace it with
real specs as groups migrate.

**Step 4: Update contract tests to know command path**

In `contract_tests/test_cli_conformance.py`, keep low-level dispatch tests for
API parity, but add one runtime-level test for the JSON envelope if practical.

**Step 5: Run tests**

Run:

```bash
uv run pytest cli/tests/test_cli_output_contract.py contract_tests/test_cli_conformance.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add cli/track_anywhere_cli/runtime.py cli/track_anywhere_cli/commands.py cli/tests/test_cli_output_contract.py contract_tests/test_cli_conformance.py
git commit -m "Add a typed registry for public CLI command paths"
```

Lore trailers:

```text
Confidence: medium
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli_output_contract.py contract_tests/test_cli_conformance.py -q
```

## Task 10: Remove Direct Business Output Calls

**Files:**

- Modify: `cli/track_anywhere_cli/click_common.py`
- Modify: `cli/track_anywhere_cli/click_app.py`
- Modify: `cli/track_anywhere_cli/config.py`
- Modify: `cli/track_anywhere_cli/renderers.py`
- Modify: `cli/tests/test_cli_output_architecture.py`

**Step 1: Write failing static guard**

In `cli/tests/test_cli_output_architecture.py`:

```python
FORBIDDEN = ["print(", "click.echo(", "Console("]
ALLOWLIST = {
    "cli/track_anywhere_cli/renderers.py",
    "cli/track_anywhere_cli/interaction.py",
}


def test_business_output_goes_through_output_boundary():
    root = Path("cli/track_anywhere_cli")
    offenders = []
    for path in root.glob("*.py"):
        if str(path) in ALLOWLIST:
            continue
        text = path.read_text()
        for token in FORBIDDEN:
            if token in text:
                offenders.append(f"{path}:{token}")

    assert offenders == []
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest cli/tests/test_cli_output_architecture.py -q
```

Expected: FAIL while any command/config module still prints directly.

**Step 3: Remove direct output**

Replace:

- `click.echo(str(exc), err=True)` in command paths with outcomes.
- direct warning `print()` in `config.py` with diagnostics.
- any direct `Console()` outside renderers or interaction.

Do not remove Click prompts from `interaction.py`.

**Step 4: Run tests**

Run:

```bash
uv run pytest cli/tests/test_cli_output_architecture.py cli/tests/test_cli.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add cli/track_anywhere_cli/click_common.py cli/track_anywhere_cli/click_app.py cli/track_anywhere_cli/config.py cli/track_anywhere_cli/renderers.py cli/tests/test_cli_output_architecture.py
git commit -m "Enforce the CLI output boundary"
```

Lore trailers:

```text
Confidence: high
Scope-risk: moderate
Tested: uv run pytest cli/tests/test_cli_output_architecture.py cli/tests/test_cli.py -q
```

## Task 11: Delete The Old Argparse Surface

**Files:**

- Delete: `cli/track_anywhere_cli/parser.py`
- Delete: `cli/track_anywhere_cli/parser_recurring.py`
- Modify: `cli/tests/test_cli_output_architecture.py`

**Step 1: Write failing import guard**

Add:

```python
def test_argparse_surface_removed():
    assert not Path("cli/track_anywhere_cli/parser.py").exists()
    assert not Path("cli/track_anywhere_cli/parser_recurring.py").exists()
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest cli/tests/test_cli_output_architecture.py::test_argparse_surface_removed -q
```

Expected: FAIL because parser files still exist.

**Step 3: Remove files**

Use `apply_patch` delete hunks or `rm` only after confirming no imports:

```bash
rg "build_parser|parser_recurring|from \\.parser|track_anywhere_cli.parser" cli contract_tests backend
```

Expected: no required imports. Then delete both files.

**Step 4: Run tests**

Run:

```bash
uv run pytest cli/tests/test_cli_output_architecture.py cli/tests -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add cli/track_anywhere_cli/parser.py cli/track_anywhere_cli/parser_recurring.py cli/tests/test_cli_output_architecture.py
git commit -m "Remove the duplicate argparse CLI surface"
```

Lore trailers:

```text
Rejected: Keep argparse as documentation | creates a stale second command contract
Confidence: high
Scope-risk: narrow
Tested: uv run pytest cli/tests/test_cli_output_architecture.py cli/tests -q
```

## Task 12: Update Documentation And Run Full Verification

**Files:**

- Modify: `README.md`
- Modify: `docs/plans/2026-05-19-cli-output-architecture-design.md` if implementation discovers a material design correction

**Step 1: Update README examples**

Document:

- default CLI output is Rich human output
- `--json` emits the envelope
- agents should read `data`, `diagnostics`, `ok`, `command`, and `status`
- example JSON:

```json
{
  "ok": true,
  "command": "account.list",
  "status": 200,
  "data": {
    "accounts": []
  },
  "diagnostics": []
}
```

**Step 2: Run focused CLI tests**

Run:

```bash
uv run pytest cli/tests contract_tests/test_cli_conformance.py -q
```

Expected: PASS.

**Step 3: Run broader tests**

Run:

```bash
uv run pytest -q
```

Expected: PASS. If unrelated dirty-worktree changes cause failures, record the
exact failing tests and inspect before deciding whether the failure belongs to
this implementation.

**Step 4: Inspect git diff**

Run:

```bash
git diff --stat
git diff -- cli/track_anywhere_cli cli/tests contract_tests README.md
```

Expected: diff is limited to CLI output architecture, tests, contract tests, and
README docs.

**Step 5: Final commit**

```bash
git add README.md docs/plans/2026-05-19-cli-output-architecture-design.md
git commit -m "Document the CLI output contract for users and agents"
```

Lore trailers:

```text
Confidence: high
Scope-risk: narrow
Tested: uv run pytest cli/tests contract_tests/test_cli_conformance.py -q
Tested: uv run pytest -q
```

## Final Acceptance Criteria

- Every CLI command supports `--json`.
- Every `--json` command emits the envelope:
  - `ok`
  - `command`
  - `status`
  - `data`
  - `diagnostics`
- Human mode uses Rich and does not print raw dictionaries.
- Expected warnings and errors are structured diagnostics.
- Auth login, auth status, dev token, and data backup use the same outcome path
  as API commands.
- `parser.py` and `parser_recurring.py` are removed or fully deprecated with no
  live imports.
- Static architecture tests reject direct business output outside output and
  interaction modules.
- `uv run pytest cli/tests contract_tests/test_cli_conformance.py -q` passes.
- `uv run pytest -q` passes or any unrelated failure is documented with exact
  evidence.
