# Track Anywhere V2 Event Ledger Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a greenfield PostgreSQL 17 V2 event ledger, switch API/CLI consumers to it, deterministically backfill the frozen V1 dump, prove replay/concurrency correctness, and delete the V1 runtime without deploying production.

**Architecture:** Financial facts are immutable, typed events ordered and hash-chained per Book. Catalog/workflow state remains CRUD; journal/posting/balance/reversal/current classification projections update in the append transaction, while slow projections consume per-Book checkpoints. Every financial command, receipt, event batch, and synchronous projection commits atomically.

**Tech Stack:** Python 3.12-3.13, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, PostgreSQL 17, psycopg 3, pytest, Click, Docker Compose.

---

## Execution rules

- Execute in an isolated `codex/` worktree created from the commit containing both the approved design and this implementation plan; never implement directly in the stable-backend checkout.
- Use `@test-driven-development` for every task: RED, verify the expected failure, GREEN with the minimum implementation, then refactor while green.
- Use only PostgreSQL 17 for persistence, migration, repository, concurrency, replay, and backfill tests. SQLite is allowed only for pure functions that do not open a database.
- Run the focused command after every step and commit after every task. Do not batch unrelated tasks into one commit.
- Never connect the V2 target writer to the live/stable database. Backfill source is a read-only restored dump; target is a separate database at the exact V2 Alembic head with zero business, event, receipt, quarantine, or seal rows.
- Do not deploy production. The terminal environment is isolated staging plus reports.

Before any PostgreSQL/API test throughout implementation, export only the three loopback PG17 cluster role URLs below: cluster admin for database lifecycle, migrator base login, and runtime base login. Task 1's fixture initially creates empty uniquely named databases; Task 6 upgrades it to create a migrated V2 database per test (and distinct source/target pairs for backfill tests). Every database has a non-login owner plus separate migrator/runtime logins. Fixtures derive child database DSNs from the base URLs, supply runtime DSNs through `TRACK_ANYWHERE_TEST_POSTGRES_URL` and `TRACK_ANYWHERE_DATABASE_URL`, and expose migrator DSNs only to migration fixtures. No test command may replace those fixture DSNs with a literal placeholder or let API/E2E run as the PostgreSQL owner. These credentials are disposable local/CI values bound to `127.0.0.1:15543`, never production credentials.

```bash
export TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE=1
export TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL=postgresql+psycopg://track_anywhere:track_anywhere@127.0.0.1:15543/postgres
export TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL=postgresql+psycopg://track_anywhere_migrator:track_anywhere_migrator_test@127.0.0.1:15543/postgres
export TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL=postgresql+psycopg://track_anywhere_runtime:track_anywhere_runtime_test@127.0.0.1:15543/postgres
```

## Frozen inputs and blocking gates

- Design: `docs/plans/2026-07-13-track-anywhere-v2-event-ledger-design.md`
- Pro review archive: `docs/reviews/2026-07-13-track-anywhere-v2-chatgpt-pro-review.md`
- V1 dump SHA-256: `a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e`
- V1 revision: `0019_posting_constraints`
- Reference counts: 121 accounts, 135 transactions, 284 postings, 43 transaction lines
- USDT: `ledger_scale=8`, online `input_scale=6`, `display_scale=6`; historical 8-decimal values are exact privileged imports.
- `global_sequence` is diagnostic only. No correctness code may checkpoint or replay by it.

## Phase 0: Freeze contracts and test infrastructure

### Task 1: Create the PostgreSQL 17 V2 test lane and repository guardrails

**Files:**
- Modify: `backend/AGENTS.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `compose.yaml`
- Modify: `compose.dev.yaml`
- Modify: `compose.e2e.yaml`
- Modify: `conftest.py`
- Modify: `backend/tests/conftest.py`
- Create: `docker/postgres/init/001-v2-roles.sh`
- Modify: `backend/app/track_anywhere/__init__.py`
- Create: `backend/tests/v2/conftest.py`
- Create: `backend/tests/v2/postgres_factory.py`
- Create: `backend/tests/v2/unit/test_repository_guardrails.py`
- Create: `backend/tests/v2/postgres/test_postgres_runtime.py`
- Create: `backend/tests/v2/postgres/test_database_factory.py`

**Step 1: Write the failing tests**

```python
# backend/tests/v2/unit/test_repository_guardrails.py
import tomllib
from pathlib import Path


def test_all_ledger_compose_services_use_postgres_17() -> None:
    for name in ("compose.yaml", "compose.dev.yaml", "compose.e2e.yaml"):
        text = Path(name).read_text(encoding="utf-8")
        assert "postgres:17-alpine" in text
        assert "postgres:16-alpine" not in text


def test_v2_does_not_reuse_a_postgres_16_data_volume() -> None:
    assert ".local/postgres17-data" in Path("compose.yaml").read_text(encoding="utf-8")
    assert "track-anywhere-v2-postgres17" in Path("compose.dev.yaml").read_text(encoding="utf-8")
    assert "postgres17-data" in Path("compose.e2e.yaml").read_text(encoding="utf-8")


def test_backend_agent_rules_name_v2_units_and_postgres_17() -> None:
    text = Path("backend/AGENTS.md").read_text(encoding="utf-8")
    assert "/api/v2" in text
    assert "integer units" in text
    assert "PostgreSQL 17" in text


def test_package_import_has_no_v1_service_side_effect() -> None:
    import track_anywhere
    assert not hasattr(track_anywhere, "FinanceService")


def test_python_support_range_is_finite_and_ci_testable() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12,<3.14"' in text


def test_pytest_is_in_uv_default_dev_group() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert "pytest>=8.4" in config["dependency-groups"]["dev"]
    assert "dev" not in config.get("project", {}).get("optional-dependencies", {})


def test_external_pg17_lane_cannot_be_overridden_to_sqlite() -> None:
    root = Path("conftest.py").read_text(encoding="utf-8")
    backend = Path("backend/tests/conftest.py").read_text(encoding="utf-8")
    assert "TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE" in root
    assert "TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE" in backend


def test_compose_provisions_distinct_migrator_and_runtime_roles() -> None:
    init = Path("docker/postgres/init/001-v2-roles.sh").read_text(encoding="utf-8")
    assert "TRACK_ANYWHERE_OWNER_ROLE" in init
    assert "TRACK_ANYWHERE_MIGRATOR_ROLE" in init
    assert "TRACK_ANYWHERE_RUNTIME_ROLE" in init
    assert "NOSUPERUSER" in init
```

```python
# backend/tests/v2/postgres/test_postgres_runtime.py
from sqlalchemy import text


def test_postgres_17_is_the_integration_runtime(pg_engine) -> None:
    with pg_engine.connect() as connection:
        version = connection.execute(text("show server_version_num")).scalar_one()
    assert 170000 <= int(version) < 180000
```

`backend/tests/v2/postgres_factory.py` and `conftest.py` must require the admin, migrator-base, and runtime-base URLs and prove they share the same loopback PG17 host/port while using three distinct login identities. They initially provide an admin-backed empty `postgres_database_factory` plus independent empty source/target pairs. Database names include the pytest worker/test UUID; cleanup terminates child connections and drops each database. The factory provisions a database owned by a non-login owner role, revokes default PUBLIC connect/create rights, and grants only necessary connection rights; the non-superuser migrator login may `SET ROLE` to that owner, while runtime has no membership. It derives child DSNs by replacing only the base URL's database component and returns admin, migrator, and restricted runtime DSNs; ordinary `pg_engine` uses runtime. The initial CLI supports `create --purpose NAME --schema empty --emit-role migrator|runtime`, `role-name --kind owner|migrator|runtime`, and `drop --url URL`; Task 6 adds `--schema v2` only after the V2 baseline exists. It prints only the requested DSN/role identifier on stdout. Missing/mismatched role URLs fail with a specific message rather than skip. `test_database_factory.py` proves lifecycle, per-test/per-worker isolation, role separation, runtime cannot create schemas/tables or `SET ROLE` owner, source/target separation, child-process visibility, and cleanup; it must not expect a V2 migration yet.

**Step 2: Run the tests and verify RED**

Run against the existing dependency layout: `uv run --extra dev pytest backend/tests/v2/unit/test_repository_guardrails.py -q`
Expected: FAIL because compose and backend rules still describe V1/PG16.

Start the isolated test database: `TRACK_ANYWHERE_E2E_POSTGRES_PORT=15543 docker compose -p track-anywhere-v2-test -f compose.e2e.yaml up -d --wait postgres`

Run with its admin URL against the existing optional-extra layout: `TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL=postgresql+psycopg://track_anywhere:track_anywhere@127.0.0.1:15543/postgres uv run --extra dev --extra postgres pytest backend/tests/v2/postgres/test_postgres_runtime.py backend/tests/v2/postgres/test_database_factory.py -q`
Expected: FAIL if the local compose service is still PG16.

Remove the RED database and its PG16 volume before GREEN: `docker compose -p track-anywhere-v2-test -f compose.e2e.yaml down -v --remove-orphans`

**Step 3: Make the minimum infrastructure change**

- Change the three development/test compose images to `postgres:17-alpine`. Give local/dev/E2E V2 new PG17-specific volume paths/names; never start PG17 on an existing PG16 data directory.
- Mount the executable `docker/postgres/init/001-v2-roles.sh` into fresh local/E2E clusters. It creates a non-login owner, a non-superuser migrator login that may `SET ROLE` owner, and a separate non-superuser runtime login with no owner membership, from environment-provided local/CI credentials. The runtime role has no database/schema creation or trigger-management authority. Never use these local credentials for production.
- Rewrite `backend/AGENTS.md` public boundary to `/api/v2`, exact integer-unit rules, clean-schema migrations, and mandatory PG17 integration gates.
- Make `track_anywhere/__init__.py` side-effect free; importing the package must not construct or export `FinanceService`.
- Pin the claimed Python support window to `>=3.12,<3.14`; Task 34 must test both claimed minors before expanding it.
- Move pytest from the optional `dev` extra to uv's default `[dependency-groups].dev`, regenerate `uv.lock`, and prove a fresh `uv sync --extra postgres` can run pytest without relying on an old `.venv`.
- Make root and backend legacy conftests honor `TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE=1` before applying any SQLite fallback. Keep their legacy behavior for unported V1 tests, but V2 collection/import must never see a SQLite DSN.

**Step 4: Verify GREEN**

Start the fresh PG17 E2E service again with `--wait`, run `uv sync --extra postgres`, then rerun the guardrail command without `--extra dev`. Run the PostgreSQL tests as `uv run --extra postgres pytest backend/tests/v2/postgres/test_postgres_runtime.py backend/tests/v2/postgres/test_database_factory.py -q`; pytest must now come from the default dev group.
Expected: PASS and server version in the 17 range.

**Step 5: Commit**

```bash
git add backend/AGENTS.md pyproject.toml uv.lock compose.yaml compose.dev.yaml compose.e2e.yaml conftest.py backend/tests/conftest.py docker/postgres/init/001-v2-roles.sh backend/app/track_anywhere/__init__.py backend/tests/v2
git commit -m "test: establish PostgreSQL 17 V2 lane"
```

### Task 2: Implement exact scaled units and USDT input policy

**Files:**
- Create: `backend/app/track_anywhere/domain/__init__.py`
- Create: `backend/app/track_anywhere/domain/money/__init__.py`
- Create: `backend/app/track_anywhere/domain/money/scaled_units.py`
- Create: `backend/app/track_anywhere/domain/money/asset_policy.py`
- Create: `backend/tests/v2/unit/test_scaled_units.py`
- Create: `backend/tests/v2/unit/test_asset_policy.py`

**Step 1: Write failing golden and property-style tests**

Cover:

```python
@pytest.mark.parametrize(
    ("raw", "scale", "units"),
    [("12.34", 2, 1234), ("12", 0, 12), ("0.00000001", 8, 1),
     ("9.126095", 6, 9126095), ("1.000000000000000000", 18, 10**18)],
)
def test_parse_exact_units(raw, scale, units):
    assert ScaledUnits.parse(raw, scale=scale, max_input_scale=scale).units == units
```

Also assert rejection of float input, exponent notation, signs, zero, `-0`, more than the allowed input scale, 39-digit units, NaN/Infinity, whitespace, and implicit rounding. Assert 38 digits are accepted and decoded exactly.

For USDT assert:

```python
def test_usdt_online_rejects_seven_decimals():
    with pytest.raises(AmountScaleExceeded):
        USDT_POLICY.parse_online("0.1234567")


def test_usdt_backfill_accepts_eight_decimals_exactly():
    assert USDT_POLICY.parse_backfill("0.12345678").units == 12_345_678
```

**Step 2: Verify RED**

Run: `uv run pytest backend/tests/v2/unit/test_scaled_units.py backend/tests/v2/unit/test_asset_policy.py -q`
Expected: collection/import failure because the V2 money package does not exist.

**Step 3: Implement the minimum domain code**

Use a strict decimal-string regex, string decomposition, and integer arithmetic. `Decimal` may be used as an independent test oracle, but no runtime payload or stored value depends on Decimal context:

```python
@dataclass(frozen=True, slots=True)
class ScaledUnits:
    units: int
    scale: int

    @classmethod
    def parse(cls, raw: str, *, scale: int, max_input_scale: int) -> "ScaledUnits":
        if not isinstance(raw, str) or not AMOUNT_PATTERN.fullmatch(raw):
            raise InvalidAmountFormat(raw)
        whole, dot, fraction = raw.partition(".")
        if len(fraction) > max_input_scale:
            raise AmountScaleExceeded(raw)
        units = int(whole) * 10**scale + int((fraction + "0" * scale)[:scale] or "0")
        if units <= 0 or len(str(units)) > 38:
            raise AmountOutOfRange(raw)
        return cls(units=units, scale=scale)
```

