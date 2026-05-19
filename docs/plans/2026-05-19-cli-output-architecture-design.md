# CLI Output Architecture Design

Status: approved
Date: 2026-05-19

## Context

Track Anywhere's CLI must serve two first-class audiences:

- humans running commands in a terminal
- agents and scripts calling commands as stable automation primitives

The current CLI already has a partial separation between Click command binding,
API command dispatch, and output rendering. That separation is useful, but the
output contract is not yet a real architecture boundary. Most commands pass raw
API payloads directly to the renderer. JSON mode dumps whatever object happens
to be returned. Human mode often prints Python-like dictionaries unless a
command has a custom Rich table. Some local flows, especially auth and token
handling, still write directly to stdout or stderr through `print` or
`click.echo`.

Because the project is still early, this is the right point to redesign the CLI
as a typed command framework instead of adding command-by-command formatting
patches.

## Goals

1. Make the CLI both human-friendly and agent-friendly by design.
2. Make `--json` a stable machine contract for every command.
3. Make default output a Rich human presentation for every command.
4. Prevent raw dictionaries or incidental internal data shapes from leaking to
   human output.
5. Route errors, warnings, prompts, and success messages through one output
   boundary.
6. Keep command execution testable without a terminal.
7. Remove duplicate CLI surfaces so future commands have one obvious path.

## Non-Goals

- Redesigning backend API response schemas.
- Changing ledger domain behavior.
- Adding a new CLI dependency beyond the existing Click and Rich stack.
- Keeping the old raw JSON output shape compatible. The new framework should
  prefer a cleaner long-term contract while the project is early enough to
  absorb the migration.

## Current Problems

The current implementation has these specific issues:

- `cli/track_anywhere_cli/renderers.py` mixes JSON dumping with a small number
  of command-specific Rich renderers.
- Most commands in human mode fall through to generic object printing.
- `click_common.run_api()` is a useful execution choke point, but it returns raw
  payloads directly to the renderer.
- Local commands in `click_app.py`, such as auth login, auth status, and data
  backup, branch manually between JSON and human output.
- `config.py` writes warnings directly to stderr, bypassing the output contract.
- Auth login mixes browser opening, prompts, stderr instructions, API exchange,
  token persistence, and result rendering in one function.
- `parser.py` and `parser_recurring.py` preserve an argparse command surface
  even though the active CLI uses Click, creating maintenance ambiguity.

## Considered Approaches

### Option A: Expand `renderers.py`

Add Rich renderers for every existing command and keep the rest of the CLI
structure mostly unchanged.

This is fast, but it does not fix scattered error handling, warnings, prompts,
or local command output. It also leaves JSON mode as a raw object dump.

### Option B: Add an Outcome Boundary

Introduce a shared `CliOutcome` object and route all commands through JSON or
Rich renderers. Keep most existing command modules intact.

This is safer than Option A and would solve much of the problem, but it still
leaves command definitions, execution, and presentation loosely connected.

### Option C: Typed CLI Command Framework

Make each command a typed spec with a command path, argument model, executor,
JSON contract, and human presenter. Click becomes the shell around this
framework rather than the place where behavior lives.

This is the recommended approach. It is a larger refactor, but it gives the CLI
a durable architecture while the project is still small enough to change
direction cleanly.

## Chosen Design

Use Option C: a typed CLI command framework.

The CLI should have four layers:

1. Click binding layer
   - Owns command names, options, arguments, and interactive input collection.
   - Does not render business output.
   - Does not decide JSON versus human presentation.

2. Command spec layer
   - Owns typed command definitions.
   - Declares `command_path`, input model, auth requirements, executor, and
     presenter key.
   - Converts Click values into typed command inputs.

3. Runtime layer
   - Owns the execution pipeline.
   - Resolves config and auth.
   - Runs command executors.
   - Converts API status and exceptions into `CliOutcome`.
   - Sends the outcome to the selected renderer.
   - Returns the process exit code.

4. Presentation layer
   - Owns JSON rendering and Rich human rendering.
   - Uses a presenter registry keyed by command path.
   - Provides explicit empty states, success summaries, tables, panels, and
     error presentations.

## Core Types

The runtime should pass structured outcomes across the output boundary.

```python
@dataclass(frozen=True)
class CliOutcome:
    command_path: str
    status: int
    data: Any
    diagnostics: list[CliDiagnostic]
    exit_code: int


@dataclass(frozen=True)
class CliDiagnostic:
    level: Literal["info", "warning", "error"]
    message: str
    code: str | None = None
    detail: Any | None = None
```

Command executors should return domain-neutral command results, not write to
stdout or stderr.

```python
@dataclass(frozen=True)
class CommandResult:
    status: int
    data: Any
    diagnostics: list[CliDiagnostic] = field(default_factory=list)
```

Interactive commands, such as browser login, should use an interaction channel
owned by the runtime.

```python
class Interaction:
    def open_url(self, url: str) -> None: ...
    def prompt(self, label: str, *, secret: bool = False) -> str: ...
```

The interaction channel may use Click internally, but command code should not
emit business output directly.

## JSON Contract

All commands with `--json` should return one envelope shape.

```json
{
  "ok": true,
  "command": "account.list",
  "status": 200,
  "data": {},
  "diagnostics": []
}
```

On failure:

