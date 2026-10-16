# Prepaid Credit Wallet — Tasks

Work these **in order**. Phases have real dependencies.

## How to use this file

Each task has:

- **Read first** — open these files before writing anything. Do not code against a file you
  have not read.
- **Do** — what to change.
- **Verify** — how you prove it works. A task is not done until this passes.
- **Requirements** — which acceptance criteria in `requirements.md` this satisfies.

Tasks marked **[GATE]** are safety gates. Do not continue past one until its condition is
confirmed. They exist because of real incidents.

Before your first task, read `README.md`, `requirements.md`, and `design.md` in full.

### Conventions you must follow

- Backend: route handlers stay thin, logic in `api/services/`, queries in `api/db/`. See
  `api/AGENTS.md`.
- Money is `decimal.Decimal` in Python and `Numeric` in PostgreSQL. **Never `float`.**
- Run `./scripts/format.sh` before every commit. CI checks formatting.
- Any change to an API request or response schema requires
  `python -m scripts.dump_docs_openapi`. CI checks the committed spec is current.
- Branch off `Billing` (after task 0.2). Never push to `main` or `develop` — both are
  protected. Open a pull request.

---

# Phase 0 — Prerequisites

No new features. Branch hygiene and one safety gate.

## 0.1 Review and commit the pending `Billing` branch edits

**Read first:** `git diff` output for the four files below.

**Do:** Four files on the `Billing` branch have uncommitted changes. Review and commit them
before starting, because later tasks rewrite two of them:

- `api/db/organization_usage_client.py` — a dead method was deleted
- `api/routes/organization_usage.py` — a stale comment fixed
- `api/services/posthog_client.py` — telemetry gated on `ENABLE_TELEMETRY`
- `api/tests/test_workflow_run_billing.py` — tests added for the pricing formula

**Verify:** `git status --short` is clean.

**Requirements:** design.md §15

## 0.2 Merge `develop` into `Billing`

**Do:** `Billing` is 6 commits behind `develop`. Those commits contain the email/OTP login this
feature depends on and a fix for an Alembic multiple-heads bug. Merge `develop` in and resolve
any conflicts, most likely in `api/db/organization_usage_client.py`.

**Verify:**
- `api/routes/auth.py` exists and contains OTP handling
- `api/alembic/versions/otp001_add_otp_columns_to_users.py` exists
- `git log --oneline -1 origin/develop` is an ancestor of your branch

**Requirements:** design.md §15

## 0.3 [GATE] Confirm exactly one Alembic head

**Do:** Run `alembic -c api/alembic.ini heads` and confirm it prints **exactly one** revision.
Write that revision id down — task 2.2 uses it as `down_revision`.

**Why this is a gate:** The API container runs `alembic upgrade head` on startup. Two migrations
were once given the same parent, that command failed, the container crash-looped, and
**production went down**. If you see more than one head, stop and fix it before continuing.

**Verify:** One revision id printed. Record it in your pull request description.

**Requirements:** 9.1

---

# Phase 1 — Adapter authentication

**[GATE] This entire phase ships before anything in Phase 6 is reachable.**

The WhatsApp Adapter has no authentication of any kind — no login, no token, no session — and
nginx publishes it on the public internet. `GET /agents` currently returns every customer's
AgentX API key in plaintext to anyone who asks. We are about to add "give this account money"
to this service.

All work in `Whatsapp_Adapter/`.

## 1.1 Add session configuration and fail-fast startup

**Read first:** `Whatsapp_Adapter/app.py` lines 1–70 (the config block),
`Whatsapp_Adapter/.env.example`

**Do:**
- Add `ADAPTER_ADMIN_PASSWORD` and `ADAPTER_SESSION_SECRET` reads, **with no defaults**
- Raise `RuntimeError` at import time if either is missing, per design.md §2
- Add `itsdangerous` to `requirements.txt`
- Add both variables to `.env.example` with placeholder values and a comment saying to
  generate with `openssl rand -hex 32`

**Verify:** Unset `ADAPTER_ADMIN_PASSWORD` and start the app — it must refuse to start with a
clear message, not start insecurely.

**Requirements:** 1.4, 9.6

## 1.2 Implement login, logout, and the guard dependency

**Read first:** design.md §2 for the full code

**Do:**
- `POST /adapter/auth/login` — `secrets.compare_digest` against the password, then set an
  HttpOnly + Secure + SameSite=Lax cookie holding an `itsdangerous.TimestampSigner` token,
  12-hour max age
- `POST /adapter/auth/logout` — delete the cookie
- `require_operator` dependency — 401 on missing, expired, or badly signed cookie

**Use `secrets.compare_digest`, not `==`.** A plain `==` returns as soon as it finds a
differing character, so response timing leaks how many leading characters were correct.

