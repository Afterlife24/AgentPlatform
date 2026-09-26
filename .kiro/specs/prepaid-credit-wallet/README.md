# Prepaid Credit Wallet — Start Here

Read this file completely before touching any code. It gives you the context the rest of
the spec assumes.

## What we are building, in one paragraph

Customers use AgentX to build voice and chat AI agents. Running an agent costs us money
(LLM tokens). Right now we measure that cost but we have nowhere to keep a customer's
balance, and running out of money stops nothing. We are adding a prepaid wallet: an
operator records a payment in the WhatsApp Adapter admin console, that becomes a USD
balance in AgentX, every completed run subtracts its cost, and a customer with an empty
balance cannot start a new call or chat. The customer sees their balance and a full history
when they log in.

There is no payment gateway. Customers pay the operator directly (UPI, bank transfer,
whatever) and the operator types the amount into the console. That is deliberate and not a
gap to fix.

## The two codebases

This feature spans two Git repositories that sit side by side on disk and run as containers
on the same EC2 host, on the same Docker network.

```
AgentX/                        <- parent folder, not a repo
├── AgentPlatform/             <- repo 1: "AgentX" / "Dograh". Most of the work.
│   ├── api/                   FastAPI backend (Python)
│   ├── ui/                    Next.js 15 customer frontend
│   ├── deploy/templates/      nginx config templates
│   ├── scripts/               format, migrate, openapi dump
│   └── .kiro/specs/prepaid-credit-wallet/   <- you are here
└── Whatsapp_Adapter/          <- repo 2: the operator console
    ├── app.py                 Entire backend in one file (~1690 lines)
    └── dashboard/             Next.js 15 admin frontend
```

### AgentPlatform (AgentX)

| Thing | Detail |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy **async**, Alembic migrations |
| Database | PostgreSQL |
| Queue | Redis + ARQ (background jobs) |
| Frontend | Next.js 15 App Router, React 19, TypeScript, Tailwind |
| API prefix | Every route is mounted under `/api/v1` |
| Routers | `api/routes/*.py`, assembled in `api/routes/main.py` |
| Deployment mode | Runs in **OSS mode** (`DEPLOYMENT_MODE=oss`). This matters enormously — see below. |

Read `AgentPlatform/AGENTS.md` and `AgentPlatform/api/AGENTS.md` before writing backend
code. They are short and they are binding. The most important rules:

- **Route handlers stay thin.** Parse the request, resolve auth and `organization_id`,
  delegate to `api/services/`, shape the response. Business logic does not live in routes.
- **Database access lives in `api/db/` clients.** Routes call services, services call DB
  clients. Do not put queries in a route or a service.
- **Organization scoping is a security requirement.** Almost everything is owned by an
  organization. Whenever you read or write an org-scoped row, filter by `organization_id`.
  Never trust an id from a request body to imply ownership.

### Whatsapp_Adapter

| Thing | Detail |
|---|---|
| Backend | Python, FastAPI, **single file** `app.py` |
| Database | MongoDB via `motor` (async driver) |
| Frontend | Next.js 15 App Router in `dashboard/` |
| Backend port | 8080 in the container, published on host `8001` |
| Dashboard port | 3001 |
| Style | No Pydantic models. Handlers do `data = await request.json()` and read raw dicts. |

The adapter's job today: it receives WhatsApp messages from Twilio, forwards them to an
AgentX agent, sends the reply back, and gives the operator a dashboard to watch
conversations, manage agents, and see captured leads.

**The adapter currently has no authentication of any kind.** No login, no password, no
token, no session. CORS is `allow_origins=["*"]`. And nginx publishes it on the public
internet. This is why adapter authentication is Phase 1 of this project rather than an
afterthought — we are about to add "give this account money" to it.

## Vocabulary

Learn these five words. The spec uses them precisely.

