# Phase 6 Complete — Operator Billing Console

**Status:** ✅ All 6 tasks done
**Scope:** `Whatsapp_Adapter/` (adapter backend + Next.js dashboard) + 3 small AgentPlatform API changes
**Verified:** 93/93 backend tests, 17/17 live end-to-end checks, 10/10 quota-gate checks, clean type-check + lint + production build

---

## What this phase delivers

The **human-facing other end** of the Phase 3 grant API. Phases 2–5 built the money machine inside AgentPlatform (wallet, internal API, debit path, enforcement). Phase 6 builds the screen where an **operator records "this customer paid me ₹500"** — in the WhatsApp Adapter, which then calls AgentPlatform's `/internal/credits/grant` over the internal Docker network.

The entire phase is dominated by one safety guarantee: **no sequence of double-clicks, refreshes, or network failures can credit a customer twice.**

### The architecture (who talks to whom)

```
  Operator's browser
        │  (session cookie, same-origin)
        ▼
  Next.js dashboard  ──rewrite proxy──►  Adapter backend (app.py)
  (localhost:3001)                        │  holds DOGRAH_INTERNAL_SECRET
                                          │  X-Dograh-Internal-Secret header
                                          ▼
                              AgentPlatform /internal/credits/* (Phase 3)
                                          │
                                          ▼
                                   PostgreSQL wallet
```

The browser **never** holds the internal secret (R7.10). The dashboard talks to the adapter; only the adapter forwards the secret to AgentX over `http://api:8000` (internal Docker DNS).

---

## Task-by-task

### 6.1 — AgentX internal client
`_agentx_internal(method, path, payload)` in `app.py`, mirroring the existing `_send_to_dograh`. Sends `X-Dograh-Internal-Secret`, base from `AGENTX_INTERNAL_BASE` (default `http://api:8000/api/v1`). On a ≥400 response it raises `HTTPException` with AgentX's status and a truncated body — and **never echoes the secret** into any error, log, or response.

### 6.2 — MongoDB collections + indexes
In `startup()`, two new collections:
- `billing_accounts` — **unique** index on `organization_id` (one account per org)
- `payments` — indexes on `(organization_id, created_at DESC)` and `status`
- plus a **unique sparse** index on `client_request_id` (see the double-submit fix below)

### 6.3 — Account routes (all behind `require_operator`)
| Route | Purpose |
|---|---|
| `POST /adapter/billing/resolve` | Preview org + email + balance from an agent's API key, **without saving** |
| `GET /adapter/billing/accounts` | List saved accounts, each with a **live** balance from AgentX |
| `POST /adapter/billing/accounts` | Resolve from an agent's key, then save with the returned email |
| `GET /adapter/billing/accounts/{org_id}` | Live balance + AgentX ledger + local payment records |

The operator never types an org id — it's derived from the API key already stored on the chosen agent. The resolved **email** is shown so they can confirm the right customer before granting money.

### 6.4 — Two-phase payment flow
`POST /adapter/billing/accounts/{org_id}/payments`:
1. Insert the `payments` doc with `status: "pending"` → MongoDB returns `_id`
2. Call AgentX grant with `idempotency_key = f"grant:{_id}"`
3. Success → `confirmed` + `agentx_entry_id` + the server's `credited_usd`
4. Failure → `failed` + the error, left visible and retryable

`POST /adapter/billing/payments/{payment_id}/retry` re-sends the **same** key. A retry either applies once or AgentX reports `duplicate`. The key is derived from the Mongo `_id` — never a timestamp or random value — so two clicks can't make two keys.

### 6.5 — Dashboard pages
- `app/billing/page.tsx` — account list, four summary cards, low-balance highlighting, search, step-by-step empty state
- `app/billing/[orgId]/page.tsx` — balance card, record-payment panel, local payments list, AgentX ledger table
- `components/billing/` — `AddAccountDialog`, `RecordPaymentPanel`, `PaymentsList`, `LedgerTable`, shared `ui.tsx`
- `lib/money.ts` — exact decimal math (BigInt, ROUND_HALF_UP), **no `parseFloat` anywhere**
- `lib/billing.ts` — account health, tones, formatting, request-id generation
- `lib/api.ts` — typed billing helpers + `apiErrorMessage` unwrapping
- One `Sidebar.tsx` entry; matches the existing dark palette and `inputCls`/`labelCls` conventions

### 6.6 — [GATE] Message quota untouched
Verification only, no code. Confirmed `check_quota`, `consume_quota`, the `/whatsapp` gate, and the reset-quota body are byte-for-byte identical to the committed baseline. The only change to any quota touchpoint is the Phase 1 auth guard on the reset route. 10/10 runtime checks pass.

---

## The record-payment experience (6.5 detail)

A deliberate three-stage flow so money is never moved by accident:

**Form →** currency toggle (INR/USD); INR pre-fills `NEXT_PUBLIC_DEFAULT_USD_INR_RATE` (editable, with a reset link); a **live USD preview**; and a warning when the chosen currency will switch what the customer sees on their own billing screen.

**Review →** a summary (account, amount, rate, reference, wallet credit) with a note that it's saved first, then sent with a one-time key.