**Verify:**
- Wrong password → 401
- Correct password → 200, `Set-Cookie` present with `HttpOnly`
- A protected route with no cookie → 401; with the cookie → 200
- A tampered cookie value → 401

**Requirements:** 1.2, 1.3

## 1.3 Protect the existing routes

**Read first:** `Whatsapp_Adapter/app.py` — every `@app.` decorator

**Do:** Add `dependencies=[Depends(require_operator)]` to:
`GET/POST /agents`, `GET/PUT/DELETE /agents/{phone}`, `POST /agents/{phone}/reset-quota`,
`GET /conversations`, `GET /messages/{phone}`, `POST /takeover`, `POST /release`,
`POST /send-message`, `GET /leads`

Leave **unprotected**: `GET /health`, `POST /whatsapp`, `POST /adapter/auth/login`.
`POST /webhook/lead-data` is called by AgentX, not a browser — leave it unprotected for now
and note it as follow-up work.

**Verify:** Every protected route returns 401 with no cookie. `/health` and `/whatsapp` still
work without one.

**Requirements:** 1.1, 1.6

## 1.4 Validate Twilio signatures on the webhook

**Read first:** `Whatsapp_Adapter/app.py` the `POST /whatsapp` handler, design.md §2

**Do:** Validate `X-Twilio-Signature` with
`twilio.request_validator.RequestValidator(TWILIO_AUTH_TOKEN)`. Reject failures with 403.

**The gotcha that will cost you an afternoon:** Twilio signed the **public HTTPS** URL, but
behind nginx your app sees `http://` and an internal hostname, so naive validation fails on
every legitimate request. Rebuild the URL from the `X-Forwarded-Proto` and `Host` headers that
nginx already sets.

**Verify:** A real WhatsApp message from Twilio still gets a reply in production. A hand-crafted
POST to `/whatsapp` with no signature gets 403. **Test against real Twilio traffic** — a unit
test will not catch the URL problem.

**Requirements:** 1.5

## 1.5 Stop leaking AgentX API keys

**Read first:** `Whatsapp_Adapter/app.py` `_serialize_agent`,
`Whatsapp_Adapter/dashboard/app/agents/[phone]/page.tsx`,
`Whatsapp_Adapter/dashboard/lib/api.ts` (the `Agent` interface)

**Do:**
- `_serialize_agent` replaces `api_key` with `api_key_masked` and `api_key_set`, per design.md §2
- `PUT /agents/{phone}` still accepts a full `api_key` for writes
- Update the `Agent` TypeScript interface and the agent editor: show the masked value, treat an
  empty input as "leave unchanged"

**Do not remove `api_key` from the MongoDB document.** The `/whatsapp` handler and Phase 6's
account resolution both need the real key. Only the HTTP response changes.

**Verify:** `GET /agents` contains no `dgr_`-prefixed value anywhere in the response body.
Saving an agent without retyping the key does not wipe it.

**Requirements:** 1.7

## 1.6 Lock down CORS

**Read first:** `Whatsapp_Adapter/app.py` the `CORSMiddleware` block

**Do:** Replace `allow_origins=["*"]` with `[ADAPTER_CORS_ORIGIN]` from the environment,
defaulting to the dashboard URL. Narrow `allow_methods` and `allow_headers`.

Note `allow_credentials=True` with `allow_origins=["*"]` is invalid per the CORS spec and
browsers reject it, so this is required once cookies are in play, not just good hygiene.

**Verify:** A request from the configured origin succeeds. One from another origin is blocked by
the browser.

**Requirements:** 1.8

## 1.7 Add the dashboard login screen

**Read first:** `Whatsapp_Adapter/dashboard/lib/api.ts`,
`Whatsapp_Adapter/dashboard/app/layout.tsx`

**Do:**
- `dashboard/app/login/page.tsx` — single password field, posts to `/adapter/auth/login`
- `lib/api.ts` — add `credentials: "include"` to the shared `req` helper, and redirect to
  `/login` on any 401
- Gate the app so an unauthenticated operator lands on login

**Verify:** Visiting any page with no session redirects to login. Logging in reaches the
dashboard. An expired session redirects back rather than showing a broken page.

**Requirements:** 1.9, 1.10

## 1.8 Fix the broken `/send-message` route

**Read first:** `Whatsapp_Adapter/app.py` the `POST /send-message` handler

**Do:** The handler references `mongo_db`, which is never defined anywhere in the module — the
globals are `mongo_client` and `db` — and queries a `conversations` collection that does not
exist. Every call raises `NameError`. Agent ownership actually lives on `sessions.agent_number`.
Fix it to read from `sessions`.

This is a pre-existing bug in a route you are already touching this phase.

