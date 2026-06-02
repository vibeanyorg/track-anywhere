# SafePal USD24-Backed Card Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users record SafePal card spending through a simple payment alias while the ledger automatically records the USD expense and immediate USD24-backed settlement in one atomic transaction.

**Architecture:** Add a persistent payment profile for token-backed cards, then add a dedicated backed-card expense use case that resolves a profile, validates backing balance, and creates a balanced multi-asset transaction using the existing FX clearing pattern. Keep ordinary expense and transfer behavior unchanged.

**Tech Stack:** FastAPI, Pydantic commands, SQLAlchemy storage models and Alembic migrations, Click CLI, pytest, existing Track Anywhere ledger/posting/idempotency services.

---

### Task 1: Lock the Desired Backed-Card Behavior With Service Tests

**Files:**
- Create: `backend/tests/test_payment_profiles.py`
- Read: `backend/app/track_anywhere/service_fx.py`
- Read: `backend/tests/test_financial_hardening.py`

**Step 1: Write the failing service test**

Add a test that creates:

- an expense category;
- `SafePal Card USD(5964)` as an asset account in `USD`;
- `SafePal USD24 (Arbitrum)` as an asset account in `USD24` with opening balance `277.44`;
- a payment profile with slug `safepal`;
- one backed-card expense for `3.40 USD`.

Expected assertions:

```python
assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "0.00"
assert service.account_balance(token, usd24.account_id)["official_balance"]["amount"] == "274.04"
assert service.category_summary(token, kind="expense", currency="USD")["groups"][0]["amount"] == "3.40"
```

Also assert the transaction has:

```python
assert len(transaction.postings) == 6
assert [line.line_type for line in transaction.lines] == ["expense", "fx_exchange"]
```

**Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest backend/tests/test_payment_profiles.py::test_token_backed_card_expense_settles_immediately -q
```

Expected: failure because payment profiles and backed-card expense recording do not exist.

**Step 3: Add an idempotency test**

In the same file, call the backed-card expense API twice with the same idempotency key.

Expected:

```python
assert replay is False
assert replay_again is True
assert service.account_balance(token, card.account_id)["official_balance"]["amount"] == "0.00"
assert service.account_balance(token, usd24.account_id)["official_balance"]["amount"] == "274.04"
```

**Step 4: Add an insufficient-backing test**

Create USD24 opening balance `1.00`, then attempt a `3.40 USD` SafePal payment.

Expected:

```python
with pytest.raises(ValidationError, match="insufficient backing balance"):
    ...
```

**Step 5: Commit tests only**

```bash
git add backend/tests/test_payment_profiles.py
git commit
```

Use a Lore-style message. The tests should still be failing at this point if no implementation has been added.

---

### Task 2: Add Payment Profile Domain and Storage

**Files:**
- Modify: `backend/app/track_anywhere/storage_models.py`
- Modify: `backend/app/track_anywhere/domain_storage_loaders.py`
- Modify: `backend/app/track_anywhere/storage_partial.py`
- Modify: `backend/app/track_anywhere/storage.py`
- Create: `alembic/versions/0012_payment_profiles.py`
- Test: `backend/tests/test_payment_profiles.py`

**Step 1: Add the storage record**

Add a `PaymentProfileRecord` in `storage_models.py`:

```python
class PaymentProfileRecord(Base):
    __tablename__ = "payment_profiles"
    __table_args__ = (
        UniqueConstraint("book_id", "slug", name="uq_payment_profiles_book_slug"),
        Index("ix_payment_profiles_book_status", "book_id", "status"),
    )

    profile_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    slug: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    instrument_account_id: Mapped[str] = mapped_column(String(80))
    instrument_currency: Mapped[str] = mapped_column(String(16))
    backing_account_id: Mapped[str] = mapped_column(String(80))
    backing_currency: Mapped[str] = mapped_column(String(16))
    settlement_mode: Mapped[str] = mapped_column(String(40))
    settlement_rate: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