```json
{
  "ok": false,
  "command": "account.list",
  "status": 401,
  "data": {
    "detail": "Not authenticated"
  },
  "diagnostics": [
    {
      "level": "error",
      "code": "auth_required",
      "message": "Authentication is required."
    }
  ]
}
```

Rules:

- stdout contains exactly one JSON document in JSON mode.
- stderr may be reserved for non-contract process failures, but expected
  warnings and command errors belong in the JSON diagnostics array.
- `data` contains the backend or local command payload.
- `command` is stable and uses dot notation, such as `tx.record`,
  `account.balance`, or `auth.login`.
- `ok` is derived from the exit code or HTTP status.
- Future machine clients should treat the envelope as the contract and the
  contents of `data` as command-specific payload.

## Human Output Contract

Default output should use Rich for every command.

Rules:

- Human output must never expose raw Python dictionaries by default.
- List commands should render tables.
- Show/detail commands should render compact panels or key-value tables.
- Mutating commands should render a success summary with the key object id and
  important follow-up fields.
- Empty lists should render explicit empty states.
- Warnings should appear as Rich warnings, not unstructured stderr lines.
- Errors should explain what happened and the next useful action when known.
- `--no-color` should continue to disable color while preserving layout.

The presenter registry should require every public command path to have either
a command-specific presenter or an explicit generic presenter decision.
Unknown command paths should fail tests.

## Command Registry

Introduce a command registry with command specs similar to:

```python
@dataclass(frozen=True)
class CliCommandSpec[InputT]:
    command_path: str
    input_type: type[InputT]
    requires_auth: bool
    execute: Callable[[InputT, RuntimeContext], CommandResult]
    presenter: str
```

The existing `command_catalog.py`, `command_ledger.py`,
`command_investment.py`, and `command_recurring.py` can be migrated into this
shape incrementally by command group.

Click modules should register command bindings, create input objects, and call
the runtime with the matching command spec.

## Auth And Local Commands

Auth and local commands must use the same runtime and output contract as API
commands.

Examples:

- `auth.status` returns `data.authenticated`, `data.base_url`, and
  `data.token_source`.
- `auth.login` returns `data.authenticated`, `data.token_saved`, and optional
  `data.scope`.
- `data.backup` returns backup metadata under `data.backup`.
- Token-store fallback warnings become diagnostics instead of direct stderr
  output.
- Insecure env-token warnings become diagnostics.

Browser login should separate the OAuth flow from presentation:

1. The executor creates the authorization URL.
2. The interaction channel opens the URL and prompts for a callback.
3. The executor exchanges the callback for a token.
4. The token store persists the token and returns diagnostics for storage
   warnings.
5. The renderer presents success or failure.

## Error Handling

Expected errors should be converted to outcomes:

- missing auth
- validation failures
- HTTP 400/401/403/404/409 responses
- token persistence warnings
- OAuth callback mismatch
- unsupported command paths

Unexpected exceptions should still fail loudly in tests. The CLI runtime may
render a compact user-facing message for production use, but it should not hide
programmer errors during test execution.

Exit codes remain meaningful:

- `0`: success
- `2`: validation or unsupported command
- `3`: policy denied
- `4`: idempotency conflict
- `5`: stale version
- `6`: auth failure
- `7`: security precondition failure
- `8`: not found

## Migration Plan

1. Add the new runtime, outcome, diagnostics, JSON renderer, and Rich presenter
   registry.
2. Add tests for the JSON envelope and human output rules.
3. Migrate auth and local commands first, because they currently bypass the
   output boundary most often.
4. Migrate one API command group at a time:
   - accounts and summaries
   - ledger transactions and balances
   - categories and credit cards
   - investments
   - recurring commands
5. Replace direct `print` and `click.echo` business output with diagnostics or
   interactions.
6. Remove or formally deprecate `parser.py` and `parser_recurring.py`.
7. Update contract tests so agent-facing CLI calls assert the new envelope.
8. Update README examples to document default Rich output and `--json` envelope
   output.

## Verification Strategy

Tests should enforce the architecture, not just examples.

- Every command supports `--json` and emits the envelope shape.
- Human mode for every command does not begin with `{` or `[`.
- Core commands have semantic Rich output assertions.
- Error paths produce structured diagnostics in JSON mode.
- Auth, validation, 403, 404, and 409 paths preserve exit codes.
- Static tests prohibit business output through `print`, `click.echo`, or
  ad-hoc `Console.print` outside the output and interaction modules.
- Registry tests ensure every public command path has a presenter.
- Contract tests use the same runtime path agents use, not a lower-level raw
  dispatch helper only.

## Risks

- The JSON envelope is a breaking change for existing CLI tests and any current
  scripts consuming raw payloads.
- A full framework refactor touches many command modules at once, so migration
  should happen group by group with tests running after each group.
- Rich output can become inconsistent if generic fallback rendering is too
  permissive. Tests should fail when a new command lacks an explicit presenter
  decision.
- Browser login has interaction side effects. The interaction channel should be
  injectable so tests do not open a real browser or block on prompts.

## Decision

Proceed with the typed CLI command framework. Treat human and agent output as a
cross-cutting product contract, not as per-command formatting. Prefer the new
JSON envelope despite the breaking change because the project is still early and
the long-term agent contract will be cleaner.
