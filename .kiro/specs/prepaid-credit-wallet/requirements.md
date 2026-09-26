# Prepaid Credit Wallet — Requirements

Read `README.md` first. It explains the two codebases, the vocabulary, and why OSS mode
changes what the existing code does.

## Introduction

### The problem

AgentX already calculates what each workflow run costs. It writes that cost into
`organization_usage_cycles.used_dograh_tokens` and compares it against a hardcoded ceiling
of 500. Three things are wrong with that:

1. **There is no balance.** The counter goes upward against a fixed ceiling. There is no way
   to record "this customer paid me ₹500" and no ledger of what happened.
2. **It resets every month.** The counter is scoped to a calendar month
   (`_calculate_current_period` in `api/db/organization_usage_client.py`). On the 1st, a
   fresh row appears at zero and every customer silently gets their allowance back. We sell
   pay-as-you-go, not a monthly subscription.
3. **Nothing reads it.** `api/services/quota_service.py` has a real enforcement layer wired
   into 12 entry points, but its OSS branch returns "allowed" without ever looking at a
   balance. Running out of money stops nothing.

### The solution

A prepaid wallet per organization, stored in USD, that never resets. Operators record
payments through the WhatsApp Adapter console. Completed runs debit it. New calls and chats
are refused when it is empty. Customers see the balance and a full transaction history.

### Locked decisions

These were decided by the product owner. Do not revisit them without asking.

| # | Decision | Consequence |
|---|---|---|
| 1 | **There is no "credit" unit. Everything is money.** Operator picks INR or USD when recording a payment; the customer's screen shows that same currency. | Stored in USD because the cost formula is natively USD. Converted once on the way in and once for display — never during a debit. The existing `_DOLLARS_TO_CREDITS = 100` factor is deleted. |
| 2 | The wallet is **pay-as-you-go and never resets**. | It gets its own table. It must not live in `organization_usage_cycles`. |
| 3 | All workflows under one account share **one wallet**. | The wallet is keyed by `organization_id`. This already works — every workflow carries an `organization_id`, and OSS signup creates one org per user. |
| 4 | Accounts are keyed by **`organization_id`**, never by a typed-in email. | The operator picks an adapter agent; we resolve its org from the AgentX API key already stored on it. The email is displayed for confirmation only. |
| 5 | The adapter's existing per-agent **message quota is untouched**. | It counts WhatsApp messages per business number. Unrelated to money. Leave it exactly as it is. |
| 6 | If the balance cannot be read, **refuse the run** (fail closed). | A deliberate reversal of today's behaviour, which returns "allowed" on any error. |
| 7 | The customer page shows **detailed numbers, in their own currency**. | Per-top-up and per-run breakdowns including token counts, not a progress bar. A customer who pays in rupees sees rupees. |
| 8 | **A running call or chat is never interrupted.** | Balance is checked only at session start. An in-flight run finishes even if it goes negative. |
| 9 | Balance is allowed to go **negative**, and is recorded honestly. | Not clamped at zero. The next top-up nets out correctly. |
| 10 | **Adapter authentication is built first.** | The adapter is publicly reachable with zero auth today. Nothing else ships before this. |

### The exact blocking rule

This is the single most important behavioural detail in the spec. Get it right.

```
allow the new call / chat session  IF  balance_usd  >  MINIMUM_WALLET_BALANCE_USD
refuse the new call / chat session IF  balance_usd  <= MINIMUM_WALLET_BALANCE_USD
```

`MINIMUM_WALLET_BALANCE_USD` defaults to `0.00`. So:

| Balance | New session? | Running session? |
|---|---|---|
| `5.000000` | Allowed | Continues |
| `0.000100` | Allowed | Continues |
| `0.000000` | **Refused** | Continues to completion |
| `-0.142000` | **Refused** | Continues to completion |

A run that begins at `0.000100` and costs `0.019201` ends at `-0.019101`. That is correct
and expected. The customer was not interrupted, the ledger is truthful, and their next
session is refused until they top up. Exposure is bounded to roughly one run per account.

### Out of scope

- Automated payment collection. No gateway. Grants are manual.
- Interrupting or metering a call while it is in progress. Explicitly rejected — see
  decision 8.
- Currencies beyond INR and USD.
- Automatic FX rate lookup. The rate is operator-supplied, pre-filled from config.
- Changing hosted (`saas`) billing behaviour in any way.

### One vocabulary note

The database tables are called `organization_credit_accounts` and `credit_ledger_entries`, and the
API path is `/internal/credits`. "Credit" there is the accounting verb — to credit an account — not
a points unit. **No user-facing number is ever expressed in "credits".** If you are writing a
conversion factor between credits and money, you have misread this document.

---

