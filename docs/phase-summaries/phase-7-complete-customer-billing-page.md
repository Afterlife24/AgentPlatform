# Phase 7 Complete — Customer-Facing Billing Page

**Status:** ✅ All 3 tasks done
**Scope:** AgentPlatform `api/` (OSS billing response) + `ui/` (billing page)
**Verified:** 96/96 backend tests, live end-to-end against the dev DB, UI type-checks + lints clean, both containers healthy

---

## What this phase does

Phases 2–6 built the wallet, the internal API, the debit path, enforcement, and the operator console. **Phase 7 is the customer's own view of their wallet** — the billing page a logged-in OSS customer sees at `/billing` (localhost:3000).

Before this phase, that page was effectively dead in OSS: the backend short-circuited to a legacy "credits" response that only drove a progress bar. Phase 7 lights it up with the **real wallet** — balance, top-ups, and call/KB debits — all in the customer's **display currency** (₹ or $), with token counts on usage rows.

```
Operator records ₹500   →  Phase 3 grant  →  wallet ledger (topup)
Customer makes a call    →  Phase 4 debit  →  wallet ledger (debit_call)
                                                      │
Customer opens /billing  ───────────────────────────►│
                          Phase 7: read the wallet, show it in their currency
```

The elegant part: the frontend **already had a complete v2 ledger table** that was dead code in OSS. Phase 7 fills it rather than rebuilding anything.

---

## The two decisions (both confirmed against the spec, not shortcuts)

### 1. Reuse the "credits"-named schema fields to carry currency amounts

The design.md §9 mapping table *literally dictates* this: `credits_delta ← amount_usd converted`, `balance_after ← balance_after_usd converted`, `cached_balance_credits ← balance converted`. The response schema (`MPSBillingCreditsResponse`) is shared with the hosted MPS path, so renaming fields would break the hosted branch (violating R8.7) and force the frontend table to be rewritten.

**Chosen:** keep the field names, pour display-currency amounts into them, change only the on-screen text. A documenting comment on the schema and the OSS route records that these legacy names carry currency amounts in v2/OSS. **Nothing the customer sees is labelled "credits"** (R8.8).

### 2. Show the single `tokens_charged` total, not a prompt/completion/embedding split

Phase 2's ledger stores one `tokens_charged` column (the decision made then); Phase 4 writes the sum. R8.4 asks a debit row to show "the token counts it was priced from" — a count, which the total satisfies. Splitting would mean reopening Phases 2 and 4 with a new migration for zero customer-visible gain; faking a split would mislead.

**Chosen:** show the honest single total (e.g. "102,305 tokens"). The three split fields are added to the schema (per spec) but stay null for OSS — forward-compatible.

---

## Task 7.1 — Backend: rebuild the OSS response from the wallet

**Files:** `api/services/credit_account_service.py`, `api/routes/organization_usage.py`

- Added `build_oss_billing_view(organization_id, page, limit)` to the service — assembles the whole customer payload from the wallet. **All FX conversion happens here**, in the backend, using the exact `Decimal` balance and the Phase 3 `to_display` + inverse-rate helpers. The frontend never does FX.
- Two display rules enforced:
  - **Grant** row → tendered amount verbatim from `paid_amount_minor` / `paid_currency` (₹500 shows as ₹500, zero rounding).
  - **Debit** row → USD cost converted at the account rate ($0.019201 → −₹1.71).
- Rewired the OSS branch of `get_billing_credits` to a thin `_oss_wallet_billing_response()` that shapes the service output into the response model. **Hosted/MPS branch left completely untouched** (R8.7).
- Schema additions: `tokens_charged`, `prompt_tokens`, `completion_tokens`, `embedding_tokens`, `memo` on the entry; `low_balance`, `is_blocked` on the response — with a comment explaining the legacy field-name reuse.
- Regenerated the committed `docs/api-reference/openapi.json` (byte-exact UTF-8) so the CI drift check passes.

| Response field | Wallet source |
|---|---|
| `billing_version` | `"v2"` |
| `remaining_credits` | balance in display currency |
| `total_credits_used` | sum of debit magnitudes, display currency |
| `account.currency` | `account.display_currency` |
| `account.cached_balance_credits` | balance in display currency |
| `ledger_entries[].credits_delta` | `amount_usd` converted |
| `ledger_entries[].balance_after` | `balance_after_usd` converted |
| `ledger_entries[].amount_minor/.amount_currency` | tendered amount (grants only) |
| `ledger_entries[].tokens_charged` | the single stored total (debits) |
| `ledger_entries[].workflow_run_id/.workflow_id` | run link (org-scoped) |
| `low_balance` / `is_blocked` | from the account |

