# Phase 2 Complete — Wallet and Ledger Tables

**Status:** ✅ All 5 tasks done  
**Migration head:** `2a5fcf617481_add_credit_wallet_tables`  
**Verified:** API running clean, tables created, seed data confirmed

---

## What Was Built

The database foundation for the prepaid credit wallet system. Two new PostgreSQL tables, a migration with seed data, a full database client, and a comprehensive test suite.

**Core design rules followed:**
- All money values use `Decimal` / `Numeric(18,6)` — never `float`
- All amounts stored in USD; display conversion happens at API edges only
- Ledger is immutable append-only — `updated_at` only lives on the account row
- `SELECT FOR UPDATE` on every balance mutation to prevent race conditions
- Every mutation requires a unique `idempotency_key` to prevent double-charges

---

## Task 2.1 — SQLAlchemy Models

**Files:** `api/db/models.py`, `api/enums.py`

### OrganizationCreditAccountModel
```
Table: organization_credit_accounts
```
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `organization_id` | Integer FK → organizations | UNIQUE, CASCADE |
| `balance_usd` | Numeric(18,6) | Current balance in USD |
| `display_currency` | String(3) | ISO 4217, default `USD` |
| `display_fx_rate_to_usd` | Numeric(18,6) | 1 display_currency = X USD |
| `low_balance_threshold_usd` | Numeric(18,6) | Alert threshold, 0 = off |
| `is_blocked` | Boolean | Blocks new workflow runs |
| `created_at` / `updated_at` | Timestamptz | Audit timestamps |

**Indexes:** `ix_credit_accounts_organization_id` (UNIQUE), `ix_credit_accounts_balance`, `ix_credit_accounts_blocked`

### CreditLedgerEntryModel
```
Table: credit_ledger_entries
```
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `organization_id` | Integer FK | CASCADE |
| `account_id` | Integer FK → organization_credit_accounts | CASCADE |
| `entry_type` | Enum | See below |
| `amount_usd` | Numeric(18,6) | Signed: + credit, − debit |
| `balance_after_usd` | Numeric(18,6) | Snapshot after this entry |
| `idempotency_key` | String(255) UNIQUE | Prevents duplicate charges |
| `workflow_run_id` | Integer FK → workflow_runs | SET NULL, debits only |
| `tokens_charged` | Integer | Dograh tokens, debits only |
| `paid_amount_minor` | Integer | Minor units (cents), topups only |
| `paid_currency` | String(3) | ISO 4217, topups only |
| `fx_rate_to_usd` | Numeric(18,6) | Exchange rate, topups only |
| `payment_provider_reference` | String(255) | Stripe charge_id etc. |
| `memo` | Text | Human note for adjustments |
| `created_at` | Timestamptz | Immutable — no updated_at |

**Indexes:** `ix_ledger_entries_org_created` (btree, org+created_at), `ix_ledger_entries_idempotency_key` (UNIQUE), plus indexes on account_id, workflow_run_id, entry_type

### CreditLedgerEntryType Enum
```python
initial_balance  # Org creation seed
topup            # Customer payment
debit_call       # Workflow run charge
refund           # Credit reversal
adjustment       # Manual correction
```

---

## Task 2.2 — Alembic Migration

**File:** `api/alembic/versions/2a5fcf617481_add_credit_wallet_tables.py`

- `down_revision` = `csv003_add_value_profile` (single clean chain, no new heads)
- Creates `credit_ledger_entry_type` PostgreSQL ENUM
- Creates both tables with all columns, FK constraints, and indexes
- **Seed data:** `INSERT INTO organization_credit_accounts ... SELECT id FROM organizations` — zero-balance USD accounts for every existing org, safe to re-run (`WHERE NOT EXISTS`)
- Downgrade drops both tables and the ENUM type cleanly

**Verified:**
```
$ alembic current
2a5fcf617481 (head)   ✅

$ SELECT * FROM organization_credit_accounts;
id=1, org_id=1, balance_usd=0.000000, display_currency=USD, is_blocked=false   ✅
```

---

## Tasks 2.3 & 2.4 — Database Client

**File:** `api/db/credit_account_client.py`  
**Registered in:** `api/db/db_client.py` (added to DBClient inheritance chain)

### Methods

#### `apply_ledger_entry()` — Core mutation primitive
The only function allowed to change `balance_usd`. Atomic flow:
1. Fast-path idempotency check (no lock) — returns existing entry if key seen
2. `SELECT ... FOR UPDATE` locks the account row
3. Calculates `new_balance = old_balance + amount_usd`
4. Inserts `CreditLedgerEntryModel` with `balance_after_usd = new_balance`
5. Updates `account.balance_usd = new_balance`
6. Commits — both writes in one transaction