## R1 — Adapter authentication

**Build this first.** The adapter has no login of any kind and nginx publishes it on the
public internet. `GET /agents` currently returns every customer's AgentX API key in
plaintext to anyone who asks. We are about to add money endpoints to this service.

**User story:** As the owner, I do not want a money endpoint sitting on the public internet
with no login, and I do not want API keys leaking from the existing one.

### Acceptance criteria

1.1 The adapter SHALL require an authenticated operator session for all billing routes, all
    agent create/update/delete routes, and all conversation control routes (takeover,
    release, send message, leads).

1.2 Authentication SHALL use a credential supplied through server-side configuration and
    SHALL compare it in constant time.

1.3 On successful login the adapter SHALL issue a session cookie that is HttpOnly,
    SameSite=Lax, Secure, cryptographically signed, and expires after a bounded lifetime.

1.4 IF no operator credential or no signing secret is configured, THEN the adapter SHALL
    fail to start. It SHALL NOT start and serve billing routes unauthenticated.

1.5 The Twilio inbound webhook SHALL remain reachable without an operator session, because
    Twilio cannot log in. It SHALL instead validate the Twilio request signature and reject
    requests that fail validation.

1.6 The health endpoint SHALL remain reachable without a session.

1.7 Agent listing and detail responses SHALL NOT include the raw AgentX API key. They SHALL
    return a masked identifier sufficient to tell keys apart. Write operations SHALL still
    accept a full key.

1.8 Cross-origin access SHALL be limited to the configured dashboard origin, replacing the
    current `allow_origins=["*"]`.

1.9 The dashboard SHALL present a login screen when no valid session exists, and SHALL send
    the session cookie on every request to the adapter.

1.10 WHEN a session expires or is rejected, THEN the dashboard SHALL return the operator to
     the login screen rather than showing a broken page.

---

## R2 — Wallet and ledger

**User story:** As the platform, I need a durable place to hold each account's balance and a
complete audit trail of every movement, so that money is never silently lost, duplicated, or
reset.

### Acceptance criteria

2.1 The system SHALL store exactly one credit account per organization, holding a balance
    denominated in USD.

2.2 All monetary values SHALL use exact decimal types — `Numeric` in PostgreSQL and
    `decimal.Decimal` in Python. Floating point SHALL NOT be used for money at any point.

2.3 The balance SHALL be independent of any billing cycle or calendar period. WHEN a new
    calendar month begins, the balance SHALL be unchanged.

2.4 Every change to a balance SHALL be recorded as an append-only ledger entry capturing the
    signed amount, the resulting balance, the entry type, and the timestamp.

2.5 Ledger entries SHALL never be updated or deleted. WHEN a correction is required, the
    system SHALL append a compensating `adjustment` entry.

2.6 Every ledger entry SHALL carry a unique idempotency key. WHEN an entry is submitted with
    a key that already exists, the system SHALL leave the balance unchanged and SHALL report
    the pre-existing entry as a duplicate.

2.7 WHEN multiple debits are applied concurrently to the same account, the final balance
    SHALL equal the starting balance minus the sum of all debits, with no lost updates.

2.8 The balance SHALL be permitted to go negative and SHALL NOT be clamped at zero.

2.9 A `grant` entry SHALL preserve the payment as tendered: the minor-unit amount, the ISO
    currency code, and the FX rate applied to reach USD.

2.10 A `debit` entry for a workflow run SHALL record the originating `workflow_run_id` and
     the prompt, completion, and embedding token counts it was priced from.

2.11 The cached balance on the account SHALL always equal the sum of that organization's
     ledger entry amounts. This SHALL be asserted by an automated test.

2.12 The stored balance SHALL always be denominated in USD, regardless of the currency the
     customer pays in.

2.13 The account SHALL record a display currency and a display FX rate. These SHALL be used only
     for presentation and SHALL NOT participate in any balance calculation, debit, or enforcement
     decision.

2.14 The display FX rate SHALL be the rate from the account's most recent grant, so that a
     customer's displayed balance does not change when market rates change.

---

## R3 — Account resolution

**User story:** As an operator, I want to credit the right account without memorising
internal ids or risking a typo sending someone else's money.

### Acceptance criteria

3.1 The system SHALL resolve an organization from a raw AgentX API key by hashing the key
    and matching it against the stored hash.

3.2 The system SHALL resolve an organization from an email address, matched
    case-insensitively, through the account owner.

3.3 The system SHALL accept an explicit `organization_id`.

3.4 A resolution response SHALL include the `organization_id`, the owner's email, and the
    current balance, so the operator can visually confirm the target before granting money.

3.5 IF the supplied identifier matches no organization, THEN the system SHALL respond 404
    naming what was not found.

