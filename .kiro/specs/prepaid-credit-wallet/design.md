# Prepaid Credit Wallet — Design

Read `README.md` and `requirements.md` first. This document holds the technical decisions and
the actual code shapes. `tasks.md` refers back here rather than repeating it.

---

## 1. Architecture

### Where the money lives, and why

**AgentX owns the balance. The adapter is only a console.**

```
Operator's browser
      │  signed session cookie          (built in Phase 1)
      ▼
Adapter FastAPI  ── X-Dograh-Internal-Secret ──▶  AgentX FastAPI
  MongoDB:                                          │
   billing_accounts (who maps to which org)          ▼
   payments (operator's record of intent)     PostgreSQL:
                                               organization_credit_accounts
                                               credit_ledger_entries
                                                      ▲
                          ┌───────────────────────────┤
                          │                           │
                  quota_service                workflow_run_billing
                  (reads, gates new runs)      (writes debits)
```

Three reasons the balance is not in MongoDB:

1. **Exactly-once accounting.** Debits happen inside AgentX when a run finishes. Putting the
   balance in the same PostgreSQL transaction as the debit is the only way to guarantee a
   retried background job cannot double-charge.
2. **Latency.** Enforcement runs before every call starts. A cross-service HTTP hop there
   would add failure modes and delay to call setup.
3. **The adapter's MongoDB is not reliable enough for money.** Its startup handler catches
   connection failures and falls back to in-process Python dictionaries, silently. Acceptable
   for chat transcripts. Unacceptable for a balance.

The adapter's `payments` collection is the **operator's record of intent**, not a balance.
AgentX's ledger is the balance. The two are reconciled by idempotency key.

### Money only — there is no "credit" unit

**There are no credits in this system.** Every figure a human ever sees is real money in a real
currency. The customer pays ₹500 or $6, and their screen says ₹500 or $6. The words "credit" and
"debit" appear in table and route names in their accounting sense — crediting and debiting an
account — never as a points system with its own exchange rate.

If you find yourself writing a conversion factor between "credits" and money, stop. You have
misread the spec.

The existing code does have a credit unit: `api/services/workflow_run_billing.py` computes
dollars and multiplies by `_DOLLARS_TO_CREDITS = 100` so that 1 credit = 1 cent. **That
multiplication and that constant are deleted** (task 4.1).

### Store in USD, display in the customer's currency

Two separate concerns, and conflating them is the main way to get this wrong.

| | Unit | Why |
|---|---|---|
| **Stored** | Always USD, `Numeric(18,6)` | The cost formula is natively USD — token prices are dollars per million tokens. Storing anything else would mean converting on every single debit. |
| **Displayed** | The account's own currency | The customer sees what they paid in. Set from the currency the operator chose. |

The conversion happens at exactly two edges: **once on the way in** (operator enters ₹500, we
store $5.62) and **once on the way out** (we hold $5.62, the screen renders ₹500). Never in
between, and never during a debit.

Why not store rupees for Indian customers? Because every run would then need an FX conversion
before it could be subtracted, and the moment you changed the rate, every historical ledger row
would stop adding up to the current balance. One stored unit, converted only for display, keeps
the arithmetic exact and the history permanently consistent. This is the standard way to handle
multi-currency balances and it is not negotiable.

Expect to be mildly surprised when you look in the database and see `5.620000` on an account
whose screen says `₹500.00`. That is correct.

### Which rate is used for display

The account stores the rate from its **most recent grant**, not a live global rate. So a customer
who paid ₹500 sees ₹500, not ₹468 because the market moved overnight. The rate only changes when
the operator records a new payment, at which point the new rate applies going forward.

### Two problems the USD decision solves

**A unit trap.** `quota_service.py` has `MINIMUM_DOGRAH_CREDITS_FOR_CALL = 0.10`, which the
legacy code path uses as *dollars* (its own comment says "Require at least $0.10"). But OSS
credits are cents. Comparing a cent-denominated balance against that constant would have meant
one tenth of one cent — a threshold 100× too small, and the kind of bug that stays invisible
until it costs money. With one stored unit there is nothing to convert and nothing to get wrong.

**Float drift.** The current column is `Float`. Fine for a counter, wrong for a balance. A
realistic run costs `0.019201356` USD; adding thousands of those as binary floats accumulates
error. `Numeric` is exact.

Six decimal places is one ten-thousandth of a cent. The cheapest observed run is about `$0.019`,
so there are roughly three orders of magnitude of headroom.

---

## 2. Adapter authentication (Phase 1)

This ships first. Today the adapter has no authentication, `allow_origins=["*"]`, and nginx
proxies it publicly — so `GET /agents` hands every customer's AgentX API key to anyone who
requests it.

### Session mechanism

Two new environment variables, **neither with a default**:

| Variable | Purpose |
|---|---|
| `ADAPTER_ADMIN_PASSWORD` | The operator's password |
| `ADAPTER_SESSION_SECRET` | Key used to sign session cookies |

At import time, fail loudly if either is missing (R1.4):

```python
ADAPTER_ADMIN_PASSWORD = os.getenv("ADAPTER_ADMIN_PASSWORD")
ADAPTER_SESSION_SECRET = os.getenv("ADAPTER_SESSION_SECRET")

if not ADAPTER_ADMIN_PASSWORD or not ADAPTER_SESSION_SECRET:
    raise RuntimeError(
        "ADAPTER_ADMIN_PASSWORD and ADAPTER_SESSION_SECRET must be set. "
        "The adapter will not start without them."
    )
```

Raising at import means the container refuses to start rather than starting insecure. That is
the intent — a crash-looping container is a loud, visible failure. A silently
unauthenticated money endpoint is not.

### Login

`POST /adapter/auth/login`, body `{"password": "..."}`.

```python
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

_signer = TimestampSigner(ADAPTER_SESSION_SECRET)
SESSION_COOKIE = "adapter_session"
SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours


@app.post("/adapter/auth/login")
async def operator_login(request: Request, response: Response):
    data = await request.json()
    supplied = data.get("password") or ""
    if not secrets.compare_digest(supplied, ADAPTER_ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = _signer.sign(b"operator").decode()
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, secure=True, samesite="lax",
        max_age=SESSION_MAX_AGE, path="/",
    )
    return {"success": True}
```