`AssetPolicy.parse_online()` uses `input_scale`; `parse_backfill()` uses `ledger_scale`. `decode()` returns a canonical decimal string without exponent notation.

**Step 4: Verify GREEN**

Run: `uv run pytest backend/tests/v2/unit/test_scaled_units.py backend/tests/v2/unit/test_asset_policy.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/domain backend/tests/v2/unit/test_scaled_units.py backend/tests/v2/unit/test_asset_policy.py
git commit -m "feat: add exact scaled unit policy"
```

### Task 3: Define one journal validator and accounting command model

**Files:**
- Create: `backend/app/track_anywhere/domain/journal/__init__.py`
- Create: `backend/app/track_anywhere/domain/journal/models.py`
- Create: `backend/app/track_anywhere/domain/journal/commands.py`
- Create: `backend/app/track_anywhere/domain/journal/validators.py`
- Create: `backend/tests/v2/unit/test_journal_validator.py`
- Create: `backend/tests/v2/unit/test_fx_validator.py`

**Step 1: Write failing tests**

Create typed `PostingDraft`, `PostTransaction`, `PostingSide`, and `TransactionKind` fixtures. Tests must reject fewer than two postings, duplicate posting IDs/positions, nonpositive units, cross-Book accounts, account/asset mismatch, per-Asset imbalance, and FX without both trading legs.

```python
def test_each_asset_must_balance_independently() -> None:
    command = post_command(
        posting("cny-bank", "CNY", "credit", 70000),
        posting("usd-wallet", "USD", "debit", 10000),
    )
    with pytest.raises(UnbalancedAsset, match="CNY"):
        JournalValidator.validate(command, catalog=account_catalog())
```

Add a passing four-leg CNY/USD trading-account case.

**Step 2: Verify RED**

Run: `uv run pytest backend/tests/v2/unit/test_journal_validator.py backend/tests/v2/unit/test_fx_validator.py -q`
Expected: FAIL because the journal types and validator do not exist.

**Step 3: Implement one pure validator**

```python
class JournalValidator:
    @staticmethod
    def validate(command: PostTransaction, *, catalog: AccountCatalogSnapshot) -> None:
        if len(command.postings) < 2:
            raise TooFewPostings()
        totals: dict[str, int] = defaultdict(int)
        for posting in command.postings:
            account = catalog.require(posting.account_id)
            account.require_usable(command.book_id, posting.asset_code)
            totals[posting.asset_code] += posting.units if posting.side is DEBIT else -posting.units
        if unbalanced := {asset: units for asset, units in totals.items() if units != 0}:
            raise UnbalancedAsset(unbalanced)
        if command.kind is TransactionKind.FX:
            validate_fx_trading_legs(command, catalog)
```

No database or projection balance lookup is allowed in this module.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/domain/journal backend/tests/v2/unit/test_journal_validator.py backend/tests/v2/unit/test_fx_validator.py
git commit -m "feat: define V2 journal invariants"
```

### Task 4: Define typed event contracts and PII-minimized payloads

**Files:**
- Create: `backend/app/track_anywhere/domain/journal/events.py`
- Create: `backend/app/track_anywhere/domain/reporting/__init__.py`
- Create: `backend/app/track_anywhere/domain/reporting/events.py`
- Create: `backend/app/track_anywhere/domain/investments/__init__.py`
- Create: `backend/app/track_anywhere/domain/investments/events.py`
- Create: `backend/app/track_anywhere/domain/privacy.py`
- Create: `backend/tests/v2/unit/test_event_contracts.py`
- Create: `backend/tests/v2/unit/test_event_privacy.py`

**Step 1: Write failing schema tests**

Assert exact `event_type`/`schema_version`, complete ordered postings for post/reversal, a typed `FinancialExternalReferenceCorrected.v1`, replace-all reporting lines, fixed lot allocations, and absence of raw memo, merchant name, attachment name/content, credential, or idempotency key fields.

```python
def test_journal_event_contains_units_as_strings() -> None:
    event = posted_event_fixture()
    payload = event.model_dump(mode="json")
    assert payload["postings"][0]["units"] == "70000"
    assert not isinstance(payload["postings"][0]["units"], int)
```

**Step 2: Verify RED**

Run: `uv run pytest backend/tests/v2/unit/test_event_contracts.py backend/tests/v2/unit/test_event_privacy.py -q`
Expected: import failure.

**Step 3: Implement strict Pydantic event payloads**

Use `ConfigDict(extra="forbid", frozen=True)`. Event payloads use stable IDs, enum facts, units strings, version IDs, and optional `description_ref`; they never accept arbitrary dictionaries.

**Step 4: Verify GREEN and freeze JSON Schemas**

Run the Step 2 command.
Expected: PASS.

Task 5 generates and freezes the JSON Schema files after these typed contracts pass.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/domain backend/tests/v2/unit/test_event_contracts.py backend/tests/v2/unit/test_event_privacy.py
git commit -m "feat: freeze typed V2 event contracts"
```

### Task 5: Implement canonical serialization, event registry, upcasters, and hash vectors

**Files:**
- Create: `backend/app/track_anywhere/serialization/__init__.py`
- Create: `backend/app/track_anywhere/serialization/canonical_json.py`
- Create: `backend/app/track_anywhere/serialization/event_registry.py`
- Create: `backend/app/track_anywhere/serialization/upcasters.py`
- Create: `backend/app/track_anywhere/serialization/generate_schemas.py`
- Create: `backend/app/track_anywhere/serialization/schemas/*.json`
- Create: `backend/tests/v2/unit/test_canonical_json.py`
- Create: `backend/tests/v2/unit/test_event_hash.py`
- Create: `backend/tests/v2/fixtures/event_hash_vectors.json`

**Step 1: Write failing golden tests**

Assert that key order does not change bytes/hash; timezone offsets canonicalize to UTC; Unicode is stable; envelope integers are accepted; floats/Decimal/arbitrary objects are rejected; one-bit changes in every hashed field alter the hash; `global_sequence` and `recorded_at` do not.

```python
def test_global_sequence_is_not_hashed() -> None:
    left = event_envelope(global_sequence=10)
    right = event_envelope(global_sequence=999)
    assert event_hash(left) == event_hash(right)
```

**Step 2: Verify RED**

Run: `uv run pytest backend/tests/v2/unit/test_canonical_json.py backend/tests/v2/unit/test_event_hash.py -q`
Expected: FAIL because serializer/registry do not exist.

**Step 3: Implement deterministic bytes and registry**

Use stdlib `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` after recursively proving the value is composed only of dict/list/str/int/bool/null, checking `bool` before `int`. Envelope positions and versions are integers; monetary units remain strings. Timestamps enter as fixed `YYYY-MM-DDTHH:MM:SS.ffffffZ` strings. Hash with a versioned domain separator and SHA-256.

**Step 4: Verify GREEN and schema reproducibility**