| Term | Meaning |
|---|---|
| **Organization** (`organization_id`) | The billing subject. In OSS mode every user gets exactly one organization automatically at signup. All of that user's workflows and agents belong to it. **The wallet belongs to the organization, not the user.** |
| **Workflow** | One AI agent design. A user can have many. All of them draw from the same organization wallet. |
| **Workflow run** | One execution of a workflow — one phone call or one chat session. This is what gets billed. |
| **Operator** | You / the business owner, using the adapter console. Not a customer. |
| **Customer** | The person who signs into AgentX, builds workflows, and pays the operator. |

One confusing overlap to be aware of: the adapter's MongoDB has a collection called
`agents`, but those are **not** AgentX agents. An adapter "agent" is one Twilio WhatsApp
business phone number plus the config for it. Each one stores an AgentX API key, which is
how we will find its organization.

## Why "OSS mode" matters

AgentX can run in two modes, switched by `DEPLOYMENT_MODE`:

- `saas` — the hosted Dograh product. Billing is handled by an external service called
  **MPS** (`https://services.dograh.com`). MPS owns the balance, the ledger, and Stripe.
- `oss` — self-hosted. **This is what we run.**

You will constantly find code that looks like billing already exists. Almost all of it is
`saas`-only, and it is a *proxy* to MPS, not local functionality. For example
`api/routes/organization_usage.py` has types called `MPSCreditLedgerEntryResponse` with
fields like `payment_order_id` and `balance_after`. Those are not database tables. They are
shapes describing someone else's API.

Three concrete traps:

1. `api/services/quota_service.py` is a real enforcement layer wired into 12 entry points,
   but its OSS path returns "allowed" without ever reading a balance. That is why running
   out of money stops nothing today.
2. `api/routes/organization_usage.py` short-circuits OSS to a legacy response, so the
   frontend's ledger table at `ui/src/app/billing/page.tsx` is dead code in our deployment.
   We are going to light it up.
3. `organization_usage_cycles.used_dograh_tokens` looks like a balance. It is a
   **calendar-month usage counter that counts upward and resets on the 1st of every
   month.** Never put a wallet balance in it.

## What already exists and works

Do not rebuild these.

| Capability | Where |
|---|---|
| Cost calculation for a run | `api/services/workflow_run_billing.py` — the formula is correct, including the 1.2 markup |
| Enforcement plumbing (12 call sites, HTTP 402) | `api/services/quota_service.py` and its callers |
| Email signup and login | `api/routes/auth.py` — bcrypt password + JWT, plus OTP |
| One organization per user | `api/routes/auth.py`, `api/db/organization_client.py` |
| Org API keys (`dgr_...`, SHA-256 hashed) | `api/db/api_key_client.py`, `api/utils/api_key.py` |
| A platform-level shared-secret auth pattern to copy | `api/routes/main.py`, `_verify_devops_secret` |
| Adapter → AgentX HTTP calls | `Whatsapp_Adapter/app.py`, `_send_to_dograh` |
| A customer ledger table in the frontend | `ui/src/app/billing/page.tsx`, currently unreachable in OSS |

## Local setup

### AgentPlatform

Contributor setup is documented in `AgentPlatform/docs/contribution/setup.mdx`. Follow it.

Commands you will need, from the `AgentPlatform` root:

```bash
# Run the API
uvicorn api.app:app --reload --port 8000

# Create a migration
./scripts/makemigrate.sh "add credit wallet tables"

# Apply migrations
./scripts/migrate.sh

# Run tests — note the .env.test, so you hit the test DB and not the dev DB
source venv/bin/activate && set -a && source api/.env.test && set +a \
  && python -m pytest api/tests/test_credit_account.py -v

# Formatting (CI checks this)
./scripts/format.sh

# Regenerate the OpenAPI spec (CI checks this is current)
python -m scripts.dump_docs_openapi
```

Two things that will bite you:

- `api/conftest.py` runs `alembic upgrade head` before tests. **You need a running
  PostgreSQL** for any test that imports the app or touches the DB. Pure-function tests
  (like the pricing formula) still need `REDIS_URL` set, because `api/constants.py` reads
  it with `os.environ["REDIS_URL"]` which raises if it is missing.
- Never run tests without sourcing `api/.env.test`. `api/.env` points at the dev database.

### Whatsapp_Adapter

```bash
./start.sh       # backend on 8001, dashboard on 3001
```

Needs a MongoDB. Copy `.env.example` to `.env` first. Warning: if MongoDB is unreachable
the adapter **silently** falls back to in-memory Python dictionaries and keeps serving.
That is fine for chat logs and catastrophic for money, which is the main reason the wallet
lives in AgentX's PostgreSQL and not here.

## Branching and delivery

- `main` and `develop` are both **protected**. You cannot push to them directly. Everything
  goes through a pull request.
- Feature work for this project branches off `Billing` (after Phase 0 merges `develop` into
  it).
- Deployment is automatic: merging to `main` triggers a GitHub Actions workflow on a
  self-hosted runner on the EC2 box, which rebuilds the containers. Migrations run
  automatically on API container start.
- Production host is `devagents.afterlife.org.in`.

A real incident worth learning from: two migrations were once given the same parent, which
created "multiple heads". The API container runs `alembic upgrade head` on startup, that
command failed, the container crash-looped, and **production went down**. Task 0.3 is a gate
specifically to prevent a repeat. Take it seriously.

## How to work through this spec

1. Read this file.
2. Read `requirements.md` — what we are building and how we will know it is done.
3. Read `design.md` — the technical decisions, data model, and API contracts. It contains
   the actual code shapes. `tasks.md` refers back to it instead of repeating it.
4. Work `tasks.md` **in order**. Phases have dependencies and two of them are safety gates.

Every task lists the files to read first. Read them. Do not write code against a file you
have not opened.

### Phase order and why

| Phase | What | Why here |
|---|---|---|
| 0 | Prerequisites | Branch hygiene and the migration-head gate |
| **1** | **Adapter authentication** | The adapter is publicly exposed with zero auth. Nothing else can safely ship first. |
| 2 | Wallet and ledger tables | Everything downstream needs a place to store money |
| 3 | Internal credits API | The door the adapter knocks on |
| 4 | Debit path | Runs start subtracting from the wallet |
| 5 | Enforcement | Empty balance actually blocks the next session |
| 6 | Operator console | The UI for recording payments |
| 7 | Customer-facing page | The customer can see their balance |
| 8 | Deployment | Config, nginx, seeding, then enable enforcement |

Phases 2–5 are backend only and can be reviewed independently. Phases 6–7 are UI. Phase 1
is the gate that unblocks everything public-facing.

## Two rules that are not negotiable

**Never interrupt a call or chat that is already running.** Balance is checked only when a
new call or chat session starts. A run that is under way finishes even if it drives the
balance negative. A customer mid-sentence must never be cut off over money.

**Money never touches `float`.** Use `decimal.Decimal` in Python and `Numeric` in
PostgreSQL, everywhere, end to end. Floating point silently loses fractions of a cent and
those errors accumulate across thousands of runs.

**There is no "credit" unit. Everything is real money in a real currency.** The customer pays ₹500
and their screen says ₹500. The tables are named `organization_credit_accounts` and
`credit_ledger_entries` because "credit" is the accounting verb — to credit an account — not a points
system. The balance is *stored* in USD because the cost formula is natively USD, and converted for
display into whatever currency that customer pays in. Conversion happens exactly twice: once when the
operator records a payment, once when a screen renders. **Never during a debit.** If you find
yourself writing a conversion factor between "credits" and money, you have misread the spec.

The existing code does have a credit unit — `_DOLLARS_TO_CREDITS = 100` in
`api/services/workflow_run_billing.py`, where 1 credit = 1 cent. That constant is deleted in task
4.1.