3.6 IF a resolved user exists but has no selected organization, THEN the system SHALL respond
    409 and SHALL NOT create an organization implicitly.

3.7 All workflows owned by an organization SHALL debit that organization's single account,
    regardless of which workflow, adapter agent, or API key initiated the run.

---

## R4 — Grant API

**User story:** As the adapter, I need a trustworthy way to push a recorded payment into
AgentX that cannot be replayed, guessed, or reached from a browser.

### Acceptance criteria

4.1 Every internal credits endpoint SHALL require a shared secret supplied in a dedicated
    request header, compared in constant time.

4.2 The shared secret SHALL be distinct from any existing secret in the system, so that
    read-only diagnostic access and money-moving access are not the same credential.

4.3 IF the shared secret is not configured on the server, THEN the endpoint SHALL respond 503
    and SHALL NOT process the request.

4.4 IF the supplied secret is absent or does not match, THEN the endpoint SHALL respond 401
    and SHALL NOT process the request.

4.5 The grant endpoint SHALL require a caller-supplied idempotency key and SHALL be safe to
    retry any number of times with that key.

4.6 The USD amount credited SHALL be computed server-side from the tendered amount and the FX
    rate. The caller SHALL NOT be able to submit a USD figure that disagrees with the
    tendered amount.

4.7 WHEN the tendered currency is USD, the endpoint SHALL accept an FX rate of exactly 1 or
    no rate at all. WHEN the tendered currency is not USD, a rate greater than zero SHALL be
    required.

4.8 The endpoint SHALL reject tendered amounts that are zero or negative.

4.9 The endpoint SHALL return the credited USD amount, the resulting balance, the ledger entry
    id, and whether the request was a duplicate.

4.10 A read endpoint SHALL return an account's balance and a paginated ledger, newest first.

4.11 An adjustment endpoint SHALL permit a signed correction with a mandatory note, under the
     same authentication and idempotency rules.

4.12 A grant SHALL set the account's display currency and display FX rate to the currency and rate
     supplied with that payment, in the same transaction as the ledger entry.

4.13 Resolution and read responses SHALL include the display currency, the display rate, and the
     balance already converted to the display currency, so callers do not perform FX arithmetic.

---

## R5 — Debiting runs

**User story:** As the platform, I want every completed run to reduce the balance by exactly
its computed cost, exactly once.

### Acceptance criteria

5.1 WHEN a workflow run completes in OSS mode, the system SHALL debit the owning
    organization's account by the cost produced by the existing pricing formula.

5.2 The pricing formula SHALL be unchanged in substance: prompt, completion, and embedding
    tokens at their per-million rates, multiplied by the 1.2 platform markup. Only the output
    unit changes, from credits to USD.

5.3 The debit SHALL be idempotent on `workflow_run_id`. WHEN the completion background task
    is retried, the balance SHALL change exactly once.

5.4 WHEN a run produced no billable tokens, the system SHALL record no ledger entry.

5.5 WHEN knowledge-base ingestion consumes embedding tokens, the system SHALL debit the
    account, idempotent on the document identifier.

5.6 IF a debit fails, THEN the failure SHALL be logged with the organization id and run id,
    and SHALL NOT prevent the remainder of run completion from finishing.

5.7 The monthly usage cycle row MAY continue recording duration for reporting, but SHALL NOT
    be the source of truth for any balance, and SHALL no longer receive credit writes.

---

## R6 — Enforcement

**User story:** As the platform owner, I want an empty balance to stop the next session,
without ever cutting off a conversation that is already happening.

### Acceptance criteria

6.1 WHEN a new workflow run is authorized in OSS mode, the system SHALL read the
    organization's balance.

6.2 IF the balance is less than or equal to `MINIMUM_WALLET_BALANCE_USD`, THEN authorization
    SHALL fail with a distinct error code and a message telling the customer to top up. See
    "The exact blocking rule" above for the full truth table.

6.3 `MINIMUM_WALLET_BALANCE_USD` SHALL default to `0.00`, SHALL be expressed in USD, and
    SHALL be configurable.

6.4 The balance SHALL be checked only at the start of a call or chat session. The system SHALL
    NOT check, throttle, warn, or terminate based on balance while a run is in progress.

6.5 A run that has been authorized SHALL be allowed to complete and be debited in full, even
    if the resulting balance is negative.

6.6 A failed authorization SHALL surface as HTTP 402 on REST entry points and as the existing
    quota-exceeded signal on telephony and websocket entry points, matching how current quota
    failures already propagate.

6.7 Enforcement SHALL cover every existing authorization call site: outbound and inbound
    telephony, campaigns, WebRTC signalling, agent stream, workflow text chat, public agent
    trigger, public text chat, the campaign dispatcher, and the ARI manager.