**Verify:** An operator can take over a conversation and send a message end to end.

**Requirements:** 1.1

## 1.9 [GATE] Confirm the adapter is no longer publicly writable

**Do:** From outside the network, confirm every billing-adjacent and mutating route returns 401
without a session, and that no response leaks an API key.

Do not proceed to Phase 6 until this passes.

**Requirements:** 1.1, 1.7

---

# Phase 2 — Wallet and ledger tables

All work in `AgentPlatform/`.

## 2.1 Add the SQLAlchemy models

**Read first:** `api/db/models.py` — specifically `OrganizationModel`,
`OrganizationUsageCycleModel`, and `APIKeyModel` for style. design.md §3 for the full column
list.

**Do:** Add `OrganizationCreditAccountModel` and `CreditLedgerEntryModel` to `api/db/models.py`.

- `Numeric(18, 6)` for every USD amount, `Numeric(18, 8)` for the FX rate. **Not `Float`.**
- `display_currency` (String(3), default `'USD'`) and `display_fx_rate_to_usd`
  (`Numeric(18,8)`, default 1) on the account table. These are presentation metadata only — no
  arithmetic ever reads them.
- Unique constraint on `organization_id` in the account table
- Unique constraint on `idempotency_key` in the ledger table
- Composite index `(organization_id, created_at DESC)`, plain index on `workflow_run_id`
- `ondelete="CASCADE"` on both organization foreign keys

**Verify:** Models import without error. Column types are `Numeric`, confirmed by reading the
file back.

**Requirements:** 2.1, 2.2, 2.4, 2.6, 2.9, 2.10, 2.12, 2.13

## 2.2 Write the Alembic migration

**Read first:** `api/alembic/versions/rag002_add_rag_retrieval_logs.py` as the template.
design.md §12.

**Do:**
- Generate with `./scripts/makemigrate.sh "add credit wallet tables"`, then edit the output.
  **Always read what autogenerate produced** — it sometimes picks up unrelated drift.
- `down_revision` = the revision id from task 0.3
- Create both tables and all indexes
- Seed a zero-balance account for every existing organization with the
  `INSERT ... SELECT ... ON CONFLICT DO NOTHING` in design.md §12, defaulting
  `display_currency` to `'USD'` and `display_fx_rate_to_usd` to `1`
- `downgrade` drops indexes in reverse order, then the tables

**Verify:**
- `./scripts/migrate.sh` applies cleanly
- `alembic heads` still returns **exactly one** revision
- `downgrade` then `upgrade` round-trips
- Every existing organization has an account row with balance `0.000000`

**Requirements:** 9.1, 9.2

## 2.3 Implement `apply_ledger_entry`

**Read first:** design.md §4 for the full implementation. `api/db/base_client.py` for the client
base class. `api/db/organization_client.py` for the existing `on_conflict_do_nothing` pattern.

**Do:** Create `api/db/credit_account_client.py` with `CreditAccountClient.apply_ledger_entry`
and `_lock_account` exactly as designed.

Three things that matter:

1. `SELECT ... FOR UPDATE` on the account row. Without it, two concurrent debits both read the
   old balance and one of them silently vanishes.
2. Catch `IntegrityError` **only** for the idempotency-key conflict. Re-raise everything else.
   Swallowing a bad-foreign-key error hides real bugs.
3. `Decimal` throughout. No `float` crosses this boundary.

**Verify:** Covered by task 2.5.

**Requirements:** 2.4, 2.5, 2.6, 2.7, 2.8

## 2.4 Add the read methods and register the client

**Read first:** `api/db/db_client.py` and `api/db/__init__.py` to see how clients are composed

**Do:**
- `get_account(organization_id)` → balance, threshold, blocked flag, or `None`
- `get_ledger(organization_id, page, limit)` → entries newest first plus a total count
- `get_entry_by_idempotency_key(key)`
- Register `CreditAccountClient` on the composed `db_client` facade so callers reach it the
  same way as every other client

**Verify:** `db_client.get_account(1)` works from a shell with the app environment loaded.

**Requirements:** 4.10, 8.2

## 2.5 Tests for the ledger primitive

**Read first:** `api/tests/` for existing patterns. `api/conftest.py` — note it runs
`alembic upgrade head`, so these need PostgreSQL.

**Do:** Write `api/tests/test_credit_account.py` covering:
- Same idempotency key twice → one entry, balance moved once, second returns `duplicate=True`
- Concurrent debits via `asyncio.gather` → final balance equals the sum of all debits
- **Reconciliation:** `account.balance_usd == SUM(amount_usd)` after a mixed sequence of grants,
  debits, and adjustments
- Balance is allowed to go negative and is not clamped
- A non-idempotency `IntegrityError` propagates rather than being swallowed