```

**Step 2: Add an Alembic migration**

Create `0012_payment_profiles.py` that creates the same table and constraints.

Run:

```bash
uv run alembic upgrade head
```

Expected: migration succeeds locally.

**Step 3: Add load/save plumbing**

Add payment profiles to storage load/save paths so they survive restart. Follow existing account/category patterns.

**Step 4: Run focused persistence test**

Run:

```bash
uv run pytest backend/tests/test_payment_profiles.py -q
```

Expected: tests may still fail on service behavior, but profile persistence assertions should pass after they are added.

**Step 5: Commit storage changes**

```bash
git add backend/app/track_anywhere/storage_models.py backend/app/track_anywhere/domain_storage_loaders.py backend/app/track_anywhere/storage_partial.py backend/app/track_anywhere/storage.py alembic/versions/0012_payment_profiles.py backend/tests/test_payment_profiles.py
git commit
```

---

### Task 3: Add Payment Profile Service API

**Files:**
- Create: `backend/app/track_anywhere/payment_profiles.py`
- Create: `backend/app/track_anywhere/service_payment_profiles.py`
- Modify: `backend/app/track_anywhere/domain_commands.py`
- Modify: `backend/app/track_anywhere/service.py`
- Test: `backend/tests/test_payment_profiles.py`

**Step 1: Add domain object and command**

Create a `PaymentProfile` dataclass and `CreatePaymentProfileCommand` with:

- `slug`
- `display_name`
- `kind`
- `instrument_account_id`
- `backing_account_id`
- `settlement_mode`
- `settlement_rate`

Restrict first version to:

```python
kind == "token_backed_card"
settlement_mode == "immediate"
settlement_rate == Decimal("1")
```

**Step 2: Add service validation**

Validate:

- both accounts exist;
- both accounts are in one book;
- instrument currency and backing currency are different;
- profile slug is unique per book;
- no real account id is hardcoded.

**Step 3: Add list/get helpers**

Support resolving by `profile_id` or slug. The daily command should use slug.

**Step 4: Run tests**

```bash
uv run pytest backend/tests/test_payment_profiles.py -q
```

Expected: profile creation tests pass; backed expense may still fail until Task 4.

**Step 5: Commit service profile changes**

```bash
git add backend/app/track_anywhere/payment_profiles.py backend/app/track_anywhere/service_payment_profiles.py backend/app/track_anywhere/domain_commands.py backend/app/track_anywhere/service.py backend/tests/test_payment_profiles.py
git commit
```

---

### Task 4: Implement Backed-Card Expense Recording

**Files:**
- Modify: `backend/app/track_anywhere/service_payment_profiles.py`
- Modify: `backend/app/track_anywhere/ledger.py` if a new line type is required
- Test: `backend/tests/test_payment_profiles.py`

**Step 1: Add the command shape**

Add `RecordPaymentProfileExpenseCommand`:

```python
class RecordPaymentProfileExpenseCommand(StrictCommand):
    payment: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=ASSET_CODE_PATTERN)
    category_id: str
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

**Step 2: Validate backing balance before writing**

Use `storage.account_balance(backing_account_id)` or an equivalent confirmed balance path. Reject if the USD24 balance is below the requested amount.

Expected error:

```text
insufficient backing balance
```

**Step 3: Create one atomic transaction**

Use existing system category and FX clearing helpers. For amount `A`:

```python
postings = [
    credit_posting(instrument_account_id, A, "USD"),
    debit_posting(expense_account_id, A, "USD"),
    credit_posting(backing_account_id, A, "USD24"),
    debit_posting(fx_clearing_usd24, A, "USD24"),
    debit_posting(instrument_account_id, A, "USD"),
    credit_posting(fx_clearing_usd, A, "USD"),
]
```

Add:

```python
self.ledger.add_line(transaction, line_type="expense", amount=A, currency="USD", category_id=command.category_id, memo=command.memo)
self.ledger.add_line(transaction, line_type="fx_exchange", amount=A, currency="USD24", memo="SafePal USD24-backed card settlement")
```

**Step 4: Persist with existing ledger writer**

Use the normal append-only ledger persistence path. Do not update or rewrite existing postings.

**Step 5: Run tests**

```bash
uv run pytest backend/tests/test_payment_profiles.py backend/tests/test_financial_hardening.py -q
```

Expected: all pass.

**Step 6: Commit backed-card service changes**

```bash
git add backend/app/track_anywhere/service_payment_profiles.py backend/app/track_anywhere/domain_commands.py backend/app/track_anywhere/ledger.py backend/tests/test_payment_profiles.py
git commit
```

---

### Task 5: Add API Endpoints

**Files:**
- Create: `backend/app/track_anywhere/api_routers/payment_profiles.py`
- Modify: `backend/app/track_anywhere/api.py`
- Modify: `backend/tests/snapshots/public-api-v1.json`
- Test: `backend/tests/test_api.py`
- Test: `backend/tests/test_payment_profiles.py`

**Step 1: Add routes**

Add:

```text
POST /api/v1/payment-profiles
GET  /api/v1/payment-profiles
POST /api/v1/payment-profiles/{payment}/expenses
```

The expense route accepts the simple daily payload:

```json
{
  "amount": "3.40",
  "currency": "USD",
  "category_id": "cat_x",
  "purpose": "Meituan",
  "memo": "SafePal payment"
}
```

**Step 2: Add API tests**

Verify:

- profile creation returns the stored profile;
- backed-card expense returns transaction JSON with six postings;
- duplicate `Idempotency-Key` returns replay true and does not double-deduct.

**Step 3: Refresh public API snapshot if required**

Run:

```bash
uv run pytest backend/tests/test_api.py -q
```

Update the snapshot using the repo's existing snapshot workflow if this test reports intentional route changes.