Run the Step 2 command.
Run: `uv run python -m track_anywhere.serialization.generate_schemas --check`
Expected: PASS and no schema diff.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/serialization backend/tests/v2/unit backend/tests/v2/fixtures/event_hash_vectors.json
git commit -m "feat: add canonical event codec"
```

## Phase 1: Build the clean V2 database

### Task 6: Reset Alembic to a clean V2 baseline and add engine configuration

**Files:**
- Delete: `alembic/versions/*.py` (all V1 revisions; Git remains the archive)
- Delete: `alembic_helpers/`
- Modify: `alembic.ini`
- Modify: `alembic/env.py`
- Modify: `Dockerfile`
- Create: `alembic/versions/v2_0001_schema_guard.py`
- Create: `backend/app/track_anywhere/infrastructure/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/base.py`
- Create: `backend/app/track_anywhere/infrastructure/db/engine.py`
- Modify: `backend/tests/v2/postgres_factory.py`
- Modify: `backend/tests/v2/conftest.py`
- Modify: `backend/tests/v2/postgres/test_database_factory.py`
- Create: `backend/tests/v2/postgres/test_v2_schema_guard.py`
- Create: `backend/tests/v2/postgres/test_migrated_database_factory.py`

**Step 1: Write the failing migration-boundary test**

Assert a new empty PG17 database upgrades to exactly `v2_0001_schema_guard` and records schema generation 2. Refuse any target schema containing a user table, sequence, materialized view, or view other than Alembic's own version object; include an unrelated random table fixture, not only known V1 names, and prove no V2 object is created after refusal. Assert Alembic refuses a missing DSN or `TRACK_ANYWHERE_DB_RUNTIME_ROLE`, validates the runtime role as a safe PostgreSQL identifier distinct from owner/migrator with no owner membership, honors `TRACK_ANYWHERE_DATABASE_URL` over config, runs through the migrator rather than admin/runtime role, and cannot silently use a checked-in localhost URL. Extend the Task 1 factory tests so `create --schema v2 --emit-role runtime` supplies that role name internally, returns a database at the V2 head, the runtime can read the generation marker but cannot alter/drop it, and migrated per-test/source/target fixtures clean up independently.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_v2_schema_guard.py backend/tests/v2/postgres/test_migrated_database_factory.py -q`
Expected: FAIL because Alembic still imports `track_anywhere.storage.Base`, has the V1 chain, and the factory does not yet support `--schema v2`.

**Step 3: Implement the clean baseline wiring**

Clear the credential-bearing/default `sqlalchemy.url` from `alembic.ini`. `alembic/env.py` requires both the explicit database URL and `TRACK_ANYWHERE_DB_RUNTIME_ROLE`, with the environment URL taking precedence; it validates the supplied identifier/role separation, imports `V2Base.metadata`, refuses runtime/admin session identity, derives the non-login owner from the database owner, and explicitly `SET ROLE` owner when invoked by migrator. `engine.py` rejects SQLite and exposes `require_postgres_17(connection)`. Delete the V1-only `alembic_helpers` and remove its Dockerfile `COPY` instruction in the same commit. The first immutable migration has `down_revision = None`, runs the completely-empty-target preflight, creates only a `v2_schema_metadata` generation marker, explicitly grants the supplied runtime role SELECT on `alembic_version` and the generation marker for readiness, and establishes schema/default privileges for future runtime DML without granting DDL/trigger authority. Extend `postgres_factory.py`/fixtures with `--schema v2`: migrate with the migrator DSN/owner role while supplying the runtime role name, return the requested role DSN, and default application/test engines to runtime. Later tasks add new migrations; no task edits an already committed migration.

**Step 4: Verify GREEN**

Run the Step 2 command.
Run:

```bash
set -euo pipefail
V2_SCHEMA_CHECK_URL=
cleanup_schema_check() {
  if [ -n "$V2_SCHEMA_CHECK_URL" ]; then uv run --extra postgres python backend/tests/v2/postgres_factory.py drop --url "$V2_SCHEMA_CHECK_URL" || true; fi
}
trap cleanup_schema_check EXIT
V2_SCHEMA_CHECK_URL="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py create --purpose schema-check --schema empty --emit-role migrator)"
export TRACK_ANYWHERE_DB_RUNTIME_ROLE="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py role-name --kind runtime)"
TRACK_ANYWHERE_DATABASE_URL="$V2_SCHEMA_CHECK_URL" uv run --extra postgres alembic upgrade head
TRACK_ANYWHERE_DATABASE_URL="$V2_SCHEMA_CHECK_URL" uv run --extra postgres alembic check
cleanup_schema_check
trap - EXIT
```

Expected: PASS on empty PG17; revision is `v2_0001_schema_guard`.

**Step 5: Commit**

```bash
git add -A alembic alembic_helpers
git add alembic.ini Dockerfile backend/app/track_anywhere/infrastructure backend/tests/v2/postgres_factory.py backend/tests/v2/conftest.py backend/tests/v2/postgres/test_database_factory.py backend/tests/v2/postgres/test_v2_schema_guard.py backend/tests/v2/postgres/test_migrated_database_factory.py
git commit -m "feat: reset schema to V2 baseline"
```

### Task 7: Add Book, Asset, Account, catalog-version, and privacy-sidecar tables

**Files:**
- Create: `alembic/versions/v2_0002_core_catalog.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/catalog.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/auth.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/privacy.py`
- Create: `backend/tests/v2/postgres/test_catalog_constraints.py`
- Create: `backend/tests/v2/postgres/test_auth_schema.py`

**Step 1: Write failing PostgreSQL constraint tests**

Test Book-scoped composite keys, one-asset accounts, unique trading roles, explicit Book write state (`active|paused_integrity`), unconditional `ledger_scale` immutability after Asset creation, immutable account Book/Asset/system role, soft-delete-only referenced rows, and current-name updates without changing accounting identity. Add clean-database tests for users, auth identities, password accounts, credentials, OAuth clients, authorization grants, device grants, session/revocation state, token hashes, expiry, and Book membership scope; secrets/tokens must never be stored in plaintext.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_catalog_constraints.py backend/tests/v2/postgres/test_auth_schema.py -q`
Expected: FAIL because catalog tables/constraints are absent.

**Step 3: Implement models and DDL**

Create `books`, `book_members`, `assets`, `accounts`, immutable category versions, authentication/credential/OAuth tables, and protected description sidecars. Add the partial unique index on `(book_id, asset_code, system_role)` and triggers that always block changes to Asset ledger scale and Account Book/Asset/system role. Grant the runtime role only the table-specific CRUD needed by catalog/auth commands; accounting catalogs use soft-delete updates and receive no physical DELETE privilege. Preserve only stable auth/security semantics; do not copy V1 storage facades. `models/__init__.py` imports every model module so Alembic metadata is complete.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS. Physical delete/reference races are added once event references exist in Task 9; close-vs-post serialization is added when both commands exist in Task 21.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0002_core_catalog.py backend/app/track_anywhere/infrastructure/db/models backend/tests/v2/postgres/test_catalog_constraints.py backend/tests/v2/postgres/test_auth_schema.py
git commit -m "feat: add immutable V2 catalogs"
```

### Task 8: Add event store, Book heads, stream versions, and command receipts

**Files:**
- Create: `alembic/versions/v2_0003_event_store.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/event_store.py`
- Create: `backend/tests/v2/postgres/test_event_store_constraints.py`
- Create: `backend/tests/v2/postgres/test_receipt_constraints.py`
- Create: `backend/tests/v2/postgres/test_event_store_permissions.py`

**Step 1: Write failing DDL tests**

Reject duplicate Book position, duplicate stream version, invalid hash length, non-object payload, zero schema version, duplicate receipt scope, and completed receipts missing response fields. Add a deferred receipt trigger proving `processing` cannot survive commit. Through the actual runtime DSN, prove `ledger_events` has SELECT/INSERT but not UPDATE/DELETE/TRUNCATE/ALTER or trigger-disable authority; through the migrator DSN prove migrations remain possible. Causation references cannot cross Books. Assert `global_sequence` gaps are allowed.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_event_store_constraints.py backend/tests/v2/postgres/test_receipt_constraints.py backend/tests/v2/postgres/test_event_store_permissions.py -q`
Expected: FAIL because the tables are absent.

**Step 3: Implement migration and models**

Use `event_id` as the primary key, unique `(book_id, event_id)`, `(book_id, book_position)`, and `(book_id, stream_type, stream_id, stream_version)`, plus a separate unique diagnostic sequence. Causation uses a composite Book FK. Add immutable-row triggers that reject `UPDATE` and `DELETE`; explicitly revoke UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER privileges on `ledger_events` from runtime and grant only SELECT/INSERT. Grant the runtime role the minimum SELECT/INSERT/UPDATE required for Book heads, stream heads, and receipts; it never owns tables. Receipt primary key is actor subject + Book + operation + key hash; raw keys never enter the model. A `DEFERRABLE INITIALLY DEFERRED` receipt constraint rejects any transaction that tries to commit a `processing` row.

**Step 4: Verify GREEN**

Run the Step 2 command, then:

```bash
set -euo pipefail
V2_ALEMBIC_CHECK_URL=
cleanup_event_store_check() {
  if [ -n "$V2_ALEMBIC_CHECK_URL" ]; then uv run --extra postgres python backend/tests/v2/postgres_factory.py drop --url "$V2_ALEMBIC_CHECK_URL" || true; fi
}
trap cleanup_event_store_check EXIT
V2_ALEMBIC_CHECK_URL="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py create --purpose event-store-check --schema v2 --emit-role migrator)"
export TRACK_ANYWHERE_DB_RUNTIME_ROLE="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py role-name --kind runtime)"
TRACK_ANYWHERE_DATABASE_URL="$V2_ALEMBIC_CHECK_URL" uv run --extra postgres alembic check
cleanup_event_store_check
trap - EXIT
```
Expected: PASS with no generated diff.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0003_event_store.py backend/app/track_anywhere/infrastructure/db/models/__init__.py backend/app/track_anywhere/infrastructure/db/models/event_store.py backend/tests/v2/postgres
git commit -m "feat: add V2 event store schema"
```

### Task 9: Add synchronous journal, balance, reversal, and reporting projections

**Files:**
- Create: `alembic/versions/v2_0004_sync_projections.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/projections.py`
- Create: `backend/tests/v2/postgres/test_journal_projection_constraints.py`
- Create: `backend/tests/v2/postgres/test_deferred_balance_trigger.py`

**Step 1: Write failing transaction-level tests**

Within real PostgreSQL transactions test:

- one posting commits only when later completed to two balanced postings before commit;
- unbalanced per-Asset totals fail at commit;
- account/asset or cross-Book mismatch fails through a composite FK;
- duplicate posting position fails;
- duplicate reversal target fails;
- 38-digit posting units work and 39-digit units fail;
- 48-digit accumulated balances work and 49-digit overflow rolls back the event/projection transaction atomically;
- referenced account/asset accounting fields cannot mutate concurrently with posting insertion;
- transaction, posting, reversal, reporting, source-event, and causation links use composite Book FKs;
- a sync-required event cannot commit without a matching applied-event marker.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_journal_projection_constraints.py backend/tests/v2/postgres/test_deferred_balance_trigger.py -q`
Expected: FAIL before the projection schema exists.

**Step 3: Implement DDL and deferred triggers**

Add `journal_transactions`, `journal_postings`, `account_balances`, `transaction_reversals`, `transaction_external_references`, `reporting_lines`, and `synchronous_projection_applied_events`. The `DEFERRABLE INITIALLY DEFERRED` balance trigger groups affected transactions by asset and validates posting count and debit-minus-credit sum at commit. A second deferred trigger requires every event type registered as sync-required to have an applied-event marker before commit, structurally preventing a committed Journal event with no synchronous projection.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS. Explicitly assert the failure is raised by PostgreSQL at commit, not only by Python.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0004_sync_projections.py backend/app/track_anywhere/infrastructure/db/models/__init__.py backend/app/track_anywhere/infrastructure/db/models/projections.py backend/tests/v2/postgres
git commit -m "feat: enforce V2 journal projections"
```

### Task 10: Add per-Book projection checkpoints, dirty periods, failures, and outbox

**Files:**
- Create: `alembic/versions/v2_0005_async_projection_outbox.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/async_projection.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/outbox.py`
- Create: `backend/tests/v2/postgres/test_projection_checkpoint_constraints.py`
- Create: `backend/tests/v2/postgres/test_outbox_constraints.py`

**Step 1: Write failing schema tests**

Assert checkpoint identity includes Book and projector version, `last_book_position >= 0`, dirty periods are Book/projection/time-bucket scoped, failure rows retain source event identity, and outbox message IDs are unique with no exactly-once claim.

```python
def test_checkpoint_cannot_be_global_only(inspector):
    pk = inspector.get_pk_constraint("projection_checkpoints")["constrained_columns"]
    assert pk == ["projection_name", "projector_version", "book_id"]
```

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_projection_checkpoint_constraints.py backend/tests/v2/postgres/test_outbox_constraints.py -q`
Expected: FAIL because tables are absent.

**Step 3: Implement migration and models**

Do not create `last_global_position`. Store per-Book positions, lease/owner data only for worker coordination, versioned shadow generation state, dirty periods, projection failures, and transactional outbox messages.

**Step 4: Verify GREEN**

Run the Step 2 command, then:

```bash
set -euo pipefail
V2_ALEMBIC_CHECK_URL=
cleanup_async_schema_check() {
  if [ -n "$V2_ALEMBIC_CHECK_URL" ]; then uv run --extra postgres python backend/tests/v2/postgres_factory.py drop --url "$V2_ALEMBIC_CHECK_URL" || true; fi
}
trap cleanup_async_schema_check EXIT
V2_ALEMBIC_CHECK_URL="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py create --purpose async-schema-check --schema v2 --emit-role migrator)"
export TRACK_ANYWHERE_DB_RUNTIME_ROLE="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py role-name --kind runtime)"
TRACK_ANYWHERE_DATABASE_URL="$V2_ALEMBIC_CHECK_URL" uv run --extra postgres alembic check
cleanup_async_schema_check
trap - EXIT
```
Expected: PASS.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0005_async_projection_outbox.py backend/app/track_anywhere/infrastructure/db/models backend/tests/v2/postgres
git commit -m "feat: add per-Book projector schema"
```

## Phase 2: Implement atomic append and cross-process idempotency

### Task 11: Add the request-scoped V2 unit of work and catalog repositories

**Files:**
- Create: `backend/app/track_anywhere/infrastructure/db/unit_of_work.py`
- Create: `backend/app/track_anywhere/infrastructure/db/repositories/catalogs.py`
- Create: `backend/app/track_anywhere/infrastructure/db/repositories/auth.py`
- Create: `backend/app/track_anywhere/application/__init__.py`
- Create: `backend/app/track_anywhere/application/unit_of_work.py`
- Create: `backend/tests/v2/postgres/test_unit_of_work.py`
- Create: `backend/tests/v2/postgres/test_catalog_repositories.py`
- Create: `backend/tests/v2/postgres/test_auth_repositories.py`

**Step 1: Write failing repository tests**

Verify transaction commit/rollback, Book-scoped catalog loads, auth identity/credential/OAuth lifecycle and redaction, `FOR SHARE`/`FOR UPDATE` use where accounting identity can race, and no repository returns a global unscoped account or raw secret.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_unit_of_work.py backend/tests/v2/postgres/test_catalog_repositories.py backend/tests/v2/postgres/test_auth_repositories.py -q`
Expected: import failure.

**Step 3: Implement the minimum UoW**

```python
class SqlAlchemyUnitOfWork:
    def __enter__(self):
        self.session = self._session_factory()
        self.transaction = self.session.begin()
        self.transaction.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            return self.transaction.__exit__(exc_type, exc, tb)
        finally:
            self.session.close()
```

Repositories require `book_id` in every account/category lookup signature. They return immutable domain snapshots, not ORM rows.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/application backend/app/track_anywhere/infrastructure/db backend/tests/v2/postgres
git commit -m "feat: add V2 database unit of work"
```

### Task 12: Implement per-Book event append and stream optimistic concurrency

**Files:**
- Create: `backend/app/track_anywhere/infrastructure/db/event_store.py`
- Create: `backend/app/track_anywhere/application/event_batch.py`
- Create: `backend/tests/v2/postgres/test_event_append.py`
- Create: `backend/tests/v2/concurrency/test_book_append_concurrency.py`

**Step 1: Write failing append tests**

Cover initial zero hash, consecutive positions within a batch, previous hash linkage, continuous stream versions, expected-version conflicts, whole-batch rollback, and same-Book concurrent appends.

```python
def test_append_batch_locks_one_book_head(uow, event_store):
    result = event_store._append_batch(
        uow.session,
        book_id=BOOK_ID,
        expected_stream_versions={("investment_lot", LOT_ID): 0},
        events=[lot_acquired_event_fixture()],
    )
    assert result.positions == range(1, 2)
    assert result.terminal_hash == stored_event_hash(uow.session)
```

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_event_append.py backend/tests/v2/concurrency/test_book_append_concurrency.py -q`
Expected: FAIL because `PostgresEventStore` is absent.

**Step 3: Implement append under the Book head lock**

```python
head = session.execute(
    select(BookEventHead).where(BookEventHead.book_id == book_id).with_for_update()
).scalar_one()
for pending in events:
    head.last_position += 1
    stored = encode_and_hash(pending, book_position=head.last_position, previous_hash=head.last_hash)
    session.add(stored)
    head.last_hash = stored.event_hash
```

Use an async-only investment-lot event in raw event-store tests; Task 9 must reject committing a sync-required Journal event without its projection marker. Key expected versions by `(stream_type, stream_id)` so journal and reporting streams may share a UUID safely. Do not lock or query a global sequence. Use unique stream constraints as the final optimistic-concurrency guard. Keep `_append_batch()` infrastructure-private; application handlers are forbidden from importing it. Task 15 exposes the only runtime writer, `LedgerCommitter.append_and_project()`.

**Step 4: Verify GREEN**

Run the Step 2 command with at least two Python processes.
Expected: PASS; 100 same-Book appends have continuous Book positions/hash chain.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/infrastructure/db/event_store.py backend/app/track_anywhere/application/event_batch.py backend/tests/v2
git commit -m "feat: append events per Book"
```

### Task 13: Implement transactional command receipts and response replay

**Files:**
- Create: `backend/app/track_anywhere/infrastructure/db/command_receipts.py`
- Create: `backend/app/track_anywhere/application/idempotency.py`
- Create: `backend/app/track_anywhere/application/command_bus.py`
- Create: `backend/tests/v2/postgres/test_command_receipts.py`
- Create: `backend/tests/v2/concurrency/test_cross_process_idempotency.py`

**Step 1: Write failing concurrency tests**

Test 20 processes with the same key/payload, same key/different payload, handler exception, response loss, database connection termination before commit, connection loss after commit, deadlock retry, and current authorization denial before replay. Spawn a real child process, pause it after receipt reservation and after event insertion in separate cases, send `SIGKILL`, then prove PostgreSQL rolls back the whole transaction and a same-key retry succeeds exactly once.

Expected invariants:

- one event batch and one completed receipt for same payload;
- stable 409 for different payload;
- no committed `processing` receipt;
- replay returns the stored versioned result only after current authorization passes;
- raw key never appears in database or captured logs.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_command_receipts.py backend/tests/v2/concurrency/test_cross_process_idempotency.py -q`
Expected: FAIL; current V1 process-local lock cannot satisfy cross-process tests.

**Step 3: Implement the single-transaction protocol**

```python
def execute(command, *, raw_key, actor, authorize, handler, uow_factory):
    key_hash = sha256(raw_key.encode()).digest()
    with uow_factory() as uow:
        authorization_scope = authorize(
            uow.session,
            actor,
            command.book_id,
            lock_membership=True,
        )
        request_hash = hash_request(command, authorization_scope)
        receipt = reserve_or_lock(uow.session, actor.subject_id, command, key_hash)
        if receipt.exists:
            return receipt.replay_or_conflict(request_hash)
        result = handler(command, uow)
        complete_receipt(uow.session, result)
        return result
```

The current membership/authorization check happens inside the same transaction before any receipt body is read; the membership row is locked against concurrent revocation for the command decision. Reservation, handler execution, and receipt completion all use that transaction. This task proves the generic receipt protocol with a fake atomic handler; no financial handler ships until Task 15 connects the command bus to `LedgerCommitter` and enforces the Book-head lock. Do not add leases, takeover timeouts, or in-process `Condition` objects.

**Step 4: Verify GREEN**

Run repeatedly: `for i in {1..5}; do uv run --extra postgres pytest backend/tests/v2/postgres/test_command_receipts.py backend/tests/v2/concurrency/test_cross_process_idempotency.py -q || exit 1; done`.
Expected: all runs PASS with one event batch and no `processing` row.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/application backend/app/track_anywhere/infrastructure/db/command_receipts.py backend/tests/v2
git commit -m "feat: make commands transactionally idempotent"
```

### Task 14: Prove reverse commit order across Books cannot lose events

**Files:**
- Create: `backend/tests/v2/concurrency/test_cross_book_commit_order.py`
- Create: `backend/tests/v2/unit/test_no_global_checkpoint.py`
- Create: `backend/app/track_anywhere/infrastructure/projections/event_reader.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/async_projection.py`

**Step 1: Write the regression test**

Use two connections:

1. Book A allocates diagnostic sequence N and pauses before commit.
2. Book B allocates N+1 and commits.
3. A projector processes Book B.
4. Book A commits.
5. The projector resumes and must consume Book A position 1.

Also statically assert runtime projector/checkpoint modules do not contain `last_global_position` or order correctness queries by `global_sequence`.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/concurrency/test_cross_book_commit_order.py backend/tests/v2/unit/test_no_global_checkpoint.py -q`
Expected: FAIL until the per-Book consumer skeleton exists or if any global checkpoint remains.

**Step 3: Add the minimum per-Book event fetch contract**

Create a query that selects `book_position > checkpoint` for exactly one Book, ordered by Book position. Book discovery reads the `books` table; it does not infer completion from a global high-water mark.

**Step 4: Verify GREEN**

Run: `for i in {1..100}; do uv run --extra postgres pytest backend/tests/v2/concurrency/test_cross_book_commit_order.py backend/tests/v2/unit/test_no_global_checkpoint.py -q || exit 1; done` with the test choosing a recorded random pause seed per iteration.
Expected: PASS, zero missing or duplicated source event IDs.

**Step 5: Commit**

```bash
git add backend/tests/v2 backend/app/track_anywhere/infrastructure/projections/event_reader.py backend/app/track_anywhere/infrastructure/db/models/async_projection.py
git commit -m "test: prevent global checkpoint event loss"
```

## Phase 3: Implement financial commands and synchronous projections

### Task 15: Build the synchronous projection dispatcher

**Files:**
- Create: `backend/app/track_anywhere/infrastructure/projections/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/projections/synchronous.py`
- Create: `backend/app/track_anywhere/application/ledger_committer.py`
- Modify: `backend/app/track_anywhere/application/command_bus.py`
- Create: `backend/tests/v2/postgres/test_synchronous_projection.py`
- Create: `backend/tests/v2/postgres/test_command_bus_write_boundary.py`
- Create: `backend/tests/v2/replay/test_synchronous_replay.py`
- Create: `backend/tests/v2/unit/test_ledger_write_boundary.py`

**Step 1: Write failing tests**

Assert a posted event creates transaction/postings, applied-event marker, and exact balances in the same transaction; duplicate source event is rejected/idempotent; rollback removes event and projection; a new connection after commit sees the appended position and balance. Prove command-bus order is authorization/membership lock, receipt reservation, Book-head lock, state-dependent handler, append/projection, receipt completion, then commit; the Book-head lock remains held through every financial write. A dependency test rejects any application/API import of `PostgresEventStore` or `_append_batch`; only `LedgerCommitter` may coordinate the low-level append and sync dispatcher.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_synchronous_projection.py backend/tests/v2/postgres/test_command_bus_write_boundary.py backend/tests/v2/replay/test_synchronous_replay.py backend/tests/v2/unit/test_ledger_write_boundary.py -q`
Expected: FAIL because dispatcher is absent.

**Step 3: Implement typed dispatch**

Dispatch by registry type/version to small pure appliers. Change the Task 13 command bus so `LedgerCommitter.execute_under_book_lock()` locks and returns the Book head before a financial handler reads state that controls event construction; `append_and_project()` then calls the private append and dispatcher without releasing that lock, in the same UoW transaction, and writes the applied-event marker. Receipt completion remains after successful projection and before commit. Balance updates use `INSERT ... ON CONFLICT DO UPDATE` with exact signed deltas and source-event dedupe. No handler may issue a network call.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS and cold replay equals online projection.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/infrastructure/projections backend/app/track_anywhere/application/ledger_committer.py backend/app/track_anywhere/application/command_bus.py backend/tests/v2/postgres/test_synchronous_projection.py backend/tests/v2/postgres/test_command_bus_write_boundary.py backend/tests/v2/replay backend/tests/v2/unit/test_ledger_write_boundary.py
git commit -m "feat: project journal events synchronously"
```

### Task 16: Implement the Post Transaction command

**Files:**
- Create: `backend/app/track_anywhere/application/journal/__init__.py`
- Create: `backend/app/track_anywhere/application/journal/post_transaction.py`
- Create: `backend/tests/v2/postgres/test_post_transaction.py`
- Create: `backend/tests/v2/contract/test_post_transaction_result.py`

**Step 1: Write failing use-case tests**

Cover standard, opening, adjustment, and transfer commands; authorization; Book `paused_integrity` rejection; exact amount parsing; expected stream version; event construction; sync transaction/posting/balance projection; receipt response; closed account rejection; and a write from worker A immediately read by worker B.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_post_transaction.py backend/tests/v2/contract/test_post_transaction_result.py -q`
Expected: FAIL because the handler is absent.

**Step 3: Implement the command handler**

Through the Task 15 command-bus boundary, the handler first receives the locked Book head, then loads/locks Book-scoped account snapshots, invokes only `JournalValidator`, constructs one `JournalTransactionPosted.v1`, appends it, applies sync projections, and returns transaction ID plus `as_of_book_position`. It contains no duplicate balancing logic and never reads account open/closed state before the Book-head lock.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/application/journal backend/tests/v2/postgres/test_post_transaction.py backend/tests/v2/contract
git commit -m "feat: post V2 journal transactions"
```

### Task 17: Implement exact reversal and atomic correction

**Files:**
- Create: `backend/app/track_anywhere/application/journal/reverse_transaction.py`
- Create: `backend/app/track_anywhere/application/journal/correct_transaction.py`
- Create: `backend/tests/v2/postgres/test_reverse_transaction.py`
- Create: `backend/tests/v2/concurrency/test_concurrent_reversal.py`

**Step 1: Write failing tests**

Test exact inverse side/units/asset/order, original event ID/hash provenance, one reversal target, cross-Book rejection, reversal-of-reversal rules, no cycles, 20 concurrent reversals, and correction rollback when replacement validation fails.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_reverse_transaction.py backend/tests/v2/concurrency/test_concurrent_reversal.py -q`
Expected: FAIL because reversal handlers do not exist.

**Step 3: Implement server-derived compensation**

Load the original stored event, create inverse postings on the server, and append the reversal under the same Book head lock. `CorrectTransaction` appends reversal plus replacement as one batch and one receipt transaction.

**Step 4: Verify GREEN**

Run the Step 2 command five times.
Expected: PASS; one concurrent reversal succeeds and correction is all-or-nothing.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/application/journal backend/tests/v2
git commit -m "feat: reverse and correct V2 transactions"
```

### Task 18: Implement Reporting Line revisions and financial reference correction

**Files:**
- Create: `backend/app/track_anywhere/application/journal/assign_reporting_lines.py`
- Create: `backend/app/track_anywhere/application/journal/clear_reporting_lines.py`
- Create: `backend/app/track_anywhere/application/journal/correct_external_reference.py`
- Create: `backend/tests/v2/postgres/test_reporting_line_commands.py`
- Create: `backend/tests/v2/replay/test_reporting_line_replay.py`
- Create: `backend/tests/v2/postgres/test_external_reference_correction.py`

**Step 1: Write failing tests**

Test revision starts at 1, stale expected revision conflict, replace-all semantics, clear event, immutable category version reference, over-allocation rejection, classification changes leaving balances byte-for-byte unchanged, and deterministic replay. For external references, permit only typed provider/kind/reference corrections, keep prior values in history, reject cross-Book/unknown transaction changes, and prove the correction cannot alter postings or balances.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_reporting_line_commands.py backend/tests/v2/postgres/test_external_reference_correction.py backend/tests/v2/replay/test_reporting_line_replay.py -q`
Expected: FAIL.

**Step 3: Implement assignment/clear handlers**

Use exact units, stable catalog/version IDs, normalized enum dimensions, and `description_ref`. Apply the full current set in the sync projection transaction. External-reference correction appends the typed correction event and updates only its synchronous reference projection.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS; balance rows and Book balance hash are unchanged by reclassification.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/application/journal backend/tests/v2
git commit -m "feat: version V2 reporting lines"
```

### Task 19: Implement explicit FX and freeze investment lot contracts

**Files:**
- Create: `alembic/versions/v2_0006_investment_lot_projection.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/investments.py`
- Create: `backend/app/track_anywhere/application/journal/record_fx.py`
- Create: `backend/app/track_anywhere/application/investments/__init__.py`
- Create: `backend/app/track_anywhere/application/investments/acquire_lot.py`
- Create: `backend/app/track_anywhere/application/investments/dispose_lot.py`
- Create: `backend/tests/v2/postgres/test_record_fx.py`
- Create: `backend/tests/v2/postgres/test_investment_lot_projection.py`
- Create: `backend/tests/v2/unit/test_lot_allocation.py`
- Create: `backend/tests/v2/concurrency/test_concurrent_lot_disposal.py`
- Create: `backend/tests/v2/replay/test_lot_replay.py`

**Step 1: Write failing tests**

FX tests require the four trading legs and derive a display rate from integer quantities. Lot tests cover acquisition, FIFO selection at command time, Specific ID, disposal quantity, cost basis, fees, and replay using the stored allocation rather than rerunning FIFO. Start 20 processes disposing the same limited holding: valid requests may consume each acquisition unit at most once, over-disposal must fail after waiting for the winning transaction, and no pair of committed events may allocate the same lot units twice.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_record_fx.py backend/tests/v2/postgres/test_investment_lot_projection.py backend/tests/v2/unit/test_lot_allocation.py backend/tests/v2/concurrency/test_concurrent_lot_disposal.py backend/tests/v2/replay/test_lot_replay.py -q`
Expected: FAIL.

**Step 3: Implement minimum handlers and contracts**

Record FX as one balanced journal event with exact Asset legs. Add rebuildable `investment_lots` and `investment_lot_allocations` projection tables, then implement lot command/event state and replay reducer. Disposal runs through `LedgerCommitter.execute_under_book_lock()`: acquire the Book head first, rebuild/read authoritative lot availability from the Book's investment events under that lock, freeze the exact allocations into the disposal event, then append and synchronously project before releasing the lock. Never use the asynchronous lot projection to decide allocation. Do not expose UI or advanced return reports.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS with no float in payload/model/database paths.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0006_investment_lot_projection.py backend/app/track_anywhere/application backend/app/track_anywhere/infrastructure/db/models/__init__.py backend/app/track_anywhere/infrastructure/db/models/investments.py backend/tests/v2
git commit -m "feat: add V2 FX and lot contracts"
```

## Phase 4: Expose V2 queries, API, CLI, and contracts

### Task 20: Replace the API module with a V2 package and fail-closed readiness

**Files:**
- Delete: `backend/app/track_anywhere/api.py`
- Create: `backend/app/track_anywhere/api/__init__.py`
- Create: `backend/app/track_anywhere/api/app.py`
- Create: `backend/app/track_anywhere/api/dependencies.py`
- Create: `backend/app/track_anywhere/api/errors.py`
- Create: `backend/app/track_anywhere/api/v2/__init__.py`
- Create: `backend/app/track_anywhere/api/v2/router.py`
- Create: `backend/app/track_anywhere/api/v2/system.py`
- Create: `backend/app/track_anywhere/api/v2/auth.py`
- Modify: `backend/app/track_anywhere/auth_oauth.py`
- Modify: `backend/app/track_anywhere/platform_auth.py`
- Modify: `backend/app/track_anywhere/platform_auth_metadata.py`
- Modify: `backend/app/track_anywhere/platform_auth_models.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/v2/contract/test_v2_app.py`
- Create: `backend/tests/v2/contract/test_v2_auth_api.py`
- Create: `backend/tests/v2/postgres/test_v2_readiness.py`

**Step 1: Write failing API tests**

Assert system and auth routes are under `/api/v2`, app import remains `track_anywhere.api:app`, startup does not run Alembic, readiness checks PG17 and exact Alembic head, stale/missing schema returns 503, and login/session/credential flows preserve existing security properties without a V1 router.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/contract/test_v2_app.py backend/tests/v2/contract/test_v2_auth_api.py backend/tests/v2/postgres/test_v2_readiness.py -q`
Expected: FAIL because the current app is a V1 module and auto-migration path.

**Step 3: Build the V2 composition root**

Reuse proven auth/security primitives and the Task 7/11 V2 auth tables/repositories behind a new V2 auth router and dependencies. Update OAuth discovery/callback/device paths to `/api/v2` in this task so clean-database auth tests are self-contained. Construct V2 engine/UoW/command bus explicitly from the runtime DSN; startup/readiness must reject an admin/migrator identity just as they reject SQLite or PG16. Migration services alone receive the migrator DSN. Preserve CSRF/origin/cookie/token redaction tests. Do not include the V1 `api_routers` package or instantiate `FinanceService`, `OrmStorage`, hydration, or cache.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS; importing the app performs no migration or full-database read.

**Step 5: Commit**

```bash
git add -A backend/app/track_anywhere/api.py backend/app/track_anywhere/api
git add backend/app/main.py backend/app/track_anywhere/auth_oauth.py backend/app/track_anywhere/platform_auth.py backend/app/track_anywhere/platform_auth_metadata.py backend/app/track_anywhere/platform_auth_models.py backend/tests/v2
git commit -m "feat: compose the V2 API"
```

### Task 21: Add V2 catalog and financial command endpoints

**Files:**
- Create: `backend/app/track_anywhere/api/v2/schemas.py`
- Create: `backend/app/track_anywhere/api/v2/catalogs.py`
- Create: `backend/app/track_anywhere/api/v2/journal.py`
- Create: `backend/app/track_anywhere/api/v2/reporting.py`
- Create: `backend/app/track_anywhere/api/v2/investments.py`
- Modify: `backend/app/track_anywhere/api/v2/router.py`
- Create: `backend/app/track_anywhere/application/catalogs/__init__.py`
- Create: `backend/app/track_anywhere/application/catalogs/create_book.py`
- Create: `backend/app/track_anywhere/application/catalogs/create_asset.py`
- Create: `backend/app/track_anywhere/application/catalogs/create_account.py`
- Create: `backend/app/track_anywhere/application/catalogs/close_account.py`
- Create: `backend/app/track_anywhere/application/catalogs/create_category.py`
- Create: `backend/tests/v2/contract/test_v2_catalog_api.py`
- Create: `backend/tests/v2/contract/test_v2_journal_api.py`
- Create: `backend/tests/v2/contract/test_v2_idempotency_api.py`
- Create: `backend/tests/v2/postgres/test_catalog_commands.py`
- Create: `backend/tests/v2/concurrency/test_account_close_post_race.py`

**Step 1: Write failing contract tests**

Assert decimal strings are required, JSON numbers/floats/exponents are rejected, financial writes require `X-Idempotency-Key`, replay response is stable, different payload gives 409, authorization errors do not reveal receipt data, and responses include `as_of_book_position`. Catalog command tests prove Book creation and its zero-position/zero-hash `book_event_heads` row commit atomically; a forced failure leaves neither row. They also cover Book-scoped Asset, Account, category-version creation, and Account close through application handlers rather than route-level ORM writes. Race 20 close attempts against 20 posts to one account: Book-head serialization must make every committed posting precede the close, while every post ordered after the close fails; physical deletion of a referenced account always fails.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/contract/test_v2_catalog_api.py backend/tests/v2/contract/test_v2_journal_api.py backend/tests/v2/contract/test_v2_idempotency_api.py backend/tests/v2/postgres/test_catalog_commands.py backend/tests/v2/concurrency/test_account_close_post_race.py -q`
Expected: 404/import failures.

**Step 3: Implement thin routes and strict schemas**

Implement explicit catalog application handlers with Book-scoped authorization and invariant checks. `CreateBook` inserts the Book, initial owner membership, and zero-position/zero-hash `book_event_heads` row in one UoW transaction. `CloseAccount` takes the same Book-head lock used by financial commands before locking/updating the account; posting handlers load account state only after acquiring that Book lock, giving one serialization order. Routes parse headers/body, obtain auth and UoW dependencies, call application handlers, and map typed domain errors. No route writes ORM rows directly or reimplements amount/balancing rules.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/api/v2 backend/app/track_anywhere/application/catalogs backend/tests/v2/contract backend/tests/v2/postgres/test_catalog_commands.py backend/tests/v2/concurrency/test_account_close_post_race.py
git commit -m "feat: expose V2 ledger commands"
```

### Task 22: Add stable journal, balance, and as-of queries without process cache

**Files:**
- Create: `backend/app/track_anywhere/queries/__init__.py`
- Create: `backend/app/track_anywhere/queries/journal.py`
- Create: `backend/app/track_anywhere/queries/balances.py`
- Create: `backend/app/track_anywhere/queries/reporting.py`
- Create: `backend/app/track_anywhere/api/v2/queries.py`
- Create: `backend/tests/v2/postgres/test_journal_queries.py`
- Create: `backend/tests/v2/concurrency/test_cross_worker_read_visibility.py`
- Create: `backend/tests/v2/contract/test_v2_query_api.py`

**Step 1: Write failing query tests**

Cover cursor pagination ordered by `(effective_at, book_position, transaction_id)`, `as_of_book_position`, current balance projection vs posting reference aggregation, reversed status, closed-account historical reads, and worker A write immediately visible to worker B.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_journal_queries.py backend/tests/v2/concurrency/test_cross_worker_read_visibility.py backend/tests/v2/contract/test_v2_query_api.py -q`
Expected: FAIL.

**Step 3: Implement projection-backed read services**

Queries execute against PostgreSQL per request and return explicit Book positions. Do not add full-Book caches, startup snapshots, or transaction-count versions.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS across distinct engine/session instances.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/queries backend/app/track_anywhere/api/v2/queries.py backend/tests/v2
git commit -m "feat: query V2 ledger projections"
```

### Task 23: Switch CLI, frontend proxy, OpenAPI, and E2E contract to V2

**Files:**
- Modify: `cli/track_anywhere_cli/http.py`
- Modify: `cli/track_anywhere_cli/click_auth.py`
- Modify: `cli/track_anywhere_cli/click_system.py`
- Modify: `cli/track_anywhere_cli/commands.py`
- Modify: `cli/track_anywhere_cli/config.py`
- Modify: `cli/track_anywhere_cli/data_backup.py`
- Modify: `cli/track_anywhere_cli/protocol.py`
- Modify: `cli/track_anywhere_cli/presenters.py`
- Modify: `cli/track_anywhere_cli/presenter_catalog.py`
- Modify: `cli/track_anywhere_cli/presenter_operations.py`
- Modify: `cli/track_anywhere_cli/command_ledger.py`
- Modify: `cli/track_anywhere_cli/command_catalog.py`
- Modify: `cli/track_anywhere_cli/command_investment.py`
- Modify: `cli/track_anywhere_cli/command_payment.py`
- Modify: `cli/track_anywhere_cli/command_recurring.py`
- Modify: `cli/track_anywhere_cli/command_system.py`
- Modify: `cli/track_anywhere_cli/oauth_login.py`
- Delete: `cli/track_anywhere_cli/posting_semantics.py`
- Delete: `frontend/app/api/v1/[...path]/route.ts`
- Create: `frontend/app/api/v2/[...path]/route.ts`
- Modify: `frontend/README.md`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/app/auth/auth-form.tsx`
- Modify: `frontend/app/auth/callback/cli-callback.tsx`
- Modify: `frontend/app/components/auth-console.tsx`
- Modify: `frontend/app/components/auth-header.tsx`
- Modify: `frontend/app/components/auth-provider.tsx`
- Modify: `scripts/deploy-vps.sh`
- Modify: `scripts/e2e-docker-postgres.sh`
- Modify: `scripts/stable-smoke.sh`
- Modify: `scripts/start-stable-local.sh`
- Modify: `Dockerfile`
- Create: `backend/tests/snapshots/public-api-v2.json`
- Delete: `backend/tests/snapshots/public-api-v1.json`
- Modify: `contract_tests/api_clients.py`
- Modify: `contract_tests/conftest.py`
- Modify: `contract_tests/cli_clients.py`
- Modify: `contract_tests/helpers.py`
- Modify: `contract_tests/README.md`
- Modify: `contract_tests/test_api_conformance.py`
- Modify: `contract_tests/test_cli_conformance.py`
- Modify: `cli/tests/test_cli_ledger.py`
- Modify: all other `cli/tests/test_*.py` files whose routes/contracts change
- Create: `backend/tests/v2/contract/test_public_api_v2_snapshot.py`
- Create: `docs/operations/v2-client-capability-matrix.md`

**Step 1: Write failing boundary tests**

Before changing routes, classify every CLI/frontend capability as V2-implemented, explicitly deferred, or removed, including payment, recurring, system, investment, backup, and auth flows. Then assert no runtime client path uses `/api/v1`; CLI sends amount strings and idempotency keys unchanged; V2 OpenAPI contains only approved routes; frontend proxy and Docker healthcheck target `/api/v2`; E2E smoke uses V2 readiness and post/query/reverse/classify flows. Assert `contract_tests/conftest.py` never installs a SQLite DSN and instead consumes the V2 per-test PG17 runtime URL before importing the app/client.

**Step 2: Verify RED**

Run: `rg -n '/api/v1' cli frontend scripts contract_tests backend/tests/snapshots`
Expected: many runtime matches.

Run: `uv run --extra postgres pytest backend/tests/v2/contract cli/tests contract_tests -q`
Expected: FAIL against the V1 paths/contracts.

**Step 3: Switch every client boundary together**

Keep CLI presentation/transport code where useful, but remove every import/use of `posting_semantics.py` from the listed CLI modules before deleting it. Implement or remove each command according to the reviewed client capability matrix. Rework contract fixtures/API clients in this task so app construction happens only after `postgres_database_factory` supplies a runtime-role V2 database; do not defer that SQLite removal to V1 retirement. Generate the reviewed V2 OpenAPI snapshot only after route tests pass. Pin the frontend's supported runtime to Node 22 in `package.json`/lock metadata. Change the Dockerfile healthcheck to `/api/v2/health`.

**Step 4: Verify GREEN**

Run the Step 2 pytest command.
Run: `npm --prefix frontend ci`
Run: `npm --prefix frontend run lint && npm --prefix frontend run build`
Run: `rg -n '/api/v1' cli/track_anywhere_cli frontend/app backend/app/track_anywhere/auth_oauth.py backend/app/track_anywhere/platform_auth*.py scripts contract_tests Dockerfile`
Expected: pytest/lint/build PASS and no runtime matches in migrated client/auth boundaries. Old `backend/app/track_anywhere/api_routers` hits remain only until their explicit deletion in Task 33.

**Step 5: Commit**

```bash
git add cli frontend scripts contract_tests backend/tests/snapshots backend/tests/v2/contract docs/operations/v2-client-capability-matrix.md Dockerfile
git commit -m "feat: switch clients to API V2"
```

## Phase 5: Build asynchronous projections, rebuilds, outbox, and observability

### Task 24: Implement the per-Book asynchronous projector and dirty-period semantics

**Files:**
- Create: `alembic/versions/v2_0007_monthly_category_summary.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/monthly_summary.py`
- Create: `backend/app/track_anywhere/infrastructure/projections/worker.py`
- Create: `backend/app/track_anywhere/infrastructure/projections/checkpoints.py`
- Create: `backend/app/track_anywhere/infrastructure/projections/dirty_periods.py`
- Create: `backend/app/track_anywhere/infrastructure/projections/monthly_summary.py`
- Create: `backend/tests/v2/postgres/test_async_projector.py`
- Create: `backend/tests/v2/concurrency/test_async_projector_crash.py`
- Create: `backend/tests/v2/replay/test_late_effective_events.py`

**Step 1: Write failing worker tests**

Use a monthly category summary as the first real async projection. Test per-Book position fetch, checkpoint+projection atomicity, duplicate delivery, crash before/after checkpoint, unknown schema fail-closed, Book A/Book B parallelism, and late effective events. First build July, then append a January transaction and a February reversal at later Book positions; January/February results must converge exactly to cold replay without changing unrelated July output:

```python
def test_late_event_rebuilds_old_period(projector, july_report):
    append_transaction(effective_at="2026-01-10T00:00:00Z")
    projector.run_once(BOOK_ID)
    assert report("2026-01") == cold_replay_report("2026-01")
    assert report("2026-07") == july_report
```

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_async_projector.py backend/tests/v2/concurrency/test_async_projector_crash.py backend/tests/v2/replay/test_late_effective_events.py -q`
Expected: FAIL because the worker is absent.

**Step 3: Implement one-Book transactional batches**

Lock `(projection_name, version, book_id)`, read events by `book_position`, apply idempotently, mark dirty periods from `effective_at`, and advance the Book checkpoint in the same transaction. Unknown events write a failure row and pause that Book projection.

**Step 4: Verify GREEN**

Run the Step 2 command with randomized worker termination.
Expected: PASS and output equal to cold replay.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0007_monthly_category_summary.py backend/app/track_anywhere/infrastructure/db/models backend/app/track_anywhere/infrastructure/projections backend/tests/v2
git commit -m "feat: project events per Book"
```

### Task 25: Implement shadow rebuild, catch-up, and atomic generation swap

**Files:**
- Create: `backend/app/track_anywhere/infrastructure/projections/rebuild.py`
- Create: `backend/tests/v2/concurrency/test_projection_rebuild.py`
- Create: `backend/tests/v2/replay/test_shadow_generation_parity.py`

**Step 1: Write failing continuous-write tests**

Rebuild the Task 24 monthly category summary, continue appending to multiple Books, kill/restart the builder, catch up, and swap. Assert readers see either the complete old generation or complete new generation, never empty/mixed rows; the active generation matches a cold replay hash.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/concurrency/test_projection_rebuild.py backend/tests/v2/replay/test_shadow_generation_parity.py -q`
Expected: FAIL.

**Step 3: Implement versioned generations**

Snapshot Book heads, replay each Book to the snapshot, catch up to current heads, take a short projector advisory lock, apply final deltas, and atomically update the active-generation pointer. Retain the previous generation.

**Step 4: Verify GREEN**

Run the Step 2 command repeatedly under write load.
Expected: PASS with identical canonical projection hash.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/infrastructure/projections/rebuild.py backend/tests/v2
git commit -m "feat: rebuild projections with shadow generations"
```

### Task 26: Add outbox delivery, ledger metrics, and P0 audit alerts

**Files:**
- Create: `backend/app/track_anywhere/outbox/__init__.py`
- Create: `backend/app/track_anywhere/outbox/worker.py`
- Create: `backend/app/track_anywhere/observability/__init__.py`
- Create: `backend/app/track_anywhere/observability/metrics.py`
- Create: `backend/app/track_anywhere/observability/audit.py`
- Create: `backend/tests/v2/postgres/test_monthly_summary_projection.py`
- Create: `backend/tests/v2/concurrency/test_outbox_delivery.py`
- Create: `backend/tests/v2/unit/test_sensitive_log_redaction.py`

**Step 1: Write failing tests**

Use the Task 24 monthly category projection as the observability proof. Test cold/online parity and late-period invalidation metrics, outbox publish/ack crashes with consumer dedupe, and metric/log fields. Inject a trusted terminal-hash mismatch and a synchronous balance-vs-posting mismatch; each must emit a P0 audit signal, set the affected Book to `paused_integrity`, and make subsequent financial commands fail closed until an audited operator action clears it. Assert raw key, credential, attachment content, and full memo never appear.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/postgres/test_monthly_summary_projection.py backend/tests/v2/concurrency/test_outbox_delivery.py backend/tests/v2/unit/test_sensitive_log_redaction.py -q`
Expected: FAIL.

**Step 3: Implement the minimum proof surfaces**

Expose command/append latency, Book lock wait, conflicts, idempotency replay/conflict, commit unknown, per-Book projection lag/failure, hash verification, balance parity, backfill progress/quarantine, and terminal hash status. Outbox is at-least-once with stable message ID.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS; duplicate delivery has one consumer effect through dedupe.

**Step 5: Commit**

```bash
git add backend/app/track_anywhere/outbox backend/app/track_anywhere/observability backend/tests/v2
git commit -m "feat: add rebuildable reports and outbox"
```

## Phase 6: Deterministic V1 backfill and independent verification

### Task 27: Build the frozen-source manifest, extractor, and inventory

**Files:**
- Create: `backend/tools/__init__.py`
- Create: `backend/tools/backfill_v1/__init__.py`
- Create: `backend/tools/backfill_v1/__main__.py`
- Create: `backend/tools/backfill_v1/config.py`
- Create: `backend/tools/backfill_v1/manifest.py`
- Create: `backend/tools/backfill_v1/extract.py`
- Create: `backend/tools/backfill_v1/inventory.py`
- Create: `backend/tools/backfill_v1/sql/*.sql`
- Create: `backend/tests/v2/backfill/test_manifest.py`
- Create: `backend/tests/v2/backfill/test_extract_determinism.py`
- Create: `backend/tests/v2/backfill/test_inventory.py`

**Step 1: Write failing backfill tests**

Assert source and target DSNs must differ; target must be at the exact V2 Alembic head with zero business/event/receipt/quarantine/seal rows; dump hash and revision must match; row order randomization does not alter canonical NDJSON/hash; extraction runs read-only; and inventory detects orphans, cross-Book references, invalid amounts, duplicate positions, reversal cycles/multiplicity, and unknown assets. V2 schema objects and the generation marker are expected and do not make the target “nonempty.”

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/backfill/test_manifest.py backend/tests/v2/backfill/test_extract_determinism.py backend/tests/v2/backfill/test_inventory.py -q`
Expected: import failure.

**Step 3: Implement frozen SQL extraction**

The extractor reads the actual restored V1 schema with explicit SQL and emits canonical NDJSON plus manifest metadata. Its CLI accepts explicit batch/worker/shuffle settings for determinism rehearsal, requires a new output directory, and never deletes an existing report. It must not import `FinanceService`, `OrmStorage`, V1 repositories, or V1 projectors.

**Step 4: Verify GREEN**

Run the Step 2 command with deliberately shuffled source query batches.
Expected: PASS and identical manifest hash.

**Step 5: Commit**

```bash
git add backend/tools backend/tests/v2/backfill
git commit -m "feat: extract deterministic V1 manifests"
```

### Task 28: Normalize V1 rows into deterministic V2 commands and events

**Files:**
- Create: `backend/tools/backfill_v1/normalize.py`
- Create: `backend/tools/backfill_v1/generate.py`
- Create: `backend/tools/backfill_v1/namespaces.py`
- Create: `backend/tests/v2/backfill/test_uuid_determinism.py`
- Create: `backend/tests/v2/backfill/test_normalize_legacy_signed.py`
- Create: `backend/tests/v2/backfill/test_usdt_precision.py`
- Create: `backend/tests/v2/backfill/test_backfill_sort_order.py`

**Step 1: Write failing normalization tests**

Fix UUIDv5 namespace golden values, convert legacy signed postings to explicit side/positive units without changing quantity, preserve category version IDs/snapshots, accept exact USDT 8 decimals only in backfill mode, and sort by `effective_at UTC`, canonical transaction ID bytes, then event-kind ordinal. Vary timezone, locale, process count, and extraction order.

**Step 2: Verify RED**

Run: `uv run pytest backend/tests/v2/backfill/test_uuid_determinism.py backend/tests/v2/backfill/test_normalize_legacy_signed.py backend/tests/v2/backfill/test_usdt_precision.py backend/tests/v2/backfill/test_backfill_sort_order.py -q`
Expected: FAIL.

**Step 3: Implement pure deterministic normalization**

No UUID4, current time, locale collation, database default ordering, float, or online input policy is permitted. Every hash input comes from the fixed manifest/source or a committed constant.

**Step 4: Verify GREEN**

Run the Step 2 command under at least two `TZ` and `LC_ALL` settings.
Expected: PASS with identical generated event fixtures.

**Step 5: Commit**

```bash
git add backend/tools/backfill_v1 backend/tests/v2/backfill
git commit -m "feat: normalize V1 ledger deterministically"
```

### Task 29: Implement resumable load, source receipts, checkpoint, quarantine, and seal

**Files:**
- Create: `alembic/versions/v2_0008_backfill_control.py`
- Modify: `backend/app/track_anywhere/infrastructure/db/models/__init__.py`
- Create: `backend/app/track_anywhere/infrastructure/db/models/backfill.py`
- Create: `backend/tools/backfill_v1/load.py`
- Create: `backend/tools/backfill_v1/checkpoint.py`
- Create: `backend/tools/backfill_v1/quarantine.py`
- Create: `backend/tests/v2/backfill/test_resumable_load.py`
- Create: `backend/tests/v2/backfill/test_quarantine_gate.py`
- Create: `backend/tests/v2/backfill/test_backfill_seal.py`

**Step 1: Write failing crash/resume tests**

Test schema constraints for source receipts, keyset checkpoints, quarantine decisions, and seals. Then test kill at every transaction boundary, keyset resume, duplicate source receipt, same snapshot rerun with zero new events, changed manifest rejection, nonzero quarantine blocking seal, and Book-level transaction atomicity.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/backfill/test_resumable_load.py backend/tests/v2/backfill/test_quarantine_gate.py backend/tests/v2/backfill/test_backfill_seal.py -q`
Expected: FAIL.

**Step 3: Implement privileged load through the V2 application boundary**

Create persistent backfill control tables in `v2_0008`. Use deterministic IDs and an explicit backfill actor/policy. Import receipt identity is `(snapshot_id, source_table, source_primary_key)`. Checkpoint stores the last canonical source key, never an offset. Seal records manifest hash, counts, terminal Book hashes, and quarantine summary. A successful `run --output-dir DIR` writes a stable `DIR/verification.json` consumed by the independent determinism verifier.

**Step 4: Verify GREEN**

Run the Step 2 command with injected process termination.
Expected: PASS; resumed output equals a clean run.

**Step 5: Commit**

```bash
git add alembic/versions/v2_0008_backfill_control.py backend/app/track_anywhere/infrastructure/db/models backend/tools/backfill_v1 backend/tests/v2/backfill
git commit -m "feat: load V1 data with resumable backfill"
```

### Task 30: Implement an independent SQL/reference verifier

**Files:**
- Create: `backend/tools/backfill_v1/verify.py`
- Create: `backend/tools/backfill_v1/verify_determinism.py`
- Create: `backend/tools/backfill_v1/reference_reducer.py`
- Create: `backend/tests/v2/backfill/corruption_harness.py`
- Create: `backend/tests/v2/backfill/test_independent_verifier.py`
- Create: `backend/tests/v2/backfill/test_verifier_mutations.py`

**Step 1: Write mutation tests**

Create valid imports, then independently inject one lost posting, swapped side, wrong effective time, changed classification, duplicate reversal, broken previous hash, noncontiguous Book/stream version, cross-Book link, and modified USDT unit. The verifier must report a specific code for each. First prove the normal runtime role cannot update/delete append-only facts or disable their triggers.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/backfill/test_independent_verifier.py backend/tests/v2/backfill/test_verifier_mutations.py -q`
Expected: FAIL.

**Step 3: Implement without production imports**

The verifier may share only primitive standard-library types. It must not import the production projector, event hash function, canonicalizer, or application handlers. Reimplement reference sums/hash bytes from the written contract and direct SQL. Expose `verify --source-url URL --target-url URL --manifest PATH --output PATH`; it reads the restored source and target directly and writes its own canonical report rather than trusting loader output.

Mutation tests use an isolated database cloned by `postgres_database_factory`. The test-only `corruption_harness.py` connects with the migrator DSN, explicitly `SET ROLE` to the non-login table owner, disables only the named trigger/constraint needed for one fixture, applies one corruption, immediately restores the trigger, runs the verifier, resets the role, and drops the database. It must refuse any database not created by the current test UUID and must never be imported by production/backfill packages. This owner-role harness is the sole bypass for append-only protections; admin and runtime DSNs are rejected.

Compare per Book/account/asset/time bucket, transaction/posting/reversal/reporting/investment counts and relationships, source receipts, event schemas, positions, chain/head hashes, and USDT exact values.

**Step 4: Verify GREEN**

Run the Step 2 command.
Expected: PASS; every mutation is detected.

**Step 5: Commit**

```bash
git add backend/tools/backfill_v1 backend/tests/v2/backfill
git commit -m "feat: independently verify V2 backfill"
```

### Task 31: Rehearse the frozen dump twice and record the parity report

**Files:**
- Create: `docs/operations/v2-backfill-runbook.md`
- Create: `docs/operations/v2-backfill-verification-template.md`
- Modify: `compose.e2e.yaml`
- Modify: `backend/tests/v2/postgres_factory.py`
- Modify: `backend/tests/v2/postgres/test_database_factory.py`
- Create: `scripts/pg17-client.sh`
- Create: `scripts/rehearse-v2-backfill.sh`
- Create: `backend/tests/v2/backfill/test_frozen_dump_contract.py`
- Create: `backend/tests/v2/unit/test_pg17_client_wrapper.py`
- Create: `backend/tests/v2/unit/test_backfill_rehearsal_script.py`
- Modify: `pyproject.toml`
- Generated but do not commit: `output/v2-backfill-run-*/`

**Step 1: Write the frozen-source and single-process rehearsal tests**

Register a `frozen_dump` pytest marker. The marked local-only contract requires `TRACK_ANYWHERE_FROZEN_V1_DUMP`, its manifest, and `TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL`; it verifies the dump hash/manifest/revision and directly queries the already restored read-only source for reference counts and expected USDT 8-decimal identities before loading. It never performs a second restore.

`test_pg17_client_wrapper.py` requires a dedicated `postgres:17-alpine` client service and `scripts/pg17-client.sh`; the wrapper accepts only `psql`, `pg_restore`, or `pg_dump`, runs the selected tool inside that pinned service on the E2E network, preserves stdin/exit status, and proves all three report major version 17 without requiring host-installed PostgreSQL binaries. Extend the factory test for `libpq-url --url SQLALCHEMY_URL --host postgres --port 5432`: parse and render through SQLAlchemy's URL API, change `postgresql+psycopg://` to `postgresql://`, preserve percent-encoded credentials/database/query values, and reject non-loopback source hosts or any target host other than the fixed Compose service. Never perform this conversion with string replacement.

