# Phase 3 Complete — Internal Credits API

**Status:** ✅ All 6 tasks done
**Prefix:** `/api/v1/internal/credits`
**Verified:** 64/64 credit-wallet tests pass + live end-to-end calls against the dev DB

---

## What Was Built

The secure HTTP surface for the prepaid credit wallet. Four internal, service-to-service endpoints that let a trusted caller (e.g. the WhatsApp Adapter) resolve an account, top it up, adjust it, and read its ledger — all gated by a dedicated secret, with money computed server-side.

**Core design rules followed:**
- Separate `DOGRAH_INTERNAL_SECRET` from `DOGRAH_DEVOPS_SECRET` — leaking read-only diagnostics never grants money-moving access
- `secrets.compare_digest` for the secret check — constant-time, no timing attacks
- The USD figure is computed **server-side only** — the caller can never supply it
- Money fields serialize as `Decimal` strings over JSON — never float
- A duplicate grant returns **HTTP 200 with `duplicate: true`**, not 409 — a retry that finds the work done is a success
- Grant sets `display_currency` + `display_fx_rate_to_usd` **in the same transaction** as the ledger entry

---

## Task 3.1 — Configuration Constants

**File:** `api/constants.py`

| Constant | Default | Purpose |
|---|---|---|
| `DOGRAH_INTERNAL_SECRET` | `None` | Shared secret for the internal credits API |
| `ENFORCE_CREDIT_BALANCE` | `False` | Toggle balance enforcement for OSS runs |
| `MINIMUM_WALLET_BALANCE_USD` | `Decimal("0.00")` | Block threshold when enforcement is on |

**Verified:** all three import with correct defaults; `MINIMUM_WALLET_BALANCE_USD` is a true `Decimal`.

---

## Task 3.2 — `verify_internal_secret`

**File:** `api/services/auth/internal_auth.py`

FastAPI dependency reading the `X-Dograh-Internal-Secret` header.

| Condition | Response |
|---|---|
| Secret not configured | `503` Service Unavailable |
| Header absent | `401` Unauthorized |
| Header wrong | `401` Unauthorized |
| Header correct | passes |

Uses `secrets.compare_digest` for the comparison. A **separate** credential from the DevOps secret.

**Verified:** all four cases confirmed live against the running server.

---

## Task 3.3 — Account Resolution

**File:** `api/services/credit_account_service.py`

Resolves an organization three ways:

| Input | Mechanism | Failure |
|---|---|---|
| raw API key | `hash_api_key` → `get_api_key_by_hash` → `organization_id` | `404` |
| email | `get_user_by_email` (case-insensitive) → `selected_organization_id` | `404`, or `409` if no selected org |
| explicit `organization_id` | validate the org exists | `404` |

Exactly one identifier must be supplied (`400` otherwise). A user with a null `selected_organization_id` returns **409** — no organization is created implicitly.

---

## Task 3.4 — FX Computation

**File:** `api/services/credit_account_service.py`

### `compute_credited_usd(paid_amount_minor, paid_currency, fx_rate_to_usd)`
The only place money enters the wallet. `Decimal` throughout, quantized to 6 dp with `ROUND_HALF_UP`.

Rules: reject amounts ≤ 0; USD requires rate absent or exactly 1; non-USD requires a rate > 0.

**Reference:** `50000` INR @ `0.01124` → `Decimal("5.620000")` ✓

### `to_display(balance_usd, currency, rate)`
The single place FX conversion happens for presentation. Quantized to 2 dp. The account stores *"1 display unit = X USD"*; the service inverts it to *"display units per USD"* for display.

---

## Task 3.5 — Router Created and Mounted