**Verify:** `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_credit_account.py -v`

**Requirements:** 2.6, 2.7, 2.8, 2.11

---

# Phase 3 — Internal credits API

## 3.1 Add the configuration constants

**Read first:** `api/constants.py` — see how `DOGRAH_DEVOPS_SECRET` is read

**Do:** Add to `api/constants.py`:
- `DOGRAH_INTERNAL_SECRET = os.getenv("DOGRAH_INTERNAL_SECRET") or None`
- `ENFORCE_CREDIT_BALANCE` — bool, default `False`
- `MINIMUM_WALLET_BALANCE_USD` — `Decimal`, default `Decimal("0.00")`

**Verify:** Import and print all three with nothing set — the defaults must match.

**Requirements:** 6.3, 6.10, 9.6

## 3.2 Implement `verify_internal_secret`

**Read first:** `api/routes/main.py` — `_verify_devops_secret` and
`DOGRAH_DEVOPS_SECRET_HEADER`. Copy that shape.

**Do:** Implement the dependency per design.md §5. Header `X-Dograh-Internal-Secret`. Unset →
503. Absent or mismatched → 401. `secrets.compare_digest` for the comparison.

This must be a **separate** secret from `DOGRAH_DEVOPS_SECRET`, so leaking read-only diagnostic
access does not also grant the ability to move money.

**Verify:** All four cases (unset, absent, wrong, correct) behave as specified.

**Requirements:** 4.1, 4.2, 4.3, 4.4

## 3.3 Implement account resolution

**Read first:** `api/db/api_key_client.py` (`get_api_key_by_hash`), `api/utils/api_key.py`
(`hash_api_key`), `api/db/user_client.py` (`get_user_by_email`). design.md §5.

**Do:** Create `api/services/credit_account_service.py` with resolution by:
- raw API key — `hash_api_key` then `get_api_key_by_hash`, read `organization_id` off the row
- email — `get_user_by_email` (already case-insensitive), then `selected_organization_id`
- explicit `organization_id`

404 when nothing matches. **409 when the user has no selected organization — do not create one
implicitly.**

**Verify:** All three paths resolve a real organization. A bad key gives 404. A user with a null
`selected_organization_id` gives 409.

**Requirements:** 3.1, 3.2, 3.3, 3.4, 3.5, 3.6

## 3.4 Implement grant amount computation

**Read first:** design.md §5 for the exact function

**Do:** Implement `compute_credited_usd` in the service. `Decimal` throughout, quantized to 6 dp
with `ROUND_HALF_UP`.

Rules: reject amounts ≤ 0; USD requires rate absent or exactly 1; non-USD requires a rate > 0.

**The caller must never be able to supply the USD figure.** Accepting a client-computed amount
would let a bug or a tampered request credit an arbitrary sum.

Also implement `to_display(balance_usd, currency, rate)` per design.md §3 — the single place FX
conversion happens for presentation.

**Verify:** `50000` INR at `0.01124` → exactly `Decimal("5.620000")`. Converting `5.620000` back at
the same rate → `Decimal("500.00")`. All rejection cases raise.

**Requirements:** 4.6, 4.7, 4.8

## 3.5 Create and mount the router

**Read first:** `api/routes/main.py` (how routers are included), `api/routes/service_keys.py`
(a thin-handler example), `api/AGENTS.md`

**Do:** Create `api/routes/internal_credits.py`, prefix `/internal/credits`, every route
depending on `verify_internal_secret`:
- `POST /resolve-account`
- `POST /grant`
- `GET /{organization_id}/ledger`
- `POST /adjust` — signed amount, **mandatory** note

Handlers stay thin: validate, delegate to the service, shape the response. Pydantic schemas for
requests and responses. Include the router in `api/routes/main.py`.

A duplicate grant returns **200** with `duplicate: true`, not 409. A retry that finds the work
already done is a success — returning an error would make the adapter's retry button look
broken.

The grant must also set `display_currency` and `display_fx_rate_to_usd` on the account, in the
**same transaction** as the ledger entry. That is what makes the operator's currency choice show up
on the customer's screen. Resolution and ledger responses include those two fields plus the balance
already converted, so no caller does FX arithmetic.

**Verify:** `curl` each endpoint with and without the secret header. Grant the same
idempotency key twice and confirm the balance moves once. Grant in INR and confirm the account's
`display_currency` becomes `INR` and the returned `balance_display` is in rupees.
Run `python -m scripts.dump_docs_openapi` and commit the updated spec.

**Requirements:** 4.5, 4.9, 4.10, 4.11, 4.12, 4.13

## 3.6 Tests for the API layer