`test_backfill_rehearsal_script.py` requires one script process to own the complete lifecycle: PG17 client/version preflight, cleanup trap installed before database creation, restored source plus two V2 targets, source contract check, run A, run B, two independent SQL verifier invocations, determinism comparison, strict success cleanup, absence readback, and only then the PASS summary. It rejects a design that relies on host `pg_restore`/`psql`/`pg_dump`, passes a SQLAlchemy driver URL to libpq, relies on variables/traps surviving across separate shell invocations, reuses best-effort trap cleanup as the success gate, converts a failed command into a PASS summary/zero exit, or deletes an existing output directory. Extend the factory CLI with `assert-absent --url URL`; it extracts the expected factory database name, connects through the admin base URL, and fails while that database still exists.

**Step 2: Verify RED**

Run: `uv run pytest backend/tests/v2/unit/test_pg17_client_wrapper.py backend/tests/v2/unit/test_backfill_rehearsal_script.py -q`
Expected: FAIL because the pinned PG17 client wrapper, safe libpq conversion, and single-process rehearsal script do not exist.

**Step 3: Implement the complete rehearsal in one shell process**

Add a no-volume, no-published-port `pg17-client` service to `compose.e2e.yaml`, pinned to the same `postgres:17-alpine` image and attached only to its default network. `scripts/pg17-client.sh` runs its allowlisted client command via `docker compose -p track-anywhere-v2-test -f compose.e2e.yaml run --rm -T --no-deps pg17-client`; it never calls a host PostgreSQL binary. Add the factory's tested `libpq-url` command so container clients receive a valid `postgresql://...@postgres:5432/...` URI while SQLAlchemy continues to receive the original host `postgresql+psycopg://` DSN.