**Result →** one of four clear outcomes:
- **Credited** — `$X.XX`, with the AgentX entry number
- **Already credited** — AgentX matched the key; no extra money added
- **Failed** — AgentX's reason, with a Retry button
- **Couldn't confirm** — a network blip; "Try again" is safe (same key)

---

## Money handling — why no `parseFloat`

JavaScript numbers are binary floats (`0.1 + 0.2 !== 0.3`). Every amount in `lib/money.ts` is handled as a scaled `BigInt` with `ROUND_HALF_UP`, matching AgentX's `Decimal`. The operator types `500`, the UI sends `50000` (minor units) via integer arithmetic.

**Cross-checked against the backend's own `compute_credited_usd` on six cases:**

| Input | Frontend preview | Backend | Match |
|---|---|---|---|
| 50000 @ 0.01124 | 5.620000 | 5.620000 | ✅ |
| 12345 @ 0.0112 | 1.382640 | 1.382640 | ✅ |
| 99999 @ 0.011999 | 11.998880 | 11.998880 | ✅ |
| 1 @ 0.000001 | 0.000000 | 0.000000 | ✅ |
| 777777 @ 0.012345 | 96.016571 | 96.016571 | ✅ |
| 3 @ 0.004999 | 0.000150 | 0.000150 | ✅ |

The preview is a convenience only — the server recomputes and its figure is authoritative.

---

## Three backend changes beyond the UI

1. **Resubmit protection.** The Phase 2 idempotency key protects one payment *record*, but a double-click or lost HTTP response could still create a *second* record. The form now sends a `client_request_id` it reuses on every resubmit; the adapter returns the existing payment instead of inserting a new one, enforced by a unique index even under a race.
2. **Low-balance threshold passed through.** AgentX's resolve response now includes `low_balance_threshold_usd` so the console can show "Low balance".
3. **Timestamp fix.** The adapter produced `...+00:00Z` (invalid for JS `Date`); now normalized to a parseable ISO-8601 UTC string.

---

## Files changed

**Whatsapp_Adapter/**
| File | Change |
|---|---|
| `app.py` | `_agentx_internal`, 2 collections + indexes, 6 billing routes, resubmit guard, timestamp fix |
| `dashboard/app/billing/page.tsx` | Account list (new) |
| `dashboard/app/billing/[orgId]/page.tsx` | Account detail (new) |
| `dashboard/components/billing/*` | 5 billing components (new) |
| `dashboard/lib/money.ts` | Exact decimal money helpers (new) |
| `dashboard/lib/billing.ts` | Health, tones, formatting (new) |
| `dashboard/lib/api.ts` | Typed billing helpers + error unwrapping |
| `dashboard/components/Sidebar.tsx` | Billing nav entry |
| `dashboard/Dockerfile` | `NEXT_PUBLIC_DEFAULT_USD_INR_RATE` build arg |

**AgentPlatform/**
| File | Change |
|---|---|
| `api/schemas/internal_credits.py` | `low_balance_threshold_usd` on resolve response |
| `api/services/credit_account_service.py` | Return threshold in summary |
| `api/routes/internal_credits.py` | Wire threshold through |
| `docker-compose.override.yaml` | `AGENTX_INTERNAL_BASE` + `DOGRAH_INTERNAL_SECRET` to adapter; `NEXT_PUBLIC_DEFAULT_USD_INR_RATE` to dashboard |

---

## Verification

**Backend tests:** 93/93 pass (internal credits, resolution, credit account, quota wallet, run billing).

**Live end-to-end** through the dashboard proxy (the real browser path) — 17/17:
- No login → 401
- Login → 200; both pages render
- Resolve shows owner email `sanyasirao0301@gmail.com` + threshold field
- Create account for org 1
- Record ₹500 @ 0.01124 → confirmed, credited exactly `5.620000`
- **Resubmit the same form → same payment_id, same ledger entry, no second credit**
- Balance rose by exactly `5.62`; display currency switched to INR
- One local payment, one ledger top-up, tendered ₹500 preserved
- Timestamp parseable by JS (`...Z`, not `...+00:00Z`)

**Secret isolation:** `DOGRAH_INTERNAL_SECRET` absent from the dashboard container's env and its built bundle.

**Quota gate:** 10/10 runtime checks; static diff confirms quota logic unchanged.

**Build quality:** `tsc --noEmit` clean, `eslint` clean on all billing files, `next build` succeeds and lists `/billing` + `/billing/[orgId]`.

**Cleanup:** test credit removed, org 1 restored to `$62.24` USD, test account/payment deleted, local `.next` build output removed.

---

## Open items (not blockers)

- **Visual review pending.** Pages build, load, and their data flows are verified, but I haven't seen them rendered. Open `http://localhost:3001/billing` to review the look.
- **"Low balance" won't show yet** — every account's threshold is `0` and there's no UI to set one (out of 6.5 scope).
- **`NEXT_PUBLIC_DEFAULT_USD_INR_RATE`** (default `0.0112`) is baked in at dashboard build time; changing it means rebuilding the dashboard.
- `node_modules` was installed in `dashboard/` for local type-check/build; it's gitignored.

---

## What's next

- **Phase 7** — customer-facing billing page (replace the OSS short-circuit in `organization_usage.py`, light up the existing v2 ledger table in `ui/`).
- **Phase 8** — deployment wiring (compose config, nginx `/adapter/` location, CI).