**Do:** `api/tests/test_internal_credits.py`:
- Secret: unset → 503, absent → 401, wrong → 401, correct → passes
- `compute_credited_usd` table: INR with rate, USD no rate, USD rate 1, USD rate 2 rejected,
  non-USD no rate rejected, zero rejected, negative rejected
- Duplicate grant returns the original figures with `duplicate: true`
- Resolution: by key, by email, by id, 404, 409

**Verify:** The suite passes.

**Requirements:** 4.1–4.9

---

# Phase 4 — Debit path

## 4.1 Convert the pricing formula to USD `Decimal`

**Read first:** `api/services/workflow_run_billing.py` in full.
`api/tests/test_workflow_run_billing.py`. design.md §6.

**Do:**
- Rename `_calculate_credits` → `_calculate_cost_usd`, return `Decimal`
- Delete the trailing `* _DOLLARS_TO_CREDITS` and the constant itself
- Make the prices and markup `Decimal` constants
- Quantize to 6 dp with `ROUND_HALF_UP`

The markup and prices do not change in substance. Only the unit changes, credits → USD.

**Verify:** All five reference rows in design.md §6 match exactly. In particular
`(100779, 1472, 54)` → `Decimal("0.019201")`.

**Requirements:** 5.2

## 4.2 Debit the ledger on run completion

**Read first:** `api/services/workflow_run_billing.py` `_report_oss_platform_usage`,
`api/tasks/workflow_completion.py`

**Do:** Replace `db_client.add_oss_call_credits(...)` with `apply_ledger_entry` using
`entry_type="debit"`, a **negative** `amount_usd`, `idempotency_key=f"run:{workflow_run.id}"`,
and the three token counts.

Keep both existing behaviours:
- the early return when all token counts are zero — no entry at all, so the ledger stays
  readable
- the `try/except` that logs and returns without re-raising — a billing failure must not abort
  artifact upload or integrations

`run:{workflow_run_id}` is what makes this safe. `workflow_completion.py` calls this inside a
`try/except` and ARQ retries failed tasks, so without the key a retry bills the customer twice.

**Verify:** A completed run produces exactly one debit with the right amount and token counts.
Calling the completion task again produces no second entry.

**Requirements:** 5.1, 5.3, 5.4, 5.6

## 4.3 Debit knowledge-base ingestion

**Read first:** `api/tasks/knowledge_base_processing.py` around the
`add_oss_kb_embed_token_usage` call

**Do:** Replace with a debit keyed `kb:{document_id}`, priced at the embedding rate with the
same markup.

**Verify:** Ingesting a document creates one debit. Reprocessing the same document creates none.

**Requirements:** 5.5

## 4.4 Remove the superseded cycle-counter writers

**Read first:** `api/db/organization_usage_client.py`

**Do:** Delete `add_oss_call_credits` and `add_oss_kb_embed_token_usage`. Their three duplicated
atomic-increment blocks are now replaced by the single `apply_ledger_entry` primitive.

Leave the `used_dograh_tokens` **column** in place — it is already marked deprecated in
`api/db/models.py` and dropping it is unrelated cleanup. It simply stops being written. Keep
`total_duration_seconds` reporting working.

**Verify:** No caller references either deleted method. Duration reporting still works.

**Requirements:** 5.7

## 4.5 Update the billing tests

**Read first:** `api/tests/test_workflow_run_billing.py`

**Do:** Adapt the existing tests to `_calculate_cost_usd` and the USD unit. Add:
- all five reference rows
- an assertion that the `1.2` markup is applied, so nobody deletes it
- a retried completion debits exactly once
- a zero-token run writes no entry

**Verify:** The suite passes.

**Requirements:** 5.2, 5.3, 5.4

---

# Phase 5 — Enforcement

## 5.1 Implement the balance check

**Read first:** `api/services/quota_service.py` in full — especially
`authorize_workflow_run_start` and the `QuotaCheckResult` dataclass. design.md §7.

**Do:** Add `WalletUnavailableError` and `_authorize_oss_wallet_balance` per design.md §7.

- Blocked account → `account_blocked`
- `balance_usd <= MINIMUM_WALLET_BALANCE_USD` → `insufficient_balance`
- Any lookup failure, or a missing account → raise `WalletUnavailableError`

Note the comparison is `<=`, not `<`. With the default threshold of `0.00`, a balance of exactly
zero is refused. See the truth table in `requirements.md`.

**Verify:** Covered by task 5.5.

**Requirements:** 6.1, 6.2, 6.3, 6.9

## 5.2 Wire it in behind the flag

**Read first:** `api/services/quota_service.py` `authorize_workflow_run_start`

**Do:** Inside the OSS branch, before the existing Dograh-key logic:

```python
if DEPLOYMENT_MODE == "oss" and ENFORCE_CREDIT_BALANCE:
    wallet = await _authorize_oss_wallet_balance(workflow.organization_id)
    if not wallet.has_quota:
        return wallet
```