`secrets.compare_digest` rather than `==` so the comparison takes the same time regardless of
how many leading characters match. A plain `==` short-circuits on the first difference, which
leaks the password one character at a time to an attacker who can measure response times.

### The guard dependency

```python
async def require_operator(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        _signer.unsign(token.encode(), max_age=SESSION_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Session expired")
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid session")
```

Applied via `dependencies=[Depends(require_operator)]` on each protected route.

### What is protected and what is not

| Route | Protected? | Why |
|---|---|---|
| `POST /whatsapp` | **No** — Twilio signature instead | Twilio cannot log in |
| `GET /health` | No | Container healthcheck uses it |
| `POST /adapter/auth/login` | No | It is the login |
| `POST/PUT/DELETE /agents*` | Yes | Config mutation |
| `GET /agents*` | Yes | Was leaking API keys |
| `/takeover`, `/release`, `/send-message` | Yes | Acts on live customer conversations |
| `/conversations`, `/messages/*`, `/leads` | Yes | Customer personal data |
| Everything under `/adapter/billing/*` | Yes | Money |

### Twilio signature validation (R1.5)

The webhook stays open, so it must prove the request really came from Twilio:

```python
from twilio.request_validator import RequestValidator

_twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None


async def _verify_twilio_signature(request: Request, form: dict) -> None:
    if _twilio_validator is None:
        logger.warning("TWILIO_AUTH_TOKEN not set — skipping signature validation")
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    if not _twilio_validator.validate(url, form, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
```

Gotcha: the URL Twilio signed is the **public HTTPS** URL, but behind nginx the app sees
`http://` and an internal host. Build the URL from the `X-Forwarded-Proto` and `Host` headers
that nginx already sets, or validation will fail for every legitimate request. Test this
against the real webhook before considering the task done.

### Stop leaking API keys (R1.7)

`_serialize_agent` currently strips only `_id`. Change it to mask the key:

```python
def _serialize_agent(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    raw_key = doc.pop("api_key", "") or ""
    doc["api_key_masked"] = f"{raw_key[:8]}…{raw_key[-4:]}" if len(raw_key) > 12 else ""
    doc["api_key_set"] = bool(raw_key)
    ...
```

`PUT /agents/{phone}` keeps accepting a full `api_key` for writes. The dashboard shows the
masked value and an empty input meaning "leave unchanged".

Do not remove `api_key` from the internal MongoDB document — the `/whatsapp` handler and the
new billing resolution both need the real key. Only the HTTP response changes.