6.8 IF the balance cannot be read for any reason, THEN authorization SHALL fail. This outcome
    SHALL NOT be converted into "allowed" by the existing catch-all error handler in
    `quota_service.py`.

6.9 An account SHALL support an explicit block flag that refuses authorization regardless of
    balance.

6.10 Enforcement SHALL be controlled by a configuration flag defaulting to **off**, so the
     feature can be deployed and balances seeded before any customer is refused.

---

## R7 — Operator console

**User story:** As an operator, I want to record that someone paid me and see it land,
without a double-click charging them twice.

### Acceptance criteria

7.1 The adapter SHALL maintain a billing account record linking an AgentX `organization_id` to
    a display label, the owner's email, and zero or more adapter agents.

7.2 WHEN an operator adds a billing account, the adapter SHALL resolve the `organization_id`
    from the AgentX API key already stored on the chosen agent, and SHALL display the resolved
    email for confirmation before saving.

7.3 The adapter SHALL record a payment in its own database before calling AgentX, and SHALL
    use that local record's identifier to derive the idempotency key.

7.4 IF the call to AgentX fails, THEN the payment SHALL remain visible in a failed state and
    SHALL be retryable. A retry SHALL reuse the original idempotency key.

7.5 There SHALL be no sequence of operator actions, double-clicks, or network failures that
    credits an account twice for one payment.

7.6 The operator SHALL be able to enter an amount in INR or USD. WHEN INR is selected, an FX
    rate SHALL be pre-filled from configuration and SHALL remain editable.

7.7 The console SHALL display the resulting USD figure before the operator confirms.

7.8 The console SHALL display each account's balance read live from AgentX, not from a local
    cache.

7.9 The console SHALL display the AgentX ledger for an account, including run debits.

7.10 The shared secret SHALL exist only in the adapter's server-side environment. It SHALL NOT
     be sent to the browser, included in any response body, or referenced by the dashboard
     bundle.

7.11 The existing per-agent message quota behaviour SHALL remain unchanged.

7.12 The currency the operator selects when recording a payment SHALL become the currency the
     customer's billing screen displays.

7.13 The console SHALL display each account's balance in that account's display currency, and SHALL
     also show the underlying USD figure so the operator can reconcile.

---

## R8 — Customer-facing view

**User story:** As a customer, when I log in I want to see my balance, what I paid, and what
each call cost me.

### Acceptance criteria

8.1 The billing page SHALL display the current balance for the logged-in user's organization
    in OSS mode.

8.2 The page SHALL display a paginated transaction history combining grants, debits, and
    adjustments, newest first.

8.3 A grant row SHALL show the amount tendered in its original currency, the FX rate applied,
    and the USD credited.

8.4 A debit row SHALL show the USD charged, the token counts it was priced from, and a link to
    the originating workflow run.

8.5 WHEN the balance is at or below the account's low-balance threshold, the page SHALL display
    a warning.

8.6 The page SHALL NOT offer self-service purchasing, because no payment gateway is connected.
    It SHALL instead direct the customer to contact the operator.

8.7 Existing hosted (non-OSS) billing behaviour SHALL be unaffected.

8.8 Every figure on the page SHALL be shown in the account's display currency, with that currency's
    symbol. No figure SHALL be labelled "credits".

8.9 A grant row SHALL display the amount exactly as tendered, from the stored tendered amount and
    currency, with no FX conversion applied.

8.10 Currency conversion for display SHALL be performed server-side, so the frontend receives
     figures already in the display currency.

8.11 The page SHALL NOT render a column total for the transaction list, because independently
     rounded rows will not always sum to the displayed balance.

---

## R9 — Rollout

**User story:** As the person deploying this, I do not want to accidentally cut off every
existing customer the moment it ships.

### Acceptance criteria

9.1 The schema change SHALL be a single Alembic migration. After it is applied
    `alembic heads` SHALL return exactly one revision. It SHALL have a working `downgrade`.

9.2 The migration SHALL create a zero-balance account for every existing organization, so no
    organization lacks an account row.

9.3 Enforcement SHALL remain disabled until explicitly enabled by configuration, per 6.10.

9.4 The documented rollout order SHALL be: ship adapter authentication, apply the migration,
    seed opening balances, verify balances in both consoles, then enable enforcement.

9.5 New adapter HTTP routes SHALL be mounted under a path prefix that collides with no
    existing AgentX frontend route. The adapter SHALL NOT claim `/billing`, which the AgentX
    frontend already serves.

9.6 Every new configuration variable SHALL be documented. Non-secrets SHALL have safe
    defaults. Secrets SHALL have no default and SHALL cause a loud failure when absent.