`scripts/rehearse-v2-backfill.sh` uses `set -euo pipefail` and accepts exactly `--dump PATH --manifest PATH --output-root DIR`. The output root must not exist. Before creating anything it installs an EXIT trap that calls `cleanup_best_effort`; that failure-only cleanup drops every nonempty factory URL independently with `|| true` so one drop cannot prevent the others. A separate `cleanup_strict` is used only on the success path: it drops every created database without `|| true` and calls factory `assert-absent` for source, target A, and target B. The script exits nonzero on every failed command, failed assertion, strict-cleanup failure, or absence-readback failure; writes `status=PASS` only after every gate and strict cleanup succeeds; and never turns a retained failure report into exit status 0.

Within that same process, in this order:

1. Assert the dump SHA-256 and manifest path match the frozen inputs; require the Compose PG17 server healthy, then assert `scripts/pg17-client.sh psql --version`, `pg_restore --version`, and `pg_dump --version` all report major version 17.
2. Create the V1 source with `create --schema empty --emit-role migrator`; obtain the non-login owner name with `role-name --kind owner`; convert the returned SQLAlchemy DSN with the factory's `libpq-url` command; pipe the custom dump over stdin to `scripts/pg17-client.sh pg_restore --dbname LIBPQ_URL --exit-on-error --no-owner --no-acl --role OWNER`. Assert the URI passed to libpq has scheme `postgresql://` and container host `postgres:5432`; never expose it in output.
3. Derive a source DSN whose connections default to read-only, and create targets A/B with `create --schema v2 --emit-role runtime`. Assert both are at the exact V2 head with zero business/event/receipt/quarantine/seal rows.
4. Run `test_frozen_dump_contract.py -m frozen_dump` with dump, manifest, and restored-source URL environment variables.
5. Run A under `TZ=UTC LC_ALL=C`, batch size 37, one worker, shuffle seed 0, output `DIR/run-a`.
6. Run B under `TZ=Pacific/Auckland LC_ALL=en_US.UTF-8`, batch size 13, four workers, shuffle seed 731, output `DIR/run-b`.
7. Invoke Task 30 `verify` independently against source/target A and source/target B, writing `run-a/independent-verification.json` and `run-b/independent-verification.json`.
8. Invoke `verify-determinism` on those two independent reports, not the loader reports.
9. Run `cleanup_strict`, read back database absence through the admin connection, then write secret-free `DIR/summary.json` with a unique run ID, source counts, quarantine count, event/projection/terminal hashes, both independent report hashes, runtime/migrator role names, and `status=PASS`; only after that atomic report write succeeds may the script clear the EXIT trap. A failure may write a separate diagnostic report, but must not write a PASS summary and must retain its original nonzero exit status.