---

## Task 7.2 — Frontend: light up the page

**Files:** `ui/src/app/billing/page.tsx` (rewritten), `ui/src/app/billing/lib.ts` (new), regenerated `ui/src/client/*`

- Regenerated the typed client from the live OpenAPI spec (new fields present).
- Redesigned the page: balance + total-spent cards in the account's currency, a transaction table with colored credit/debit icons, grant rows showing the tendered amount verbatim, debit rows showing the token count and a working run link.
- **Low-balance warning** (R8.5) and a **suspended-account notice** when `is_blocked`.
- **OSS banner reworded** to point at the operator / account administrator — no more `app.afterlife.ai`, no self-service checkout (R8.6).
- **No "Add Credits" button** in OSS (`canPurchaseCredits` is `false`).
- **No column total** on the ledger (rows round independently; design.md §3).
- The word **"credits" appears nowhere** user-facing — it's "Balance", "Total spent", "Payment received", "Usage".
- A `lib.ts` helper centralizes currency/token/date formatting and entry labeling. Currency symbols come free from `Intl.NumberFormat` once the backend sends `display_currency`.

---

## Task 7.3 — End-to-end verification

Live against the dev DB (org 1): seeded a ₹500 grant at 0.01124 + a call debit, called the real `GET /organizations/billing/credits` endpoint the frontend uses, then cleaned up and restored the account.

- `billing_version: "v2"`, `account.currency: "INR"` ✓
- Grant row: `amount_minor=50000`, `amount_currency="INR"` (verbatim), converted delta ₹500.00 ✓
- Debit row: converted to **−₹1.71**, `tokens_charged=102305`, no `amount_minor` ✓
- `low_balance` + `is_blocked` present ✓

---

## Verification summary

| Check | Result |
|---|---|
| Backend wallet/billing/quota tests | **96/96 pass** (incl. the modified route's suite) |
| Live billing-view service check | 14/14 — grant verbatim, debit −₹1.71, token total |
| Live HTTP e2e via the real endpoint | v2 shape, INR currency, grant + debit rows |
| UI `tsc --noEmit` (whole project) | clean |
| `next lint` on both billing files | No warnings or errors |
| UI production build | billing page compiled |
| API + UI containers | both healthy |

---

## Files changed

| File | Change |
|---|---|
| `api/services/credit_account_service.py` | New `build_oss_billing_view()` + `_entry_display_type()` |
| `api/routes/organization_usage.py` | New `_oss_wallet_billing_response()`; OSS branch rewired; schema fields added |
| `docs/api-reference/openapi.json` | Regenerated (new fields) |
| `ui/src/app/billing/page.tsx` | Rewritten customer billing page |
| `ui/src/app/billing/lib.ts` | New presentation helpers |
| `ui/src/client/*` | Regenerated typed client |

---

## Honest notes

- **Browser visual review still recommended.** The page builds, type-checks, lints clean, and its data flows are verified end-to-end — but a human should confirm the look. (Org 1 is back on USD/$77.84 after test cleanup; INR rendering shows once an INR grant exists.)
- **Pre-existing UI lint errors** in unrelated files (`RagSettings`, `posthog`, `GitHubStarBadge`) block a full `next build`. Not Phase 7, not touched here.
- **"Low balance" needs a non-zero threshold** on the account to trigger; all are `0` now. The warning renders correctly when `balance ≤ threshold`; there is no UI to set the threshold (out of scope).
- **The "Backend connection failed" banner** seen during testing was unrelated — a stray `BACKEND_URL=localhost:8000` in the shell overriding the UI's server-side URL. Fixed by pinning `BACKEND_URL=http://api:8000` in `.env` and clearing the shell var; the billing page itself was never affected.
- **`ui/node_modules`** installed locally for client regen + build; gitignored.

---

## Where things stand

Phases 0–7 are complete and verified: schema, internal API, debit path, enforcement, operator console, and now the customer billing page. The whole prepaid-credit-wallet feature works end-to-end — operator records a payment, the customer sees it and their call costs in their own currency.

**Remaining:** Phase 8 — deployment wiring (compose config, the nginx `/adapter/` location, CI fixes).
