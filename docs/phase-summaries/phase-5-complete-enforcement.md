# Phase 5 Complete — Enforcement

**Status:** ✅ All 6 tasks done
**Scope:** One function in `quota_service.py` + one new test file
**Verified:** 98/98 quota/billing/wallet tests pass, plus a live truth-table check against the dev DB

---

## What Was Built

The gate. Phases 2–4 built the wallet, the APIs, and the debit path, but nothing was blocked. Phase 5 adds a single start-of-session check: when `ENFORCE_CREDIT_BALANCE=true`, an OSS organization whose wallet is empty, negative, or blocked is refused before a new call/chat starts. One check, in one place, behind one flag.

**Core design rules followed:**
- The comparison is `<=`, not `<` — with the default `0.00` threshold, a balance of exactly zero is refused
- **Fail CLOSED**: if the balance can't be read, the run is refused — never silently allowed
- The gate runs at **session start only** — no mid-call polling, warnings, or termination
- `ENFORCE_CREDIT_BALANCE` defaults to **off**, so flipping it to `true` is the deliberate go-live moment; rollback is a config change
- No route edits — all 12 existing call sites already map `has_quota=False` to a refusal

---

## The Truth Table (threshold = 0.00)

| Balance | `is_blocked` | New session | error_code |
|---|---|---|---|
| `1.000000` | false | allowed | — |
| `0.010000` | false | allowed | — |
| `0.000000` | false | **refused** | `insufficient_balance` |
| `-0.140000` | false | **refused** | `insufficient_balance` |
| any | true | **refused** | `account_blocked` |
| lookup fails / no account | — | **refused** | `balance_check_failed` |

A run authorized at `0.000100` that costs `0.019201` completes and leaves `-0.019101`. That is correct and expected — the customer is never cut off mid-sentence; the next session is refused until top-up.

---

## Task 5.1 — The Balance Check

**File:** `api/services/quota_service.py`

- `WalletUnavailableError` — a distinct exception so the caller can fail closed
- `_authorize_oss_wallet_balance(organization_id)`:
  - `None` org id, `get_account` raises, or missing account → raises `WalletUnavailableError`
  - `is_blocked` → refuse with `account_blocked`
  - `balance_usd <= MINIMUM_WALLET_BALANCE_USD` → refuse with `insufficient_balance`
  - otherwise → allow

---

## Task 5.2 — Wired Behind the Flag

Inside `authorize_workflow_run_start`, in the OSS branch, before the Dograh-key logic:

```python
if DEPLOYMENT_MODE == "oss" and ENFORCE_CREDIT_BALANCE:
    wallet = await _authorize_oss_wallet_balance(workflow.organization_id)
    if not wallet.has_quota:
        return wallet
```

With the flag off, the balance is never read.

---

## Task 5.3 — The Fail-Closed Handler (the critical one)

Added `except WalletUnavailableError` **immediately before** the existing `except Exception`:

```python
except WalletUnavailableError as e:
    logger.error("Balance check failed for workflow {}: {}", workflow_id, e)
    return QuotaCheckResult(
        has_quota=False,
        error_code="balance_check_failed",
        error_message="Could not verify your balance. Please try again.",
    )
except Exception as e:
    logger.error(f"Error during quota check: {str(e)}")
    return QuotaCheckResult(has_quota=True)   # unchanged, fail-open
```

Python matches `except` clauses in source order, so the specific handler wins. Without it, a transient DB error would fall into the fail-open catch-all and become a free call — the exact failure R6.8 forbids. The catch-all's fail-open behaviour is left unchanged; it serves hosted paying customers on a different billing system.

---

## Task 5.4 — [GATE] Propagation Verified at All 12 Call Sites

Verification only, no new code. Every site already maps `has_quota=False` to a refusal:

| # | Site | Line | Refusal |
|---|---|---|---|
| 1 | `routes/telephony.py` (outbound REST) | 188 | HTTP 402 |
| 2 | `routes/telephony.py` (outbound webhook) | 782 | `TelephonyError.QUOTA_EXCEEDED` |
| 3 | `routes/telephony.py` (inbound) | 917 | `TelephonyError.QUOTA_EXCEEDED` |
| 4 | `routes/campaign.py` | 557 | HTTP 402 |
| 5 | `routes/campaign.py` | 882 | HTTP 402 |
| 6 | `routes/agent_stream.py` | 102 | WS close 1008 |
| 7 | `routes/public_agent.py` | 269 | HTTP 402 |
| 8 | `routes/workflow_text_chat.py` | 109 | HTTP 402 |
| 9 | `routes/webrtc_signaling.py` | 434 | WS error message |
| 10 | `routes/public_text_chat.py` | 226 | HTTP 402 |
| 11 | `services/campaign/campaign_call_dispatcher.py` | 347 | marks run failed |
| 12 | `services/telephony/ari_manager.py` | 599 | hangs up channel |

The new error codes (`insufficient_balance`, `account_blocked`, `balance_check_failed`) flow through all of them for free.

---

## Task 5.5 — Enforcement Tests

**File:** `api/tests/test_quota_wallet.py` — 8 tests

- Truth table: `1.00`→allowed, `0.01`→allowed, `0.00`→refused, `-0.14`→refused
- `is_blocked` → refused with `account_blocked`
- **`get_account` raises → refused with `balance_check_failed`, not allowed** (the single most important test)
- Missing account → refused
- Flag off → `get_account` never called (asserted on the mock)

---

## Task 5.6 — No Mid-Call Enforcement

Self-reviewed the diff. The change is purely a start-of-session gate: one exception class, one helper that reads the balance once, one call, and the fail-closed handler. No balance polling, no mid-call warnings, no call termination, no change to any in-progress run path. The fail-open catch-all is untouched.

---

## Verification

### Live truth-table check (real dev DB, org 1)

Drove the real `_authorize_oss_wallet_balance` against the live account, cycling its balance / block flag, then restored the original state:

| Balance | Blocked | has_quota | code |
|---|---|---|---|
| `1.000000` | false | True | — |
| `0.010000` | false | True | — |
| `0.000000` | false | False | `insufficient_balance` |
| `-0.140000` | false | False | `insufficient_balance` |
| `100.000000` | true | False | `account_blocked` |

Org 1 restored to `balance=62.240000, is_blocked=false` and confirmed in Postgres.

### Test suite — 98/98 passing
| File | Tests |
|---|---|
| `test_quota_wallet.py` | 8 (Phase 5) |
| `test_quota_service.py` | 11 (existing, no regression) |
| `test_workflow_run_billing.py` | 19 (Phase 4) |
| `test_credit_account.py` | 19 (Phase 2) |
| `test_account_resolution.py` | 25 (Phase 3 service) |
| `test_internal_credits.py` | 20 (Phase 3 HTTP) |

`quota_service` imports cleanly; API container healthy.

---

## Files Changed

| File | Change |
|---|---|
| `api/services/quota_service.py` | `WalletUnavailableError`, `_authorize_oss_wallet_balance`, flag-gated call, fail-closed handler |
| `api/tests/test_quota_wallet.py` | 8 enforcement tests (new) |

No migration, no new endpoints, no route edits.

---

## Ready for Phase 6

The enforcement gate is live and proven. It stays dormant until `ENFORCE_CREDIT_BALANCE` is flipped to `true` — the go-live switch. Phase 6 builds the operator console in the WhatsApp Adapter: the two-phase payment flow that calls the Phase 3 grant API so operators can top up customer wallets.