**Step 4: Commit API changes**

```bash
git add backend/app/track_anywhere/api_routers/payment_profiles.py backend/app/track_anywhere/api.py backend/tests/test_payment_profiles.py backend/tests/snapshots/public-api-v1.json
git commit
```

---

### Task 6: Add CLI Setup and Daily Spending Commands

**Files:**
- Modify: `cli/track_anywhere_cli/click_ledger.py`
- Modify: `cli/track_anywhere_cli/command_ledger.py`
- Modify: `cli/track_anywhere_cli/presenter_catalog.py` or relevant presenter if output needs formatting
- Test: `cli/tests/test_cli_catalog.py` or create `cli/tests/test_cli_payment_profiles.py`

**Step 1: Add setup command**

Add:

```bash
ta payment profile create safepal \
  --display-name "SafePal" \
  --kind token-backed-card \
  --instrument-account-id <card-account> \
  --backing-account-id <usd24-account> \
  --settlement-mode immediate \
  --settlement-rate 1 \
  --json
```

**Step 2: Add daily command**

Extend expense recording to allow `--payment` instead of `--from-account-id`:

```bash
ta expense record --payment safepal --amount 3.40 --currency USD --category-id <cat> --purpose "Meituan" --json
```

Keep existing `--from-account-id` behavior unchanged.

**Step 3: Route the CLI payload**

When `--payment` is present, call:

```text
POST /api/v1/payment-profiles/{payment}/expenses
```

When `--from-account-id` is present, keep the existing `/api/v1/expenses` path.

Reject commands that provide both `--payment` and `--from-account-id`.

**Step 4: Add CLI tests**

Mock requester assertions:

- `--payment safepal` uses the payment-profile expense endpoint;
- `--from-account-id` still uses the old endpoint;
- both options together fail locally with a clear message.

**Step 5: Run CLI tests**

```bash
uv run pytest cli/tests -q
```

Expected: all pass.

**Step 6: Commit CLI changes**

```bash
git add cli/track_anywhere_cli/click_ledger.py cli/track_anywhere_cli/command_ledger.py cli/tests/test_cli_payment_profiles.py
git commit
```

---

### Task 7: Add Balance View and Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/agents/hermes-openclaw.md`
- Optional Modify: `backend/app/track_anywhere/service_accounts.py`
- Test: `backend/tests/test_payment_profiles.py`

**Step 1: Document the user flow**

Add a short section:

```bash
ta expense record --payment safepal --amount 3.40 --currency USD --category-id <cat> --purpose "Meituan"
```

Explain that the card is backed by USD24 at 1:1 in the first version.

**Step 2: Decide whether to add composite balance now**

If adding now, expose a payment-profile status endpoint returning:

```json
{
  "payment": "safepal",
  "backing_balance": {"amount": "277.44", "currency": "USD24"},
  "effective_instrument_balance": {"amount": "277.44", "currency": "USD"},
  "instrument_clearing_balance": {"amount": "0.00", "currency": "USD"}
}
```

If not adding now, document that raw account balances remain available and composite status is a follow-up.

**Step 3: Run docs-adjacent tests**

```bash
uv run pytest backend/tests/test_payment_profiles.py cli/tests -q
```

Expected: all pass.

**Step 4: Commit docs and optional status endpoint**

```bash
git add README.md docs/agents/hermes-openclaw.md backend/app/track_anywhere/service_accounts.py backend/tests/test_payment_profiles.py
git commit
```

---

### Task 8: Full Verification and Stable Runtime Smoke

**Files:**
- No new files expected.

**Step 1: Run full tests**

```bash
uv run pytest -q
```

Expected: all tests pass.

**Step 2: Run diff checks**

```bash
git diff --check
```

Expected: no whitespace errors.

**Step 3: Build and smoke stable image**

```bash
scripts/build-stable-local-image.sh
TRACK_ANYWHERE_READY_ATTEMPTS=90 scripts/start-stable-local.sh
scripts/stable-smoke.sh
```

Expected:

- image label source revision matches the latest commit;
- `http://127.0.0.1:12306/api/v1/ready` returns `status=ok`;
- stable smoke passes.

**Step 4: Manual SafePal dry run on a non-production or backed-up stable database**

Create or reuse a SafePal profile, then run:

```bash
ta expense record --payment safepal --amount 1.00 --currency USD --category-id <cat> --purpose "SafePal test" --idempotency-key safepal-backed-card-smoke --json
ta account balance <safepal-card-account-id> --json
ta account balance <safepal-usd24-account-id> --json
ta tx show <transaction-id> --json
```

Expected:

- card clearing remains `0.00 USD`;
- USD24 decreases by `1.00`;
- transaction shows the expense and settlement postings.

**Step 5: Final commit if needed**

If verification changes docs or snapshots:

```bash
git add <changed-files>
git commit
```

Then prepare deployment only after the user asks for it.