No DSN, password, dump, restored database, or full memo enters the reports.

**Step 4: Run the real frozen-dump gate**

Run:

```bash
set -euo pipefail
BACKFILL_RUN_ROOT="output/v2-backfill-run-$(date -u +%Y%m%dT%H%M%SZ)-$$"
bash scripts/rehearse-v2-backfill.sh \
  --dump /Users/xuyanyue/Documents/track-anywhere-stable-backend/backups/neon-track_anywhere-20260713-095634-before-ledger-kernel-refactor.dump \
  --manifest /Users/xuyanyue/Documents/track-anywhere-stable-backend/backups/neon-track_anywhere-20260713-095634-before-ledger-kernel-refactor.manifest.txt \
  --output-root "$BACKFILL_RUN_ROOT"
uv run python - "$BACKFILL_RUN_ROOT/summary.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["status"] == "PASS"
assert report["quarantine_count"] == 0
assert report["source_counts"] == {
    "accounts": 121,
    "transactions": 135,
    "postings": 284,
    "transaction_lines": 43,
}
assert report["run_id"]
PY
```

Expected: quarantine 0; exact 121 account / 135 transaction / 284 posting / 43 transaction-line source counts; A/B event IDs, Book positions, payloads, terminal hashes, and projection hashes identical; both independent verifiers PASS; no database remains after the script exits.