**Verify:** With the flag off, the balance is never read. With it on, a zero-balance
organization is refused.

**Requirements:** 6.1, 6.7, 6.10

## 5.3 Add the fail-closed handler — read this carefully

**Read first:** the very end of `authorize_workflow_run_start`, where
`except Exception` returns `has_quota=True`

**Do:** Add `except WalletUnavailableError` **immediately before** the existing `except
Exception`. Return `has_quota=False` with `error_code="balance_check_failed"`.

**Why this matters more than it looks.** The whole function body is inside one `try:` whose
handler ends with `return QuotaCheckResult(has_quota=True)` and the comment "On unexpected
error, allow the call to proceed". If your balance check raises a plain exception inside that
block, **every transient database error becomes a free call** — the opposite of what we want,
and completely invisible in normal testing. Python matches `except` clauses in source order, so
the specific handler must come first.

Leave the catch-all's fail-open behaviour unchanged. It serves the hosted paths with paying
customers on a different billing system.

**Verify:** Force `get_account` to raise. Authorization must return `has_quota=False`, not
`True`. This is the single most important test in the phase.

**Requirements:** 6.8

## 5.4 [GATE] Verify propagation at all twelve call sites

**Read first:** every `has_quota` usage:
`api/routes/telephony.py` (3 sites), `api/routes/campaign.py` (2),
`api/routes/agent_stream.py`, `api/routes/public_agent.py`,
`api/routes/workflow_text_chat.py`, `api/routes/webrtc_signaling.py`,
`api/routes/public_text_chat.py`, `api/services/campaign/campaign_call_dispatcher.py`,
`api/services/telephony/ari_manager.py`

**Do:** Confirm each maps `has_quota=False` to HTTP 402 or `TelephonyError.QUOTA_EXCEEDED`.
They already should — this task is verification, not new code. If any site ignores the result,
fix that site.

**Verify:** List all twelve in your pull request with the line number and what each returns.

**Requirements:** 6.6, 6.7

## 5.5 Enforcement tests

**Do:** `api/tests/test_quota_wallet.py`:
- Balance `1.00` → allowed; `0.01` → allowed; `0.00` → refused; `-0.14` → refused
- `is_blocked` → refused with `account_blocked`
- `get_account` raises → refused with `balance_check_failed`, **not** allowed
- Flag off → `get_account` is never called (assert on the mock)

**Verify:** The suite passes, especially the fail-closed case.

**Requirements:** 6.2, 6.3, 6.8, 6.9, 6.10

## 5.6 Confirm no mid-call enforcement was added

**Do:** Review your own diff. There must be no balance polling, no mid-call warning, no call
termination on low balance, and no change to any in-progress run path.

A run authorized at `0.000100` that costs `0.019201` completes and leaves `-0.019101`. That is
correct. Do not "fix" it.

**Requirements:** 6.4, 6.5

---

# Phase 6 — Operator console

Requires Phase 1 complete and gate 1.9 passed. All work in `Whatsapp_Adapter/`.

## 6.1 Add the AgentX internal client

**Read first:** `Whatsapp_Adapter/app.py` `_send_to_dograh` — copy its shape. design.md §8.

**Do:** Add `_agentx_internal(method, path, payload)` sending `X-Dograh-Internal-Secret` from
`DOGRAH_INTERNAL_SECRET`, base from `AGENTX_INTERNAL_BASE` (default
`http://api:8000/api/v1`, internal Docker DNS).

**Never** include the secret in any response body, log line, or error message returned to the
browser (R7.10).

**Verify:** The call reaches AgentX from inside the container. A wrong secret gives 401.

**Requirements:** 7.10

## 6.2 Create the MongoDB collections and indexes

**Read first:** `Whatsapp_Adapter/app.py` the `startup` handler where indexes are created

**Do:** Add `billing_accounts` (unique index on `organization_id`) and `payments` (indexes on
`(organization_id, created_at DESC)` and `status`). Create them in the startup handler alongside
the existing ones.

**Verify:** Indexes exist. A duplicate `organization_id` insert is rejected.

**Requirements:** 7.1

## 6.3 Implement the account routes

**Read first:** the existing agents CRUD handlers in `app.py` for style. design.md §8.

**Do:**
- `POST /adapter/billing/resolve` — takes an agent phone number, reads that agent's stored
  `api_key`, calls AgentX `resolve-account`, returns org id + email + balance **without saving**
- `GET /adapter/billing/accounts` — list, each with a live balance fetched from AgentX
- `POST /adapter/billing/accounts` — resolve, then save with the returned email
- `GET /adapter/billing/accounts/{org_id}` — balance plus the AgentX ledger