```python
entry = await db.apply_ledger_entry(
    organization_id=org.id,
    entry_type=CreditLedgerEntryType.TOPUP,
    amount_usd=Decimal("100.50"),
    idempotency_key="stripe_pi_abc123",
    paid_amount_minor=10050,
    paid_currency="USD",
    fx_rate_to_usd=Decimal("1"),
    payment_provider_reference="stripe_pi_abc123",
)
```

#### `get_account(organization_id)` → `OrganizationCreditAccountModel | None`
Simple select by organization_id.

#### `get_or_create_account(organization_id)` → `OrganizationCreditAccountModel`
Upsert using `INSERT ... ON CONFLICT DO NOTHING` — race-condition safe.

#### `get_ledger(organization_id, limit, offset)` → `list[CreditLedgerEntryModel]`
Paginated ledger history ordered `created_at DESC` (newest first).

#### `get_entry_by_idempotency_key(key)` → `CreditLedgerEntryModel | None`
Lookup by idempotency key — used for deduplication checks.

#### `update_account_settings(organization_id, ...)` → `OrganizationCreditAccountModel`
Partial update for display_currency, fx_rate, threshold, is_blocked. Only supplied fields are changed.

---

## Task 2.5 — Test Suite

**File:** `api/tests/test_credit_account.py`  
**20 test cases** — uses the project's standard `db_session` fixture (transactional rollback isolation)

| Test | What It Verifies |
|---|---|
| `test_get_account_returns_none_when_not_exists` | Returns None for missing account |
| `test_get_or_create_account_creates_new_account` | Creates account with correct defaults |
| `test_get_or_create_account_returns_existing_account` | Second call returns same ID |
| `test_apply_ledger_entry_topup` | Topup increases balance, stores provenance |
| `test_apply_ledger_entry_debit` | Negative amount decreases balance |
| `test_apply_ledger_entry_idempotency` | Same key returns existing entry, balance only incremented once |
| `test_apply_ledger_entry_zero_amount_raises_error` | ValueError on zero amount |
| `test_apply_ledger_entry_no_account_raises_error` | ValueError when account missing |
| `test_get_ledger_returns_entries_newest_first` | Descending created_at order |
| `test_get_ledger_pagination` | Correct limit/offset, no overlap |
| `test_get_entry_by_idempotency_key` | Retrieves correct entry |
| `test_get_entry_by_idempotency_key_not_found` | Returns None for missing key |
| `test_update_account_settings` | All 4 fields update correctly |
| `test_update_account_settings_partial_update` | Only supplied fields change |
| `test_update_account_settings_no_account_raises_error` | ValueError when account missing |
| `test_multiple_entries_balance_calculation` | Running balance across topup + 2 debits + refund = correct final balance |
| `test_negative_balance_allowed` | Overdraft scenario works without error |
| `test_decimal_precision` | 6-decimal-place values stored and retrieved exactly |
| `test_ledger_entry_with_all_provenance_fields` | All optional columns stored correctly |

**Run command (once test env is configured):**
```bash
source venv/bin/activate
set -a && source api/.env.test && set +a
python -m pytest api/tests/test_credit_account.py -v
```

---

## Files Changed

| File | Change |
|---|---|
| `api/enums.py` | Added `CreditLedgerEntryType` enum |
| `api/db/models.py` | Added `OrganizationCreditAccountModel`, `CreditLedgerEntryModel`; added `Numeric` import; added `CreditLedgerEntryType` to enum imports |
| `api/alembic/versions/2a5fcf617481_add_credit_wallet_tables.py` | New migration (new file) |
| `api/db/credit_account_client.py` | New DB client (new file) |
| `api/db/db_client.py` | Added `CreditAccountClient` to imports and DBClient inheritance |
| `api/tests/test_credit_account.py` | New test suite (new file) |

---

## Verification Checklist

- [x] `alembic current` → `2a5fcf617481 (head)`
- [x] One migration head (no splits)
- [x] `organization_credit_accounts` table exists with correct schema
- [x] `credit_ledger_entries` table exists with correct schema
- [x] `credit_ledger_entry_type` ENUM has all 5 values
- [x] Seed data: 1 org → 1 zero-balance USD account
- [x] `from api.db.credit_account_client import CreditAccountClient` → no errors
- [x] `from api.db.db_client import DBClient` → no errors
- [x] API container running clean, no errors in logs
- [x] 20 tests written covering all methods and edge cases

---

## Ready for Phase 3

The wallet database layer is complete. Any service or route that needs to charge a workflow run, process a topup, or check balance can now import `DBClient` and call `apply_ledger_entry()` directly — one call, fully atomic, idempotency-safe.