**Step 5: Commit only the harness, tests, and runbooks**

```bash
git add docs/operations/v2-backfill-runbook.md docs/operations/v2-backfill-verification-template.md compose.e2e.yaml backend/tests/v2/postgres_factory.py backend/tests/v2/postgres/test_database_factory.py scripts/pg17-client.sh scripts/rehearse-v2-backfill.sh backend/tests/v2/backfill/test_frozen_dump_contract.py backend/tests/v2/unit/test_pg17_client_wrapper.py backend/tests/v2/unit/test_backfill_rehearsal_script.py pyproject.toml
git commit -m "docs: prove deterministic V2 backfill"
```

Do not add dump files, restored databases, secrets, DSNs, or generated `output/` reports.

## Phase 7: Retire V1, run all gates, and stop at isolated staging

### Task 32: Freeze the capability matrix and pass the pre-retirement gate

**Files:**
- Create: `docs/operations/v2-capability-matrix.md`
- Create: `docs/operations/v2-retirement-manifest.md`
- Create: `docs/operations/v2-pre-retirement-verification.md`
- Create: `scripts/verify-v2.sh`
- Create: `backend/tests/v2/unit/test_capability_matrix.py`
- Create: `backend/tests/v2/unit/test_verify_v2_script.py`

**Step 1: Write the failing capability test**

For auth, Book and Book membership, Assets/Accounts/category versions, drafts, counterparties, projects, journal, reversal/correction, external references, classification, FX, investment lots, valuations, monthly reports, budgets, search, CLI, attachments, imports/quarantine, recurring rules, payment instruments/tools, backup/restore, and system/operations configuration, require exactly one status: V2 implemented, explicitly deferred with reason, or intentionally removed. Require an owner/test/evidence link for every implemented item. A second test requires a pre-retirement `scripts/verify-v2.sh` with all V2, PG17, concurrency, replay, synthetic-backfill, contract/CLI, frontend, migration, and role-separation gates; it must not collect legacy V1 tests or the local-only `frozen_dump` marker.

**Step 2: Verify RED**

Run: `uv run --extra postgres pytest backend/tests/v2/unit/test_capability_matrix.py backend/tests/v2/unit/test_verify_v2_script.py -q`
Expected: FAIL until the matrix, retirement manifest, and aggregate pre-retirement verifier are complete.

**Step 3: Complete the matrix and exact retirement manifest**

Inventory all root-level legacy modules and tests. The manifest names every file/directory to delete or retain; each retained auth/security/attachment/CLI utility needs a V2 import consumer and rationale. No “unknown” status is permitted.

Create `scripts/verify-v2.sh` now, before destructive retirement:

```bash
#!/usr/bin/env bash
set -euo pipefail
: "${TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL:?required isolated PG17 admin URL}"
: "${TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL:?required isolated PG17 migrator base URL}"
: "${TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL:?required isolated PG17 runtime base URL}"
export TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE=1
unset TRACK_ANYWHERE_TEST_POSTGRES_URL TRACK_ANYWHERE_DATABASE_URL
uv sync --locked --extra postgres
uv run --extra postgres pytest backend/tests/v2/unit -q
uv run --extra postgres pytest backend/tests/v2/postgres backend/tests/v2/concurrency -q
uv run --extra postgres pytest backend/tests/v2/replay backend/tests/v2/backfill -m 'not frozen_dump' -q
uv run --extra postgres pytest backend/tests/v2/contract cli/tests contract_tests -q
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run build
V2_ALEMBIC_CHECK_URL=
cleanup_verify_v2() {
  if [ -n "$V2_ALEMBIC_CHECK_URL" ]; then uv run --extra postgres python backend/tests/v2/postgres_factory.py drop --url "$V2_ALEMBIC_CHECK_URL" || true; fi
}
trap cleanup_verify_v2 EXIT
V2_ALEMBIC_CHECK_URL="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py create --purpose verify-v2 --schema empty --emit-role migrator)"
export TRACK_ANYWHERE_DB_RUNTIME_ROLE="$(uv run --extra postgres python backend/tests/v2/postgres_factory.py role-name --kind runtime)"
TRACK_ANYWHERE_DATABASE_URL="$V2_ALEMBIC_CHECK_URL" uv run --extra postgres alembic upgrade head
TRACK_ANYWHERE_DATABASE_URL="$V2_ALEMBIC_CHECK_URL" uv run --extra postgres alembic check
cleanup_verify_v2
trap - EXIT
```

The PostgreSQL suites include the Task 8 runtime-role permission test, so the aggregate gate cannot pass against an owner-backed API database.

**Step 4: Pass V2 gates before deleting V1**

Run `bash scripts/verify-v2.sh`, then run the exact Task 31 frozen-dump two-import/independent-verifier block separately because the dump is intentionally local-only. Also run `bash scripts/e2e-docker-postgres.sh`. Record commit, PG17 version, runtime/migrator identities, Alembic head, terminal hashes, projection hashes, quarantine count, and commands in `v2-pre-retirement-verification.md`.

Expected: every V2 gate PASS, frozen-dump quarantine 0, and the capability test PASS. Do not delete V1 until this evidence exists.

**Step 5: Commit**

```bash
git add docs/operations/v2-capability-matrix.md docs/operations/v2-retirement-manifest.md docs/operations/v2-pre-retirement-verification.md scripts/verify-v2.sh backend/tests/v2/unit/test_capability_matrix.py backend/tests/v2/unit/test_verify_v2_script.py
git commit -m "docs: approve the V2 retirement gate"
```

### Task 33: Delete the V1 runtime and SQLite persistence path

**Files:**
- Delete: `backend/app/track_anywhere/service.py`
- Delete: all root-level V1 `service_*.py` and `storage_*.py` files named by `docs/operations/v2-retirement-manifest.md`
- Delete: `backend/app/track_anywhere/service_persistence/`
- Delete: `backend/app/track_anywhere/storage_repositories/`
- Delete: `backend/app/track_anywhere/storage_change_writers/`
- Delete: `backend/app/track_anywhere/api_runtime.py`
- Delete: `backend/app/track_anywhere/api_routes.py`
- Delete: `backend/app/track_anywhere/api_routers/`
- Delete: `backend/app/track_anywhere/api_ports/`
- Delete: `backend/app/track_anywhere/ledger.py`
- Delete: `backend/app/track_anywhere/transaction_builder.py`
- Delete: `backend/app/track_anywhere/idempotency.py`
- Delete: `backend/app/track_anywhere/posting_semantics.py`
- Delete: `backend/app/track_anywhere/posting_semantics_audit.py`
- Delete: `backend/app/track_anywhere/posting_semantics_views.py`
- Delete: every other allowlist-excluded V1 runtime module from the retirement manifest
- Delete or rewrite: all tracked `backend/tests/test_*.py`; preserve `backend/tests/v2/` and explicitly approved shared fixtures only
- Delete: `scripts/benchmark-write-performance.py`
- Modify: `conftest.py`
- Modify: `backend/tests/conftest.py`
- Modify: `contract_tests/conftest.py`
- Modify: `contract_tests/api_clients.py`
- Create: `backend/tests/v2/unit/test_v1_runtime_removed.py`
- Create: `backend/tests/v2/unit/test_v2_module_allowlist.py`

**Step 1: Write the deletion gates and verify RED**

```python
FORBIDDEN_RUNTIME_SYMBOLS = (
    "FinanceService", "OrmStorage", "StorageReadCache", "legacy_signed",
    "amount_semantics", "confirmed_transaction_count", "/api/v1",
)
```

Scan `backend/app`, `cli`, the entire `frontend/app`, `scripts`, `contract_tests`, `Dockerfile`, compose files, and workflows, excluding only `backend/tools/backfill_v1` and historical docs. The module allowlist must fail any root-level runtime module not approved in the retirement manifest.

**Step 2: Delete exactly the approved manifest**

Remove V1 runtime modules and V1-only tests. Remove the remaining legacy SQLite fallback branches from root/backend test configuration; the contract-test override was already replaced by PG17 fixtures in Task 23. Keep/rewrite auth, security, attachment CRUD, and CLI transport only when the capability matrix proves a V2 consumer.

**Step 3: Run focused post-deletion tests**

Run: `bash scripts/verify-v2.sh`
Run the forbidden-symbol and module-allowlist tests.

Expected: PASS and no V1 runtime references outside the frozen extractor/history.

**Step 4: Run the full V2 gate again**

Repeat the exact Task 31 frozen-dump two-import/independent-verifier rehearsal against new databases and output directories, then run `bash scripts/e2e-docker-postgres.sh`. Expected: identical V2 behavior and terminal hashes after deletion.

**Step 5: Commit**

```bash
git add -A backend/app backend/tests conftest.py contract_tests cli frontend scripts Dockerfile compose*.yaml .github/workflows
git commit -m "refactor: remove the V1 runtime"
```

### Task 34: Make PostgreSQL, concurrency, replay, frontend, and backfill mandatory CI gates

**Files:**
- Modify: `.github/workflows/docker-image.yml`
- Modify: `scripts/e2e-docker-postgres.sh`
- Create: `backend/tests/v2/unit/test_ci_v2_gates.py`

**Step 1: Write the failing CI-structure test**