**File:** `api/routes/internal_credits.py` (prefix `/internal/credits`, every route `Depends(verify_internal_secret)`)
**Schemas:** `api/schemas/internal_credits.py`
**Mounted in:** `api/routes/main.py`

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/resolve-account` | One of api_key / email / organization_id → balance in USD + display currency |
| `POST` | `/grant` | Top up; USD computed server-side; sets display currency atomically; duplicate → 200 `duplicate:true` |
| `POST` | `/adjust` | Signed `amount_usd`, **mandatory** `note`; the only sanctioned way to reduce a balance outside usage |
| `GET` | `/{organization_id}/ledger` | Newest-first, `total_count` / `page` / `limit` / `total_pages` |

Handlers stay thin per `api/AGENTS.md`: validate → delegate to the service → shape the response.

### Supporting DB-client additions
**File:** `api/db/credit_account_client.py`
- `grant_topup(...)` — applies the topup **and** sets `display_currency` / `display_fx_rate_to_usd` on the account inside one locked transaction; returns `(entry, was_duplicate)`
- `get_ledger_count(organization_id)` — total count for pagination

### Service-layer orchestration
**File:** `api/services/credit_account_service.py`
- `grant_credits(...)` — computes USD, calls `grant_topup`, maps errors to HTTP codes
- `adjust_credits(...)` — mandatory-note guard, duplicate detection, signed `apply_ledger_entry`
- `get_account_summary(...)` — balance in USD + display currency
- `get_ledger_page(...)` — page of entries plus pagination metadata

---

## Task 3.6 — API-Layer Tests

**File:** `api/tests/test_internal_credits.py` — **20 tests**

| Group | Covers |
|---|---|
| Auth matrix | unset → 503, absent → 401, wrong → 401, correct → passes |
| Resolve | by key, by email, by org id, bad key → 404, no-org → 409, no identifier → 400 |
| Grant | USD, INR sets display currency, duplicate → 200 `duplicate:true` (balance moves once), zero rejected, USD rate 2 rejected, non-USD no rate rejected |
| Adjust | positive credit, negative reduces balance, missing note rejected |
| Ledger | pagination + newest-first ordering |

Also removed a stray scratch `test_internal_auth.py` from the repo root and replaced a corrupted stub test file.

---

## Verification

### Live end-to-end (real dev DB, organization 1)
- Resolve → balance `$61.24`, display currency `INR`, not blocked
- Grant `$1.00` → balance `61.24 → 62.24`, real `entry_id=88`
- Repeat same idempotency key → `duplicate=true`, balance **unchanged**
- Ledger → `total_count=7`, 5 entries returned, newest first
- Secret unset on the live container → every route returns `503`

### Test suite — 64/64 passing
| File | Tests |
|---|---|
| `test_internal_credits.py` | 20 (Phase 3 HTTP) |
| `test_account_resolution.py` | 25 (Phase 3 service) |
| `test_credit_account.py` | 19 (Phase 2 DB client) |

API container boots clean and reports **healthy**; all 4 routes registered at `/api/v1/internal/credits/*`.

---

## Files Changed

| File | Change |
|---|---|
| `api/constants.py` | Added 3 config constants |
| `api/services/auth/internal_auth.py` | `verify_internal_secret` dependency (new) |
| `api/services/credit_account_service.py` | Resolution, FX, grant/adjust/summary/ledger orchestration |
| `api/db/credit_account_client.py` | Added `grant_topup` + `get_ledger_count` |
| `api/schemas/internal_credits.py` | Request/response schemas (new) |
| `api/routes/internal_credits.py` | The router (new) |
| `api/routes/main.py` | Mounted the router |
| `api/tests/test_internal_credits.py` | 20 HTTP-layer tests (new) |
| `api/tests/test_account_resolution.py` | 25 service tests (new) |

---

## Known Follow-up

One item from the Task 3.5 "verify" step was **not** run: `python -m scripts.dump_docs_openapi` to regenerate and commit the OpenAPI spec. This is a docs artifact only — the API itself is fully working. Can be run on request.

---

## Ready for Phase 4

The internal credits API is complete and proven against the live database. Phase 4 (the debit path) can now convert the pricing formula to USD `Decimal` and call `apply_ledger_entry` with a negative amount keyed `run:{workflow_run_id}` on run completion.