### CORS (R1.8)

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ADAPTER_CORS_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)
```

`allow_credentials=True` combined with `allow_origins=["*"]` is invalid per the CORS spec and
browsers reject it, so a specific origin is required anyway once cookies are in play.

### Dashboard side

`dashboard/lib/api.ts` — the shared `req` helper must send cookies, which `fetch` does not do
cross-origin by default:

```ts
const res = await fetch(`${base}${path}`, {
  headers: { "Content-Type": "application/json" },
  credentials: "include",          // <-- required for the session cookie
  ...options,
});
if (res.status === 401) {
  window.location.href = "/login";  // R1.10
  throw new Error("Not authenticated");
}
```

Plus a `dashboard/app/login/page.tsx` with a single password field.

### Also fix while you are here

`POST /send-message` is dead code. It references `mongo_db`, which is never defined anywhere
(the module globals are `mongo_client` and `db`), and queries a `conversations` collection that
does not exist. Every call raises `NameError`. Agent ownership actually lives on
`sessions.agent_number`. Fix it as part of this phase since you are already editing the route.

---

## 3. Data model

### `organization_credit_accounts`

One row per organization. The migration creates one for every existing organization; new ones
are created on demand.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer, PK | |
| `organization_id` | Integer, FK → `organizations.id` ON DELETE CASCADE | **UNIQUE** |
| `balance_usd` | `Numeric(18,6)` NOT NULL DEFAULT 0 | Cached projection of the ledger. **Always USD.** |
| `display_currency` | String(3) NOT NULL DEFAULT `'USD'` | What the customer's screen shows. `INR` or `USD`. |
| `display_fx_rate_to_usd` | `Numeric(18,8)` NOT NULL DEFAULT 1 | Rate from the most recent grant. Used for display only. |
| `low_balance_threshold_usd` | `Numeric(18,6)` NOT NULL DEFAULT 1.0 | Drives the R8.5 warning only |
| `is_blocked` | Boolean NOT NULL DEFAULT false | Manual kill switch (R6.9) |
| `created_at` | DateTime(timezone=True) NOT NULL | |
| `updated_at` | DateTime(timezone=True) NOT NULL | |

`balance_usd` is a **cache, not a second source of truth**. It is only ever written in the same
transaction as a ledger insert, and it always equals the sum of that org's ledger amounts. A
test asserts this (R2.11).

Why cache it at all: enforcement reads the balance before every call. Summing the whole ledger
each time would get slower forever.

**`display_currency` and `display_fx_rate_to_usd` never affect arithmetic.** No debit, no
enforcement check, and no balance calculation ever reads them. They exist purely so the screen can
render the stored USD figure in the currency the customer paid in. Both are updated by a grant, to
the currency and rate the operator chose.

The conversion, with `fx_rate_to_usd` meaning "how many USD is one unit of the foreign currency":

```python
def to_display(balance_usd: Decimal, currency: str, rate: Decimal) -> Decimal:
    if currency == "USD":
        return balance_usd
    return (balance_usd / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

Worked example: rate `0.01124` means ₹1 = $0.01124. A `$5.620000` balance displays as
`5.620000 / 0.01124 = ₹500.00`. The customer who paid ₹500 sees ₹500.

Do not render a column total anywhere in the UI. Each row is converted and rounded to two decimals
independently, so a column of displayed values will not always sum to the displayed balance — a
2-cent call is `₹1.78` after rounding, and a hundred of those drift from the exact total. Showing
the balance and the rows without claiming one sums to the other avoids the problem entirely.

### `credit_ledger_entries`

Append-only. No UPDATE. No DELETE. Ever.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer, PK | |
| `organization_id` | Integer, FK → `organizations.id` ON DELETE CASCADE, indexed | |
| `entry_type` | Enum(`grant`, `debit`, `adjustment`) NOT NULL | |
| `amount_usd` | `Numeric(18,6)` NOT NULL | **Signed.** Grants positive, debits negative |
| `balance_after_usd` | `Numeric(18,6)` NOT NULL | Snapshot at the time, for audit |
| `idempotency_key` | String(128) NOT NULL **UNIQUE** | See below |
| `note` | Text NULL | Required for `adjustment` |
| `created_by` | String(255) NULL | Operator label, grants only |
| `paid_amount_minor` | Integer NULL | Grants only. `50000` = ₹500.00 |
| `paid_currency` | String(3) NULL | Grants only. `INR` / `USD` |
| `fx_rate_to_usd` | `Numeric(18,8)` NULL | Grants only |
| `workflow_run_id` | Integer, FK → `workflow_runs.id` NULL, indexed | Debits only |
| `prompt_tokens` | Integer NULL | Debits only |
| `completion_tokens` | Integer NULL | Debits only |
| `embedding_tokens` | Integer NULL | Debits only |
| `created_at` | DateTime(timezone=True) NOT NULL, indexed | |

Indexes: unique on `idempotency_key`, composite `(organization_id, created_at DESC)` for the
history page, plain index on `workflow_run_id`.

Why signed amounts in one column rather than separate credit/debit columns: the balance is
`SUM(amount_usd)` with no `CASE` expression, and the reconciliation test becomes trivial.

### Idempotency keys — the whole safety mechanism

The unique constraint on `idempotency_key` is what makes double-charging structurally
impossible. Keys are **deterministic**, derived from the thing being billed, never random:

| Source | Key format | Protects against |
|---|---|---|
| Operator payment | `grant:{payments._id}` | Double-click, network retry, browser refresh |
| Workflow run | `run:{workflow_run_id}` | Retried ARQ completion task |
| KB ingestion | `kb:{document_id}` | Reprocessed document |
| Correction | `adjust:{uuid4}` | Caller-generated, one per intent |

`run:{workflow_run_id}` closes a live bug. `api/tasks/workflow_completion.py` calls
`report_completed_workflow_run_platform_usage` inside a `try/except`, and ARQ re-runs failed
tasks. Today a retry would bill the customer twice.

---

## 4. The balance mutation primitive

One function, in a new `api/db/credit_account_client.py`, used by grants, debits, and
adjustments alike. Everything else in this feature calls it.

```python
@dataclass
class LedgerApplyResult:
    entry_id: int
    amount_usd: Decimal
    balance_usd: Decimal
    duplicate: bool


class CreditAccountClient(BaseDBClient):
    async def apply_ledger_entry(
        self,
        *,
        organization_id: int,
        entry_type: str,
        amount_usd: Decimal,
        idempotency_key: str,
        **provenance,
    ) -> LedgerApplyResult:
        try:
            async with self.async_session() as session:
                async with session.begin():
                    account = await self._lock_account(session, organization_id)

                    new_balance = account.balance_usd + amount_usd

                    entry = CreditLedgerEntryModel(
                        organization_id=organization_id,
                        entry_type=entry_type,
                        amount_usd=amount_usd,
                        balance_after_usd=new_balance,
                        idempotency_key=idempotency_key,
                        **provenance,
                    )
                    session.add(entry)

                    account.balance_usd = new_balance
                    account.updated_at = datetime.now(timezone.utc)

                    await session.flush()      # surfaces IntegrityError inside the block
                    entry_id = entry.id

            return LedgerApplyResult(entry_id, amount_usd, new_balance, duplicate=False)

        except IntegrityError as e:
            if not self._is_idempotency_conflict(e):
                raise
            existing = await self.get_entry_by_idempotency_key(idempotency_key)
            return LedgerApplyResult(
                entry_id=existing.id,
                amount_usd=existing.amount_usd,
                balance_usd=(await self.get_account(organization_id)).balance_usd,
                duplicate=True,
            )
```

`_lock_account` gets the row with a write lock, creating it if absent:

```python
async def _lock_account(self, session, organization_id: int):
    result = await session.execute(
        select(OrganizationCreditAccountModel)
        .where(OrganizationCreditAccountModel.organization_id == organization_id)
        .with_for_update()
    )
    account = result.scalar_one_or_none()
    if account is not None:
        return account

    await session.execute(
        insert(OrganizationCreditAccountModel)
        .values(organization_id=organization_id, balance_usd=Decimal("0"))
        .on_conflict_do_nothing(index_elements=["organization_id"])
    )
    result = await session.execute(
        select(OrganizationCreditAccountModel)
        .where(OrganizationCreditAccountModel.organization_id == organization_id)
        .with_for_update()
    )
    return result.scalar_one()
```

### Two protections, doing different jobs

**`SELECT ... FOR UPDATE` prevents lost updates.** Two debits arriving at once would otherwise
both read balance `1.00`, both compute `1.00 - 0.02`, and both write `0.98` — one debit
vanishes. The row lock forces the second to wait until the first commits, so it reads `0.98`
and writes `0.96`. Locking is per organization: different customers never block each other.

**The unique index catches replays.** The row lock cannot help when the *same* payment is
submitted twice, because they are two legitimate-looking requests. The unique
`idempotency_key` rejects the second at the database level, which is the only place that is
airtight under concurrency (R2.6).

### Rules for this function

- `amount_usd` is always `Decimal`. Never `float`. Callers convert before calling.
- Only catch the idempotency conflict. Any other `IntegrityError` — a bad FK, a missing
  organization — must propagate. Swallowing those hides real bugs.
- `insert(...).on_conflict_do_nothing` is PostgreSQL-specific, imported from
  `sqlalchemy.dialects.postgresql`. The codebase already uses this pattern in
  `api/db/organization_client.py`.

---

## 5. Internal credits API

New router `api/routes/internal_credits.py`, prefix `/internal/credits`, included in
`api/routes/main.py`. Full paths therefore begin `/api/v1/internal/credits/`.

Handlers stay thin per `api/api/AGENTS.md`: they validate, call
`api/services/credit_account_service.py`, and shape the response.

### Authentication

Copy the one existing precedent — `_verify_devops_secret` in `api/routes/main.py`, which
already guards `/health/active-calls`:

```python
INTERNAL_SECRET_HEADER = "X-Dograh-Internal-Secret"


async def verify_internal_secret(
    x_dograh_internal_secret: Annotated[str | None, Header()] = None,
) -> None:
    if not DOGRAH_INTERNAL_SECRET:
        raise HTTPException(503, "Internal credits API is not configured")
    if not x_dograh_internal_secret or not secrets.compare_digest(
        x_dograh_internal_secret, DOGRAH_INTERNAL_SECRET
    ):
        raise HTTPException(401, "Invalid internal secret")
```

FastAPI maps the parameter name `x_dograh_internal_secret` to the header
`X-Dograh-Internal-Secret` automatically.

A **separate** secret from `DOGRAH_DEVOPS_SECRET` (R4.2) — read-only diagnostics and moving
money should not share one credential, so leaking one does not grant the other.

### `POST /api/v1/internal/credits/resolve-account`

Supply exactly one of `api_key`, `email`, `organization_id`.

```jsonc
// request
{ "api_key": "dgr_xxxxxxxx..." }

// 200
{
  "organization_id": 12,
  "email": "clinic@example.com",
  "balance_usd": "4.821500",
  "display_currency": "INR",
  "display_fx_rate_to_usd": "0.01124",
  "balance_display": "428.96",
  "is_blocked": false
}
```

API key resolution — this is why the operator never types an org id:

```python
key_hash = hash_api_key(raw_key)                        # api/utils/api_key.py
api_key_row = await db_client.get_api_key_by_hash(key_hash)   # api/db/api_key_client.py
if api_key_row is None:
    raise HTTPException(404, "No organization found for that API key")
organization_id = api_key_row.organization_id
```

Every adapter agent document already stores this raw key, so the adapter can resolve without
operator input. The adapter holding the key is not a new privilege — the key already
authenticates against that org's API.

Email resolution: `get_user_by_email` in `api/db/user_client.py` matches on
`lower(email)`, then read `user.selected_organization_id`. If it is `None`, return 409 and do
**not** create an organization (R3.6).

### `POST /api/v1/internal/credits/grant`

```jsonc
// request
{
  "organization_id": 12,
  "idempotency_key": "grant:6712ab9f4c8e...",
  "paid_amount_minor": 50000,
  "paid_currency": "INR",
  "fx_rate_to_usd": "0.01124",
  "note": "UPI ref 4432",
  "created_by": "harini"
}

// 200
{
  "organization_id": 12,
  "credited_usd": "5.620000",
  "balance_usd": "10.441500",
  "entry_id": 91,
  "duplicate": false
}
```

The USD figure is computed **server-side** and never accepted from the caller (R4.6):

```python
def compute_credited_usd(
    paid_amount_minor: int, paid_currency: str, fx_rate_to_usd: Decimal | None
) -> Decimal:
    if paid_amount_minor <= 0:
        raise ValueError("paid_amount_minor must be greater than zero")

    currency = paid_currency.upper()
    if currency == "USD":
        if fx_rate_to_usd is not None and fx_rate_to_usd != Decimal("1"):
            raise ValueError("fx_rate_to_usd must be 1 or omitted when currency is USD")
        rate = Decimal("1")
    else:
        if fx_rate_to_usd is None or fx_rate_to_usd <= 0:
            raise ValueError(f"fx_rate_to_usd is required and must be > 0 for {currency}")
        rate = fx_rate_to_usd

    tendered = Decimal(paid_amount_minor) / Decimal(100)
    return (tendered * rate).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
```

Worked example: `50000 / 100 = 500.00` INR, `× 0.01124 = 5.62000` → `5.620000` USD.

**The grant also sets the account's display currency**, in the same transaction as the ledger
insert:

```python
account.display_currency       = paid_currency.upper()
account.display_fx_rate_to_usd = rate
```

So the currency the operator picks in the console becomes the currency the customer sees on their
billing screen. An operator who records a USD payment for an account that previously paid in rupees
switches that customer's display to dollars from then on — which is the correct behaviour, since it
reflects how they actually pay.

This is display metadata only. It never participates in any balance arithmetic.

Why amounts arrive as **minor units** (integer paise/cents) rather than a decimal string: it
is impossible to misread `50000` but easy to mangle `"500.00"` through a JavaScript `number`,
which is a float and cannot represent all decimal fractions. The frontend never does money
arithmetic; it sends integers.

`duplicate: true` returns the original entry's figures and leaves the balance untouched.
The HTTP status is still 200 — a retry that finds the work already done is a success, not an
error. Returning 409 would make the adapter's retry button look broken.

### `GET /api/v1/internal/credits/{organization_id}/ledger?page=1&limit=50`

Newest first, with `balance_usd`, `total_count`, `page`, `limit`, `total_pages`.

### `POST /api/v1/internal/credits/adjust`

Signed `amount_usd`, **mandatory** `note`, same auth and idempotency. The only sanctioned way
to reduce a balance outside of usage — refunds, corrections, goodwill.

---

## 6. Debiting runs

### Formula conversion

In `api/services/workflow_run_billing.py`:

```python
# before
_DOLLARS_TO_CREDITS = 100
def _calculate_credits(prompt, completion, embedding) -> float:
    return (...) * _PLATFORM_MARKUP * _DOLLARS_TO_CREDITS

# after
_INPUT_PRICE_PER_1M  = Decimal("0.15")
_OUTPUT_PRICE_PER_1M = Decimal("0.60")
_EMBED_PRICE_PER_1M  = Decimal("0.02")
_PLATFORM_MARKUP     = Decimal("1.2")
_MILLION             = Decimal("1000000")

def _calculate_cost_usd(prompt: int, completion: int, embedding: int) -> Decimal:
    raw = (
        (Decimal(prompt)     * _INPUT_PRICE_PER_1M  / _MILLION)
        + (Decimal(completion) * _OUTPUT_PRICE_PER_1M / _MILLION)
        + (Decimal(embedding)  * _EMBED_PRICE_PER_1M  / _MILLION)
    )
    return (raw * _PLATFORM_MARKUP).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
```

`_DOLLARS_TO_CREDITS` is deleted. The markup and prices are unchanged in substance (R5.2).

**Reference values — your tests must match these exactly:**

| Prompt | Completion | Embedding | Cost USD |
|---|---|---|---|
| 1,000,000 | 0 | 0 | `0.180000` |
| 0 | 1,000,000 | 0 | `0.720000` |
| 0 | 0 | 1,000,000 | `0.024000` |
| 100,779 | 1,472 | 54 | `0.019201` |
| 0 | 0 | 0 | `0.000000` |

The last realistic row was previously `1.9201356` credits. Same money, new unit.

### The debit call

`_report_oss_platform_usage` currently ends with
`db_client.add_oss_call_credits(organization_id, credits_to_add)`. Replace with:

```python
cost_usd = _calculate_cost_usd(prompt_tokens, completion_tokens, embedding_tokens)

await db_client.apply_ledger_entry(
    organization_id=organization_id,
    entry_type="debit",
    amount_usd=-cost_usd,                       # negative
    idempotency_key=f"run:{workflow_run.id}",
    workflow_run_id=workflow_run.id,
    prompt_tokens=prompt_tokens,
    completion_tokens=completion_tokens,
    embedding_tokens=embedding_tokens,
)
```

Preserve both existing behaviours in that function:

- the early return when all three token counts are zero (R5.4) — no entry at all, not a
  zero-amount entry, so the ledger stays readable;
- the `try/except` that logs and returns without re-raising (R5.6) — a billing failure must
  not abort artifact upload or integrations.

### Knowledge base ingestion

`api/tasks/knowledge_base_processing.py` calls
`add_oss_kb_embed_token_usage(organization_id, embed_tokens)`. Convert to a debit keyed
`kb:{document_id}`, priced with the embedding rate and the same markup.

### Removing the old writers

Delete `add_oss_call_credits` and `add_oss_kb_embed_token_usage` from
`api/db/organization_usage_client.py`. The three duplicated atomic-increment blocks in that
file collapse into the single `apply_ledger_entry` primitive.

`organization_usage_cycles` keeps `total_duration_seconds` and its reporting role.
`used_dograh_tokens` simply stops being written. Leave the column in place — it is already
marked deprecated in `api/db/models.py` and dropping it is unrelated cleanup with no upside
here.

---

## 7. Enforcement

### Where it goes

`api/services/quota_service.py`, inside `authorize_workflow_run_start`, before the existing
Dograh-key logic:

```python
if DEPLOYMENT_MODE == "oss" and ENFORCE_CREDIT_BALANCE:
    wallet = await _authorize_oss_wallet_balance(workflow.organization_id)
    if not wallet.has_quota:
        return wallet
```

All twelve existing call sites inherit this for free, because they already branch on
`quota_result.has_quota` and already map it to HTTP 402 or
`TelephonyError.QUOTA_EXCEEDED`. **No route files need editing for enforcement** (R6.6, R6.7).
Task 5.4 verifies that rather than assuming it.

### The check

```python
MINIMUM_WALLET_BALANCE_USD = Decimal("0.00")   # from api/constants.py


class WalletUnavailableError(Exception):
    """Raised when the wallet balance cannot be determined."""


async def _authorize_oss_wallet_balance(organization_id: int | None) -> QuotaCheckResult:
    if organization_id is None:
        raise WalletUnavailableError("workflow has no organization_id")

    try:
        account = await db_client.get_account(organization_id)
    except Exception as e:
        raise WalletUnavailableError(str(e)) from e

    if account is None:
        raise WalletUnavailableError(f"no credit account for org {organization_id}")

    if account.is_blocked:
        return QuotaCheckResult(
            has_quota=False,
            error_code="account_blocked",
            error_message="Your account has been suspended. Please contact support.",
        )

    if account.balance_usd <= MINIMUM_WALLET_BALANCE_USD:
        return QuotaCheckResult(
            has_quota=False,
            error_code="insufficient_balance",
            error_message=(
                "Your account balance is empty. Please contact support to top up "
                "before starting a new call or chat."
            ),
        )

    return QuotaCheckResult(has_quota=True)
```

Note `<=`, not `<`. With the default threshold of `0.00`, a balance of exactly zero is
refused, matching the truth table in `requirements.md`.

### The fail-open trap — read this carefully

The **entire body** of `authorize_workflow_run_start` sits inside a `try:` whose handler is:

```python
except Exception as e:
    logger.error(f"Error during quota check: {str(e)}")
    # On unexpected error, allow the call to proceed
    return QuotaCheckResult(has_quota=True)
```

If you put the balance check inside that block and let it raise a plain exception, **every
transient database error silently becomes a free call** — the exact opposite of R6.8. This is
the single easiest way to get this feature wrong, and it would be invisible in testing.

The fix is a dedicated exception caught *before* the catch-all. Python evaluates `except`
clauses in source order, so the specific one wins:

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
        return QuotaCheckResult(has_quota=True)      # unchanged
```

Leave the catch-all's fail-open behaviour alone. It serves the hosted paths, which have
paying customers on a different billing system. Changing it is out of scope.

### No mid-call enforcement — by design

The gate runs at session start only. A run authorized at `0.000100` that costs `0.019201`
completes and leaves the balance at `-0.019101`.

This is a **product decision, not an oversight** (decisions 8 and 9 in `requirements.md`). Do
not add balance polling, mid-call warnings, or call termination. A customer must never be cut
off mid-sentence over money. The negative balance is recorded honestly so the next top-up nets
out correctly, and the next session is refused. Exposure is bounded to about one run per
account.

### Error codes

| Condition | `error_code` | Customer-visible message |
|---|---|---|
| `balance_usd <= threshold` | `insufficient_balance` | Balance empty, contact support to top up |
| `is_blocked` | `account_blocked` | Account suspended, contact support |
| Lookup failed | `balance_check_failed` | Could not verify balance, try again |

### The config flag

`ENFORCE_CREDIT_BALANCE`, default `false`. This exists so the migration can ship, balances can
be seeded, and both consoles verified before any customer is refused. **Flipping it to `true`
is the go-live moment** (R6.10, R9.3). Until then everything is measured and recorded but
nothing is blocked, so rollback is a config change.

---

## 8. Operator console

### MongoDB collections

`billing_accounts` — unique index on `organization_id`:

```jsonc
{
  "organization_id": 12,
  "email": "clinic@example.com",       // from AgentX, display only
  "label": "Rehabb Care",
  "agent_numbers": ["+17178976546"],
  "created_at": "...", "updated_at": "..."
}
```

`payments` — the `_id` is the source of the idempotency key:

```jsonc
{
  "_id": ObjectId("6712ab9f4c8e..."),
  "organization_id": 12,
  "paid_amount_minor": 50000,
  "paid_currency": "INR",
  "fx_rate_to_usd": 0.01124,
  "credited_usd": 5.62,                // display only; AgentX is authoritative
  "note": "UPI ref 4432",
  "created_by": "harini",
  "status": "pending",                 // pending | confirmed | failed
  "agentx_entry_id": null,
  "error": null,
  "created_at": "..."
}
```

### The two-phase grant

This is the pattern that makes double-crediting impossible (R7.3–R7.5):

1. Insert the `payments` document with `status: "pending"`. MongoDB returns `_id`.
2. Call AgentX with `idempotency_key = f"grant:{_id}"`.
3. On success: `status: "confirmed"`, store `agentx_entry_id` and the server's `credited_usd`.
4. On failure: `status: "failed"`, store the error. The row stays visible and retryable.

A retry re-sends the **same** key, so AgentX either applies it once or reports `duplicate`.
There is no sequence of clicks, refreshes, or network failures that credits twice.

Never generate the key from a timestamp or a random value. Two clicks would produce two keys
and two grants.

### New routes

All under `/adapter/` — see the routing section for why.

| Route | Purpose |
|---|---|
| `POST /adapter/auth/login` | Operator login (Phase 1) |
| `POST /adapter/auth/logout` | Clear session |
| `POST /adapter/billing/resolve` | Preview resolution before saving an account |
| `GET /adapter/billing/accounts` | List with live balances from AgentX |
| `POST /adapter/billing/accounts` | Create, resolving the org from an agent's API key |
| `GET /adapter/billing/accounts/{org_id}` | Balance plus AgentX ledger |
| `POST /adapter/billing/accounts/{org_id}/payments` | Record a payment |
| `POST /adapter/billing/payments/{payment_id}/retry` | Retry a failed grant |

### Calling AgentX

Mirror the existing `_send_to_dograh` helper in `app.py`:

```python
AGENTX_INTERNAL_BASE = os.getenv("AGENTX_INTERNAL_BASE", "http://api:8000/api/v1")
DOGRAH_INTERNAL_SECRET = os.getenv("DOGRAH_INTERNAL_SECRET", "")


async def _agentx_internal(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{AGENTX_INTERNAL_BASE}/internal/credits{path}"
    headers = {
        "X-Dograh-Internal-Secret": DOGRAH_INTERNAL_SECRET,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.request(method, url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"AgentX: {response.text[:300]}")
    return response.json()
```

`http://api:8000` is internal Docker DNS — the two containers share a network, so this never
leaves the host. Never expose the secret to the dashboard (R7.10): the browser talks to the
adapter, the adapter talks to AgentX.

### Dashboard pages

- `dashboard/app/billing/page.tsx` — account list, balances, low-balance highlighting
- `dashboard/app/billing/[orgId]/page.tsx` — detail, record-payment form, ledger, retry
- `dashboard/app/login/page.tsx` — Phase 1
- `dashboard/lib/api.ts` — typed helpers, `credentials: "include"`, 401 redirect
- `dashboard/components/Sidebar.tsx` — one nav entry

Follow the existing visual conventions in `dashboard/app/agents/[phone]/page.tsx`: the
`inputCls`, `labelCls`, `sectionCls` constants and the dark palette.

The record-payment form has an INR/USD toggle. INR pre-fills the rate from
`NEXT_PUBLIC_DEFAULT_USD_INR_RATE` and leaves it editable, and shows the computed USD live.
That preview is a convenience only — the server recomputes and its figure wins (R7.6, R7.7).

Send the amount as **minor units**: the operator types `500`, you send `50000`. Do the
conversion with integer arithmetic on a validated input, not `parseFloat`.

---

## 9. Customer-facing billing page

### Backend

`api/routes/organization_usage.py` currently short-circuits OSS to
`_legacy_mps_credits_response`, which is why `ui/src/app/billing/page.tsx` always renders a
progress bar in our deployment.

Replace the OSS branch to build the existing `MPSBillingCreditsResponse` shape from the
wallet, with `billing_version: "v2"`.

This is deliberate reuse. That page **already has a complete ledger table** — Date, Activity,
Origin, Run, Delta, Balance, Amount — which is currently dead code in OSS. Filling
`ledger_entries` lights it up with minimal frontend work.

| Response field | Source |
|---|---|
| `billing_version` | `"v2"` |
| `remaining_credits` | `account.balance_usd` |
| `total_credits_used` | Sum of debit magnitudes |
| `account.cached_balance_credits` | `account.balance_usd` converted to display currency |
| `account.currency` | `account.display_currency` |
| `ledger_entries[].credits_delta` | `amount_usd` converted to display currency |
| `ledger_entries[].balance_after` | `balance_after_usd` converted to display currency |
| `ledger_entries[].entry_type` | `grant` / `debit` / `adjustment` |
| `ledger_entries[].amount_minor`, `.amount_currency` | `paid_amount_minor`, `paid_currency` (grants only) |
| `ledger_entries[].workflow_run_id` | Run link |

New fields to add: optional `prompt_tokens`, `completion_tokens`, `embedding_tokens` on the
ledger entry schema, and `low_balance` plus `is_blocked` on the response.

**Convert to the display currency in the backend, not the frontend.** The API returns numbers
already in the customer's currency, plus `account.currency` naming it. Keeping the conversion in one
place means the rate is applied once, by code that has the exact `Decimal` balance, rather than by
JavaScript floats in a React component.

This maps onto the frontend better than it looks. `ui/src/app/billing/page.tsx` already has:

```ts
formatAmount(entry.amount_minor, entry.amount_currency)
// → Intl.NumberFormat(style: "currency", currency: currency || "USD")
```

`Intl.NumberFormat` renders `₹` for `"INR"` with no code change. Sending `display_currency` through
as `account.currency` is enough to make the whole page render in rupees.

Two display rules:

- A **grant** row shows the amount exactly as tendered, from `paid_amount_minor` and
  `paid_currency`. No conversion — it is stored verbatim, so ₹500.00 renders as ₹500.00 with zero
  rounding risk.
- A **debit** row shows the USD cost converted at the account rate. A `$0.019201` call renders as
  about `₹1.71`. Show two decimals and accept that small debits round; see the warning in §3 about
  not rendering a column total.

Leave the hosted branch completely untouched (R8.7).

### Frontend

`canPurchaseCredits = isBillingV2 && !isOssMode` already evaluates `false` in OSS, so the "Add
Credits" button stays hidden with no change — correct, since there is no payment gateway
(R8.6). Reword the existing amber banner to point at the operator instead of `app.afterlife.ai`.

Add token counts on debit rows, and the low-balance warning.

After changing response schemas, regenerate the typed client and run
`python -m scripts.dump_docs_openapi` — CI fails if the committed OpenAPI spec is stale.

---

## 10. The nginx routing collision

### What nginx is doing

One nginx container sits in front of everything on the EC2 box. It is a receptionist: a request
arrives for `https://devagents.afterlife.org.in/<path>`, nginx looks at `<path>`, and decides
which internal container handles it.

Current rules, from `deploy/templates/nginx.remote.conf.template`:

| Path prefix | Goes to |
|---|---|
| `/api/v1/` | AgentX backend |
| `/whatsapp`, `/agents`, `/conversations`, `/messages/`, `/takeover`, `/release`, `/send-message`, `/leads`, `/health` | WhatsApp adapter |
| `/voice-audio/` | MinIO file storage |
| `/` | AgentX frontend (Next.js), the catch-all |

**nginx picks the most specific matching prefix, not the first one listed.** A rule for
`/billing` is more specific than `/`, so it would win.

### The collision

The AgentX frontend already serves a page at `/billing` — it is
`ui/src/app/billing/page.tsx`, the page customers land on when they click Billing after
logging in. It is reached through the `/` catch-all.

If we gave the adapter a rule for `/billing`, then every customer clicking Billing would be
handed to the WhatsApp adapter instead of their own billing page. The adapter has no such
route, so they would get a 404 or raw JSON. **Nothing would error loudly.** No exception, no
alert, no failed deploy. The page would just quietly serve the wrong thing — and it would be
the very page this project exists to improve.

### The fix

Every new adapter route is prefixed `/adapter/`:

- `/adapter/billing/accounts` → adapter
- `/billing` → AgentX frontend, untouched

One nginx location block covers them all:

```nginx
location /adapter/ {
    proxy_pass http://whatsapp_adapter;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}
```

Add it to both `deploy/templates/nginx.remote.conf.template` and the local `nginx-local.conf`.
Leave the existing adapter locations alone.

### The landmine to know about

The existing config already claims `/agents`, `/conversations`, `/leads`, and `/messages/` at
the root for the adapter. If anyone ever adds a page at one of those paths to the AgentX
frontend, it will break in exactly this silent way. Not a problem today. Do not make it worse
by adding more root-level adapter paths.

---

## 11. Configuration

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `ADAPTER_ADMIN_PASSWORD` | adapter | **none, refuses to start** | Operator login |
| `ADAPTER_SESSION_SECRET` | adapter | **none, refuses to start** | Cookie signing key |
| `ADAPTER_CORS_ORIGIN` | adapter | dashboard URL | Replaces the `*` wildcard |
| `DOGRAH_INTERNAL_SECRET` | api **and** adapter | **none, 503 on the API side** | Shared secret for the internal credits API |
| `AGENTX_INTERNAL_BASE` | adapter | `http://api:8000/api/v1` | Internal Docker DNS path |
| `DEFAULT_USD_INR_RATE` | adapter | `0.0112` | Pre-filled, editable FX rate |
| `ENFORCE_CREDIT_BALANCE` | api | `false` | Master switch for refusing runs |
| `MINIMUM_WALLET_BALANCE_USD` | api | `0.00` | Refuse at or below this |

`docker-compose.override.yaml` passes these in. `DOGRAH_INTERNAL_SECRET` goes to the
`whatsapp-adapter` service only — **never** to `whatsapp-dashboard`, which is a browser bundle.

Generate the secrets with `openssl rand -hex 32`. Never commit them. Add them to
`/home/ubuntu/dograh/.env` on the EC2 host.

---

## 12. Migration

One revision. `down_revision` must be the **current single head** — verify with
`alembic heads` first (task 0.3). A second head crash-loops the API container on startup and
has already taken production down once.

Follow the conventions in `api/alembic/versions/rag002_add_rag_retrieval_logs.py`: docstring
header, the four typed module-level identifiers (`revision`, `down_revision`,
`branch_labels`, `depends_on`), `op.create_table` with inline
`sa.ForeignKey(..., ondelete="CASCADE")`, explicit `op.create_index` calls, and a `downgrade`
that drops indexes in reverse order then the tables.

`upgrade` also seeds accounts for existing organizations (R9.2):

```python
op.execute("""
    INSERT INTO organization_credit_accounts
        (organization_id, balance_usd, display_currency, display_fx_rate_to_usd,
         low_balance_threshold_usd, is_blocked, created_at, updated_at)
    SELECT id, 0, 'USD', 1, 1.0, false, now(), now()
    FROM organizations
    ON CONFLICT (organization_id) DO NOTHING
""")
```

Seeding zero is safe because `ENFORCE_CREDIT_BALANCE` defaults to `false`. If you seeded zero
*and* shipped with enforcement on, every existing customer would be blocked instantly.

Create it with `./scripts/makemigrate.sh "add credit wallet tables"`, then edit the generated
file. Always read what autogenerate produced — it sometimes includes unrelated drift.

---

## 13. Testing

### Runs without PostgreSQL

Pure functions and mocked clients. `REDIS_URL` still has to be set —
`api/constants.py` reads it with `os.environ[...]`, which raises when absent.

- `_calculate_cost_usd` against all five reference rows in section 6, plus an explicit
  assertion that removing the markup changes the result (so nobody deletes the `1.2`).
- `compute_credited_usd`: INR with a rate; USD with no rate; USD with rate 1; USD with rate 2
  rejected; non-USD with no rate rejected; zero and negative amounts rejected.
- `verify_internal_secret`: unset → 503; absent → 401; wrong → 401; correct → passes.
- Enforcement decision table: balance `1.00`, `0.01`, `0.00`, `-0.14`; `is_blocked` true;
  lookup raising.
- **`WalletUnavailableError` is caught by the specific handler and not turned into
  `has_quota=True`.** This is the fail-open trap — test it explicitly.
- Flag off → the balance is never read (assert the mock was not called).

### Needs PostgreSQL

`api/conftest.py` runs `alembic upgrade head`, so these run in CI.

- Same idempotency key twice → one entry, balance moved once, second reports `duplicate`.
- Concurrent debits via `asyncio.gather` → final balance equals the sum of all debits.
- Reconciliation: `account.balance_usd == SUM(amount_usd)` after a mixed sequence of grants,
  debits, and adjustments (R2.11).
- Balance goes negative and is not clamped.
- Migration `upgrade` then `downgrade` round trip; `alembic heads` returns exactly one.

### CI is currently red

`pytest` on pull requests into `main` is failing for an undiagnosed reason, and the drift check
fails on a stale `docs/api-reference/openapi.json`. Until pytest is green **your new tests
prove nothing in CI** — the job was already red, so nobody can see a new failure. Task 8.3
covers fixing it. This is not hypothetical: the pytest job runs `alembic upgrade head` and
would have caught the migration bug that took production down, but it was already red so the
signal was invisible.

---

## 14. Sequences

### Operator records ₹500

```
Operator → Dashboard   : 500, INR, rate 0.01124
Dashboard → Adapter    : POST /adapter/billing/accounts/12/payments   (session cookie)
Adapter → MongoDB      : insert payments{status:"pending"} → _id = 6712ab...
Adapter → AgentX       : POST /internal/credits/grant                (internal secret)
                         idempotency_key = "grant:6712ab..."
                         paid_amount_minor = 50000, paid_currency = "INR",
                         fx_rate_to_usd = "0.01124"
AgentX                 : credited = 50000/100 × 0.01124 = 5.620000
                         SELECT ... FOR UPDATE on the account row
                         INSERT ledger{grant, +5.620000, balance_after = 10.441500}
                         UPDATE account.balance_usd = 10.441500
                         (one transaction)
AgentX → Adapter       : credited_usd 5.620000, balance_usd 10.441500, entry_id 91
Adapter → MongoDB      : payments{status:"confirmed", agentx_entry_id:91}
Adapter → Dashboard    : confirmed, new balance
```

If the AgentX call times out, the payment stays `pending`/`failed` and the retry button
re-sends `grant:6712ab...`. AgentX either applies it once or returns `duplicate: true`.

### New call refused at zero

```
Caller → POST /api/v1/telephony/...
quota_service.authorize_workflow_run_start:
    DEPLOYMENT_MODE == "oss", ENFORCE_CREDIT_BALANCE == true
    account.balance_usd = 0.000000
    0.000000 <= 0.00  →  has_quota = False, "insufficient_balance"
Route: HTTP 402, "Your account balance is empty..."
```

### Run completes, debits, and is retried

```
ARQ worker: process_workflow_completion(run_id=8821)
  report_completed_workflow_run_platform_usage(8821)
    _report_oss_platform_usage(run)
      tokens (100779, 1472, 54) → _calculate_cost_usd → 0.019201 USD
      apply_ledger_entry(org=12, "debit", -0.019201, key="run:8821", tokens…)
        SELECT FOR UPDATE → INSERT ledger → UPDATE balance

Task retried later (ARQ retry, or manual replay):
  same key "run:8821" → unique violation → caught → duplicate=True
  balance unchanged. Customer billed once.
```

### A call that overruns the balance

```
balance = 0.000100
New call authorized:  0.000100 > 0.00  →  allowed
Call runs 4 minutes. Not interrupted. Not warned. Not throttled.
Call ends, cost 0.019201
Debit applied: balance = -0.019101        (negative, not clamped)
Next call attempt: -0.019101 <= 0.00  →  refused, 402
Operator grants $5:  balance = 4.980899   (the overrun nets out correctly)
```

---

## 15. Prerequisites and rollout

### Before any code

1. `develop` must be merged into `Billing`. `Billing` is 6 commits behind and lacks both the
   email/OTP login this feature assumes and the alembic multiple-heads fix.
2. The four uncommitted files on `Billing` need review and committing. This design rewrites
   parts of `api/db/organization_usage_client.py` and
   `api/tests/test_workflow_run_billing.py`, both of which have pending edits.
3. `alembic heads` must return exactly one revision before the migration is authored.

### Rollout order

1. **Ship adapter authentication.** Nothing else goes out first.
2. Ship the AgentX backend with `ENFORCE_CREDIT_BALANCE=false`. The migration runs on
   container start and seeds zero-balance accounts.
3. Ship the adapter billing console.
4. Ship the customer-facing page.
5. Record opening balances through the console. Verify each against the customer page.
6. Set `ENFORCE_CREDIT_BALANCE=true`. **This is go-live.**

Up to step 6, rollback is a config flip. Balances are recorded and debited, but nothing is
refused, so a mistake costs accuracy rather than availability.