All protected by `require_operator`.

The operator never types an organization id. It comes from the API key already on the agent
document. Show the resolved email so they can confirm the target before granting money.

**Verify:** Creating an account from a real agent resolves the correct organization and email.
Balances shown match AgentX.

**Requirements:** 7.1, 7.2, 7.8, 7.9

## 6.4 Implement the two-phase payment flow

**Read first:** design.md §8

**Do:**
- `POST /adapter/billing/accounts/{org_id}/payments` —
  1. insert the `payments` doc with `status: "pending"`
  2. call AgentX grant with `idempotency_key = f"grant:{_id}"`
  3. success → `status: "confirmed"` plus `agentx_entry_id` and the server's `credited_usd`
  4. failure → `status: "failed"` plus the error, left visible and retryable
- `POST /adapter/billing/payments/{payment_id}/retry` — re-send the **same** key

**Derive the key from the MongoDB `_id`.** Never from a timestamp or a random value — two
clicks would produce two keys and two grants.

**Verify:** Submit the same payment twice; AgentX shows one entry and the second reports
`duplicate`. Break the network mid-call, confirm the payment shows failed, retry, confirm
exactly one grant exists.

**Requirements:** 7.3, 7.4, 7.5

## 6.5 Build the dashboard pages

**Read first:** `dashboard/app/agents/[phone]/page.tsx` for the visual conventions
(`inputCls`, `labelCls`, `sectionCls`, dark palette). `dashboard/lib/api.ts`.
`dashboard/components/Sidebar.tsx`.

**Do:**
- `app/billing/page.tsx` — account list, balances, low-balance highlighting
- `app/billing/[orgId]/page.tsx` — detail, record-payment form, ledger table, retry button on
  failed payments
- Currency toggle; INR pre-fills `NEXT_PUBLIC_DEFAULT_USD_INR_RATE`, editable; live USD preview
- Show each balance in the account's display currency **and** the underlying USD, so you can
  reconcile
- Typed helpers in `lib/api.ts`; one `Sidebar.tsx` entry

**Send minor units.** The operator types `500`, you send `50000`. Convert with integer
arithmetic on a validated input, never `parseFloat` — JavaScript numbers are floats and cannot
represent every decimal fraction.

The live USD preview is a convenience. The server recomputes and its figure is authoritative.

Make the currency toggle's effect explicit in the UI: whichever currency the operator picks becomes
the currency that customer sees on their own billing screen from then on. Label it so nobody
switches it by accident.

**Verify:** Record ₹500 at `0.01124`, see `$5.62` previewed, confirm, and see the balance rise
by exactly `5.620000` in AgentX. Confirm the account now reports `display_currency: "INR"`.

**Requirements:** 7.6, 7.7, 7.8, 7.9, 7.12, 7.13

## 6.6 [GATE] Confirm the message quota is untouched

**Do:** Verify `check_quota`, `consume_quota`, `POST /agents/{phone}/reset-quota`, and the quota
gate inside `/whatsapp` behave exactly as before. That feature counts WhatsApp messages per
business number and is unrelated to money. It must not have changed.

**Requirements:** 7.11

---

# Phase 7 — Customer-facing billing page

## 7.1 Replace the OSS short-circuit

**Read first:** `api/routes/organization_usage.py` — the
`if DEPLOYMENT_MODE == "oss" ... return await _legacy_mps_credits_response(user)` branch and the
`MPSBillingCreditsResponse` / `MPSCreditLedgerEntryResponse` schemas. design.md §9.

**Do:** Build `MPSBillingCreditsResponse` from the wallet with `billing_version: "v2"`, mapped
per the table in design.md §9. Add optional `prompt_tokens`, `completion_tokens`,
`embedding_tokens` to the ledger entry schema, and `low_balance` plus `is_blocked` to the
response.

Reuse the existing response shape deliberately — the frontend already has a full ledger table
built for it that is currently dead code in OSS. Leave the hosted branch untouched.

**Convert to the display currency here, in the backend.** Send `account.currency` =
`display_currency` and every amount already converted. Do not make the React component do FX. Two
rules: a **grant** row shows the tendered amount verbatim from `paid_amount_minor` / `paid_currency`
with no conversion; a **debit** row shows the USD cost converted at the account rate.

**Verify:** `GET /api/v1/organizations/billing/credits` as a logged-in OSS user returns
`billing_version: "v2"` with real entries, `currency` matching the account's display currency, and
amounts in that currency. Hosted behaviour is unchanged.

**Requirements:** 8.1, 8.2, 8.3, 8.4, 8.5, 8.7, 8.8, 8.9, 8.10

## 7.2 Update the frontend

**Read first:** `ui/src/app/billing/page.tsx` in full — note the existing v2 ledger table,
`formatAmount`, and `canPurchaseCredits`

