# Phase 4 Complete — Debit Path

**Status:** ✅ All 5 tasks done
**Scope:** OSS usage now debits the prepaid wallet ledger in USD
**Verified:** 83/83 wallet + billing tests pass, plus a live end-to-end debit against the dev DB

---

## What Was Built

The money-**out** path. Phases 2–3 built the wallet and the top-up/adjust APIs; Phase 4 wires the automatic debit that fires whenever a workflow run completes or a knowledge-base document is ingested. The old OSS "credits" counter (`used_dograh_tokens` on a monthly cycle row) is replaced by a proper negative ledger entry in USD, written through the Phase 2 `apply_ledger_entry` primitive.

**This phase does not block anything.** It only records cost. Enforcement (refusing runs on a low balance) is Phase 5. With enforcement still off, balances simply go down as runs complete.

**Core design rules followed:**
- `Decimal` throughout — never float — so the cost landing in the ledger is exact
- The `1.2x` platform markup is unchanged; only the unit changed, credits → USD
- Every debit is **negative** `amount_usd` (money leaving the wallet)
- Idempotency keys `run:{id}` and `kb:{document_id}` make debits safe against ARQ task retries
- Both existing safety behaviours preserved: zero-token early return, and log-and-swallow on failure

---

## Schema Reconciliation (the one real decision)

The design doc's §6 snippet passed `prompt_tokens=`, `completion_tokens=`, `embedding_tokens=` to `apply_ledger_entry`. But the **committed Phase 2 model** has a single column:

```python
tokens_charged = Column(Integer, nullable=True,
    comment="Dograh tokens consumed (call debits only)")
```

There are no three separate token columns. The design snippet was aspirational pseudocode never reconciled with the final schema. Rather than add a migration that contradicts the live Phase 2 tables, the chosen approach stores the **total** (prompt + completion + embedding) in `tokens_charged` — the only option consistent with the schema. Full per-model token detail still lives on `workflow_run.usage_info`.

Likewise the spec's bare `entry_type="debit"` does not exist in the enum. Run and KB debits both use `CreditLedgerEntryType.DEBIT_CALL`, distinguished by the idempotency key (`run:` vs `kb:`) and memo. **No new migration.**

---

## Task 4.1 — USD Cost Formula

**File:** `api/services/workflow_run_billing.py`

- `_calculate_credits` (float, "credits") → `_calculate_cost_usd` (returns `Decimal`)
- Deleted the trailing `* 100` credits conversion and the `_DOLLARS_TO_CREDITS` constant
- Prices and markup are now `Decimal` constants; result quantized to 6 dp with `ROUND_HALF_UP`

**Reference rows — all match exactly:**

| Prompt | Completion | Embedding | Cost USD |
|---|---|---|---|
| 1,000,000 | 0 | 0 | `0.180000` |
| 0 | 1,000,000 | 0 | `0.720000` |
| 0 | 0 | 1,000,000 | `0.024000` |
| 100,779 | 1,472 | 54 | `0.019201` |
| 0 | 0 | 0 | `0.000000` |

---

## Task 4.2 — Debit on Run Completion

**File:** `api/services/workflow_run_billing.py` → `_report_oss_platform_usage`

Replaced `add_oss_call_credits(...)` with:

```python
await db_client.apply_ledger_entry(
    organization_id=organization_id,
    entry_type=CreditLedgerEntryType.DEBIT_CALL,
    amount_usd=-cost_usd,                 # negative
    idempotency_key=f"run:{workflow_run.id}",
    workflow_run_id=workflow_run.id,
    tokens_charged=prompt + completion + embedding,
)
```

Preserved: the early return when all token counts are zero (no entry at all), and the `try/except` that logs and returns without re-raising (a billing failure must never abort artifact upload or integrations). The `run:{id}` key is what makes ARQ retries safe.

---

## Task 4.3 — Debit Knowledge-Base Ingestion

**File:** `api/tasks/knowledge_base_processing.py`

Replaced `add_oss_kb_embed_token_usage(...)` with a debit keyed `kb:{document_id}`, priced at the embedding rate (`$0.02 / 1M`) × `1.2` markup, quantized to 6 dp, with a memo. Reprocessing the same document never double-debits.

---

## Task 4.4 — Removed Superseded Writers

**File:** `api/db/organization_usage_client.py`

Deleted `add_oss_call_credits` and `add_oss_kb_embed_token_usage` (confirmed zero other callers). Left the deprecated `used_dograh_tokens` **column** in place — it simply stops being written. `total_duration_seconds` reporting still works.

---

## Task 4.5 — Billing Tests

**File:** `api/tests/test_workflow_run_billing.py`

Added:
- all five reference rows (parametrized)
- a `Decimal`-return assertion
- a markup-is-applied assertion (so nobody silently deletes the `1.2`)
- one-negative-debit on completion, with correct amount / key / tokens
- zero-token run writes nothing
- idempotency-key stability across a retry
- billing-failure-does-not-raise

---

## Verification

### Live end-to-end debit (real dev DB, org 1, real run id 59)

Simulated the completion billing hook twice:

| Check | Result |
|---|---|
| Debit entry created | `entry_id=178`, type `debit_call` |
| Amount | `-0.019201` (reference row 4) |
| tokens_charged | `102305` (100779 + 1472 + 54) |
| workflow_run_id | `59` |
| Balance moved once | `62.240000 → 62.220799` (−0.019201) |
| Retry wrote no second entry | same `entry_id=178`, balance unchanged |

Confirmed directly in Postgres:
```
 id  | entry_type | amount_usd | balance_after_usd | idempotency_key | workflow_run_id | tokens_charged
 178 | debit_call |  -0.019201 |         62.220799 | run:59          |              59 |         102305
```

### Test suite — 83/83 passing
| File | Tests |
|---|---|
| `test_workflow_run_billing.py` | 19 (Phase 4) |
| `test_credit_account.py` | 19 (Phase 2 DB client) |
| `test_account_resolution.py` | 25 (Phase 3 service) |
| `test_internal_credits.py` | 20 (Phase 3 HTTP) |

All three modified modules import cleanly; API container healthy.

---

## Files Changed

| File | Change |
|---|---|
| `api/services/workflow_run_billing.py` | Formula → USD `Decimal`; completion writes a negative ledger debit |
| `api/tasks/knowledge_base_processing.py` | KB ingestion writes a `kb:{document_id}` debit |
| `api/db/organization_usage_client.py` | Deleted the two superseded credit writers |
| `api/tests/test_workflow_run_billing.py` | Reference rows, markup, idempotency, zero-token, fail-safe tests |

---

## Known Follow-up (pre-existing, not Phase 4)

`test_knowledge_base_processing_embeddings.py::test_embed_texts_in_batches_preserves_order` fails, but it is **pre-existing and unrelated**: verified via `git show HEAD` that the failing line (`get_last_embedding_tokens()` in `_process_batch`, ~line 71) is unchanged original code. The failure is a test-mock gap — `FakeEmbeddingService` lacks that method. Phase 4 only touched the billing block (~line 420).

---

## Ready for Phase 5

The debit path is live and proven against the real database. Phase 5 (enforcement) can now add `_authorize_oss_wallet_balance` in `quota_service.py` behind `ENFORCE_CREDIT_BALANCE`, with the critical fail-closed `except WalletUnavailableError` placed **before** the existing fail-open catch-all.