Assert CI starts PostgreSQL 17 and has separate required commands for unit, PostgreSQL constraints, concurrency, replay, contract/CLI, frontend lint/build, and synthetic backfill tests before image build. Assert CI installs Node 22 with `actions/setup-node` before `npm ci`; both nightly and stable image builds depend on every gate; the hash-vector matrix runs on Python 3.12 and 3.13; the real `frozen_dump` marker is excluded from CI with an explicit reason; and no production deploy job is added.

**Step 2: Verify RED**

Run: `uv run pytest backend/tests/v2/unit/test_ci_v2_gates.py -q`
Expected: FAIL because current CI has one SQLite-biased unit job and tag-only E2E.

**Step 3: Wire the already-proven gates into CI**

Provision the same non-superuser migrator/runtime roles in the PG17 CI service before tests; export the admin, migrator-base, and runtime-base factory URLs plus `TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE=1`; and run the committed Task 32 gate commands (or phase-equivalent commands) as required jobs. API/E2E receives the derived runtime DSN; Alembic receives the derived migrator DSN and explicit `TRACK_ANYWHERE_DB_RUNTIME_ROLE`. CI runs `actions/setup-node` with Node 22, matching the frontend engine pin, before `npm ci`. A separate small matrix runs the committed hash vectors on Python 3.12 and 3.13; do not claim another Python minor supported until its vectors pass. The local `frozen_dump` rehearsal stays excluded with an explicit reason and remains a manual release gate.

**Step 4: Verify GREEN locally**

Run: `bash scripts/verify-v2.sh`
Run: `bash scripts/e2e-docker-postgres.sh`
Expected: all gates PASS. E2E exercises V2 health, auth, account setup, exact post, query, reclassify, reverse, and balance readback.

**Step 5: Commit**

```bash
git add .github/workflows/docker-image.yml scripts backend/tests/v2/unit/test_ci_v2_gates.py
git commit -m "ci: require all V2 ledger gates"
```

### Task 35: Build and validate isolated staging, then stop

**Files:**
- Create: `docs/operations/v2-isolated-staging-checklist.md`
- Create: `docs/operations/v2-final-verification.md`
- Create: `scripts/staging-v2-smoke.sh`
- Modify: `scripts/e2e-docker-postgres.sh`
- Modify: `compose.e2e.yaml`
- Modify if required: `.dockerignore`
- Modify if required: `Dockerfile`
- Create: `backend/tests/v2/unit/test_staging_harness.py`

**Step 1: Write the staging-harness test and verify RED**

Assert the checklist/harness requires clean migration, readiness fail-closed, V2 API/CLI smoke, no V1 route, PG17 version, fresh-connection balance visibility, hash/head verification, async lag, independent replay, image labels/digests, exact running-container image IDs, distinct migrator/runtime identities, and an explicit “no production deploy” stop condition. The compose migration service must use a migrator DSN; API/web smoke must use the non-owner runtime DSN. Assert no-build/existing-stack modes cannot call `docker build` or recreate the database, all ports bind loopback, and `.dockerignore` excludes dumps/backups/`output/`. The harness must require a caller-supplied unique run ID and a nonexistent run-specific report directory, write both the run ID and source commit into its final report, emit `status=PASS` only after all checks, and preserve nonzero status on any failure. The outer gate may atomically update a source-commit-specific accepted-run pointer file only after independently validating PASS/SHA/run ID; failed run directories remain available and never block a fresh UUID retry.

Run: `uv run pytest backend/tests/v2/unit/test_staging_harness.py -q`
Expected: FAIL before the staging harness/checklist exists.

**Step 2: Implement and commit the harness before building**

Teach `compose.e2e.yaml` to accept `TRACK_ANYWHERE_E2E_API_IMAGE` and `TRACK_ANYWHERE_E2E_WEB_IMAGE`, provision the Task 1 roles, add a one-shot migration service that uses the migrator DSN plus explicit `TRACK_ANYWHERE_DB_RUNTIME_ROLE`, pass only the runtime DSN to API, and keep all published ports loopback-only. `scripts/e2e-docker-postgres.sh` must support `TRACK_ANYWHERE_E2E_NO_BUILD=1` and `TRACK_ANYWHERE_E2E_EXISTING_STACK=1`; in those modes it may run HTTP/CLI checks but may not build, recreate, migrate, or silently substitute an image/database. Implement `staging-v2-smoke.sh`, checklist, exclusions, and the test; then run the Step 1 test GREEN.

Commit the harness before any image build:

```bash
git add docs/operations/v2-isolated-staging-checklist.md scripts/staging-v2-smoke.sh scripts/e2e-docker-postgres.sh compose.e2e.yaml .dockerignore Dockerfile backend/tests/v2/unit/test_staging_harness.py
git commit -m "test: add isolated V2 staging harness"
test -z "$(git status --porcelain --untracked-files=no)"
```

**Step 3: Build the committed source and run exact-image staging**

Build only after the harness commit and clean tracked-tree assertion. Feed Docker a `git archive` tar stream for that commit, not the mutable worktree, so no untracked/ignored file can enter `COPY`. Label both images with that exact source commit:

Run:

```bash
set -euo pipefail
STAGING_SOURCE_COMMIT="$(git rev-parse HEAD)"
STAGING_RUN_ID="$(uv run python -c 'import uuid; print(uuid.uuid4())')"
STAGING_REPORT_DIR="output/v2-staging-$STAGING_SOURCE_COMMIT-$STAGING_RUN_ID"
STAGING_ACCEPTED_POINTER="output/v2-staging-$STAGING_SOURCE_COMMIT-accepted"
STAGING_ACCEPTED_TMP="$STAGING_ACCEPTED_POINTER.tmp-$STAGING_RUN_ID"
test -z "$(git status --porcelain --untracked-files=no)"
test ! -e "$STAGING_REPORT_DIR"
git archive --format=tar "$STAGING_SOURCE_COMMIT" | docker build --label "org.opencontainers.image.revision=$STAGING_SOURCE_COMMIT" --target api-runtime -t track-anywhere-api:v2-staging -
git archive --format=tar "$STAGING_SOURCE_COMMIT" | docker build --label "org.opencontainers.image.revision=$STAGING_SOURCE_COMMIT" --target web-runtime -t track-anywhere-web:v2-staging -
TRACK_ANYWHERE_E2E_API_IMAGE=track-anywhere-api:v2-staging \
TRACK_ANYWHERE_E2E_WEB_IMAGE=track-anywhere-web:v2-staging \
TRACK_ANYWHERE_E2E_NO_BUILD=1 \
bash scripts/staging-v2-smoke.sh \
  --source-commit "$STAGING_SOURCE_COMMIT" \
  --run-id "$STAGING_RUN_ID" \
  --report-dir "$STAGING_REPORT_DIR"
uv run python - "$STAGING_REPORT_DIR/verification.json" "$STAGING_SOURCE_COMMIT" "$STAGING_RUN_ID" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["status"] == "PASS"
assert report["source_commit"] == sys.argv[2]
assert report["run_id"] == sys.argv[3]
PY
printf '%s\n' "$(basename "$STAGING_REPORT_DIR")" > "$STAGING_ACCEPTED_TMP"
mv -f "$STAGING_ACCEPTED_TMP" "$STAGING_ACCEPTED_POINTER"
test "$(wc -l < "$STAGING_ACCEPTED_POINTER" | tr -d ' ')" = 1
IFS= read -r ACCEPTED_REPORT_BASENAME < "$STAGING_ACCEPTED_POINTER"
test "$ACCEPTED_REPORT_BASENAME" = "$(basename "$STAGING_REPORT_DIR")"
```

`staging-v2-smoke.sh` requires `--source-commit SHA --run-id UUID --report-dir DIR`, requires the UUID to be part of a report-directory basename under `output/`, requires that directory not to exist, and never relies on a caller's unexported shell variable. It installs an EXIT trap before creating resources, creates a new compose project and PG17 volume derived from the run ID, runs the migration service to completion as migrator, starts API/web as runtime with `--no-build --wait`, and asserts their `docker inspect` image IDs and revision labels equal the two prebuilt images and the supplied SHA before invoking the existing-stack E2E flow. It verifies the runtime user cannot alter events/disable triggers, emits secret-free `DIR/verification.json` containing `status=PASS`, source SHA, and run ID only after every check passes, and tears down containers/volumes while retaining diagnostics on failure. Any mismatch, attempted build, failed check, or stale run-specific report directory is a hard nonzero failure. The caller atomically replaces the one-line accepted-run pointer only after validating this report; the harness itself never accepts a run.

**Step 4: Run final checks and write evidence for the source commit**

Run:

```bash
set -euo pipefail
STAGING_SOURCE_COMMIT="$(git rev-parse HEAD)"
STAGING_ACCEPTED_POINTER="output/v2-staging-$STAGING_SOURCE_COMMIT-accepted"
test -f "$STAGING_ACCEPTED_POINTER"
test "$(wc -l < "$STAGING_ACCEPTED_POINTER" | tr -d ' ')" = 1
IFS= read -r STAGING_REPORT_BASENAME < "$STAGING_ACCEPTED_POINTER"
test "$(basename "$STAGING_REPORT_BASENAME")" = "$STAGING_REPORT_BASENAME"
case "$STAGING_REPORT_BASENAME" in
  v2-staging-"$STAGING_SOURCE_COMMIT"-*) ;;
  *) echo "accepted staging pointer does not match source commit" >&2; exit 1 ;;
esac
STAGING_REPORT_DIR="output/$STAGING_REPORT_BASENAME"
STAGING_RUN_ID="$(uv run python - "$STAGING_REPORT_DIR/verification.json" "$STAGING_SOURCE_COMMIT" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["status"] == "PASS"
assert report["source_commit"] == sys.argv[2]
assert report["run_id"]
print(report["run_id"])
PY
)"
bash scripts/verify-v2.sh
if rg -n '/api/v1|FinanceService|OrmStorage|StorageReadCache|legacy_signed|amount_semantics' \
  backend/app cli/track_anywhere_cli frontend/app scripts contract_tests Dockerfile compose*.yaml .github/workflows; then
  echo "forbidden V1 runtime symbol remains" >&2
  exit 1
fi
test -z "$(git status --porcelain --untracked-files=no)"
git status --short
```

Expected: tests PASS; forbidden runtime scan has no hits; tracked runtime files remain clean. Create `docs/operations/v2-final-verification.md` from the staging report and record `$STAGING_SOURCE_COMMIT`, `$STAGING_RUN_ID`, PG17/Alembic versions, runtime/migrator identities, image IDs/digests/labels, exact commands, replay/hash/parity results, and the production-untouched stop condition. Do not claim that this not-yet-committed evidence file was part of the built image.

**Step 5: Commit only the evidence and stop**

```bash
set -euo pipefail
git add docs/operations/v2-final-verification.md
git commit -m "docs: record isolated V2 verification"
EVIDENCE_COMMIT="$(git rev-parse HEAD)"
STAGING_SOURCE_COMMIT="$(git rev-parse HEAD^)"
STAGING_ACCEPTED_POINTER="output/v2-staging-$STAGING_SOURCE_COMMIT-accepted"
test -f "$STAGING_ACCEPTED_POINTER"
test "$(wc -l < "$STAGING_ACCEPTED_POINTER" | tr -d ' ')" = 1
IFS= read -r STAGING_REPORT_BASENAME < "$STAGING_ACCEPTED_POINTER"
test "$(basename "$STAGING_REPORT_BASENAME")" = "$STAGING_REPORT_BASENAME"
case "$STAGING_REPORT_BASENAME" in
  v2-staging-"$STAGING_SOURCE_COMMIT"-*) ;;
  *) echo "accepted staging pointer does not match source commit" >&2; exit 1 ;;
esac
STAGING_REPORT_DIR="output/$STAGING_REPORT_BASENAME"
STAGING_RUN_ID="$(uv run python - "$STAGING_REPORT_DIR/verification.json" "$STAGING_SOURCE_COMMIT" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["status"] == "PASS"
assert report["source_commit"] == sys.argv[2]
assert report["run_id"]
print(report["run_id"])
PY
)"
rg -q "$STAGING_SOURCE_COMMIT" docs/operations/v2-final-verification.md
rg -q "$STAGING_RUN_ID" docs/operations/v2-final-verification.md
test -z "$(git status --porcelain --untracked-files=no)"
printf 'staging_source=%s evidence_commit=%s\n' "$STAGING_SOURCE_COMMIT" "$EVIDENCE_COMMIT"
git status --short
```

The handoff records both the staging source commit from the evidence document and `$EVIDENCE_COMMIT`; they are intentionally different. Do not push a production tag, replace the stable runtime, change production DSNs, or deploy. Production cutover requires a new user authorization after this plan is complete.

## Final success criteria

The implementation is complete only when all of the following are true:

- all financial facts replay from typed immutable events with exact integer units;
- PostgreSQL independently rejects cross-Book/account-asset violations and unbalanced transactions;
- same-Book concurrency preserves continuous positions/hash chain and cross-Book reverse commit order loses no events;
- cross-process idempotency survives unknown commit outcomes without a committed `processing` receipt;
- synchronous reads are immediately visible from another worker and use no process-local source-of-truth cache;
- asynchronous projections, late effective events, crashes, duplicates, and shadow rebuilds converge to cold replay;
- the fixed V1 dump imports twice with identical event IDs/order/payload/terminal and projection hashes;
- the independent verifier catches every mutation fixture and quarantine is zero;
- the capability matrix has no unknowns and the runtime has no `/api/v1`, V1 facade/cache/hydration, String amount cast, or online `legacy_signed` path;
- the full test/CI/E2E suite passes on PostgreSQL 17 with migrations and runtime using distinct non-superuser roles;
- isolated staging is verified and production remains untouched.