**Do:**
- Regenerate the typed client from the OpenAPI spec
- Render token counts on debit rows
- Render the low-balance warning
- Reword the amber OSS banner to point at the operator instead of `app.afterlife.ai`

`canPurchaseCredits = isBillingV2 && !isOssMode` already evaluates `false` in OSS, so no
purchase button appears. That is correct — there is no payment gateway. Do not add one.

Currency symbols come for free: the page already calls
`Intl.NumberFormat(style: "currency", currency: ...)`, which renders `₹` for `"INR"` with no code
change. Sending the display currency through is enough.

**Do not render a column total** for the transaction list. Rows are rounded to two decimals
independently, so a hundred sub-rupee debits will not sum exactly to the displayed balance. Show the
balance and the rows; never claim one adds up to the other.

Check the page for any string saying "credits" and change it to the currency-appropriate wording.
Nothing user-facing says "credits".

**Verify:** The page shows the balance in the account's currency, grants with the original ₹ amount
and rate, and debits with token counts. No "Add Credits" button. No column total. The word "credits"
appears nowhere on screen.

**Requirements:** 8.4, 8.5, 8.6, 8.8, 8.11

## 7.3 End-to-end verification

**Do:** Grant ₹500 from the operator console. Confirm the customer page shows the USD balance,
the ₹500 with its rate and USD equivalent, and that a subsequent run appears as a debit with
token counts and a working run link.

**Requirements:** 8.1, 8.2, 8.3, 8.4

---

# Phase 8 — Deployment

## 8.1 Wire configuration into compose

**Read first:** `AgentPlatform/docker-compose.override.yaml` — the `whatsapp-adapter` and
`whatsapp-dashboard` services. design.md §11.

**Do:** Pass every new variable to the right service. Update both `.env.example` files.

`DOGRAH_INTERNAL_SECRET` goes to `whatsapp-adapter` and the `api` service **only**. Never to
`whatsapp-dashboard` — that is a browser bundle and anything in it is public.

Generate secrets with `openssl rand -hex 32`. Never commit them.

**Verify:** `docker compose config` resolves. The dashboard container's environment contains no
secret.

**Requirements:** 9.6, 7.10

## 8.2 Add the nginx location for `/adapter/`

**Read first:** `AgentPlatform/deploy/templates/nginx.remote.conf.template` and
`nginx-local.conf`. design.md §10 for why this matters.

**Do:** Add a single `location /adapter/` block proxying to `whatsapp_adapter`. Leave the
existing adapter locations alone.

**Do not add a `location /billing` block.** The AgentX frontend already serves `/billing` — it
is the customer's own billing page, reached through the `/` catch-all. nginx picks the most
specific prefix, so a `/billing` rule would silently hand every customer to the WhatsApp
adapter instead. No error, no failed deploy, just the wrong page. This is why every new adapter
route is namespaced under `/adapter/`.

**Verify:** `/adapter/billing/accounts` reaches the adapter. `/billing` still reaches the AgentX
frontend and renders the customer billing page.

**Requirements:** 9.5

## 8.3 Fix the red CI checks

**Read first:** `.github/workflows/api-tests.yml`, `.github/workflows/pre-pr-drift-check.yml`

**Do:**
- Diagnose and fix the failing `pytest` job. **Until it is green, your new tests prove nothing
  in CI** — the job was already red so a new failure is invisible. Note this job runs
  `alembic upgrade head` via `api/conftest.py` and would have caught the migration bug that
  took production down, but nobody saw it.
- Run `./scripts/format.sh` and `python -m scripts.dump_docs_openapi` for the drift check
- Add a CI step asserting `alembic heads` returns exactly one revision

**Verify:** Both checks green on a pull request.

**Requirements:** 9.1

## 8.4 Deploy with enforcement off and seed balances

**Do:**
- Confirm `ENFORCE_CREDIT_BALANCE=false` in the production environment
- Deploy; confirm the migration ran and every organization has an account row
- Record opening balances through the operator console
- Verify each against the customer billing page

**Verify:** Balances are correct in both consoles. No customer has been refused anything yet.

**Requirements:** 9.3, 9.4

## 8.5 [GATE] Enable enforcement — go-live

**Do:** Set `ENFORCE_CREDIT_BALANCE=true` and restart the API containers.

**This is the go-live moment.** Up to here everything was measured and recorded but nothing was
refused, so rollback was a config flip. From here, customers can be blocked.

**Verify:**
- A zero-balance organization is refused with HTTP 402 and the top-up message
- A funded organization starts a call normally
- A call already in progress when the flag flipped was **not** interrupted
- After a call, the balance decreased by the expected amount

**Requirements:** 6.10, 9.4
