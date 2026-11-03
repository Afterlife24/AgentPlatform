# Wallet Balance Alert Emails: Deployment Notes

Branch: `feat/Mail-Automation` (AgentPlatform only; `Whatsapp_Adapter` and `agentX_consultancy` are unchanged)

## What this feature does

The account owner of an organization now gets an email when their prepaid wallet runs low:

| Email | Sent when | Subject |
|---|---|---|
| Low balance warning | Balance drops to **$2.00 or below** | `Low wallet balance warning` |
| Balance exhausted | Balance drops to **$0.00 or below** | `Wallet balance exhausted` |

Behavior:

- **Recipient:** the organization's owner email (the same "primary user" the operator console shows).
- **Triggers:** checked after every balance change: call/chat billing (ARQ worker), knowledge-base ingestion debits (ARQ worker), operator adjustments, and operator top-ups (API).
- **Once per drop:** each email is sent once, not on every debit. A top-up that lifts the balance back above the level re-arms it, so the next drop sends it again.
- **Big drops:** if one debit takes the balance from above $2 straight to $0 or below, only the "exhausted" email is sent.
- **Safe for billing:** an email failure never blocks or rolls back a charge. A failed send is retried on the next balance change.
- **Multi-worker safe:** the "already sent" check uses a database row lock, so concurrent workers can't send duplicates.
- **SMTP off:** if `SMTP_PASSWORD` is empty, no alerts are sent, and they stay armed until SMTP is configured.

## Changes in this branch

**New files**
- `api/services/wallet_balance_alerts.py`: alert logic and email templates.
- `api/alembic/versions/wallet001_add_balance_alert_columns.py`: database migration.

**Modified files**
- `api/db/models.py`: two new columns on `organization_credit_accounts`.
- `api/db/credit_account_client.py`: `claim_balance_alerts()` and `release_balance_alert()`.
- `api/services/credit_account_service.py`: alert check after grant and adjust.
- `api/services/workflow_run_billing.py`: alert check after each call debit.
- `api/tasks/knowledge_base_processing.py`: alert check after each KB ingestion debit.
- `api/services/auth/email.py`: generic `send_email()` helper (OTP emails reuse it), plus support for `SMTP_DISABLE_TLS`.
- `api/constants.py`: `LOW_BALANCE_ALERT_USD` and `SMTP_DISABLE_TLS`.
- `api/.env.example`: documents `LOW_BALANCE_ALERT_USD`.
- `docker-compose.override.yaml`:
  - passes `SMTP_DISABLE_TLS`, `LOW_BALANCE_ALERT_USD` and `UI_APP_URL` into `api` and `arq-worker`;
  - adds an optional `mailpit` service (local mail catcher) under the `local-mail` profile. It does **not** start with `--profile remote`.

## Database migration

Revision `wallet001` (parent: `merge001`) adds two nullable columns to `organization_credit_accounts`:

| Column | Type | Meaning |
|---|---|---|
| `low_balance_alert_sent_at` | `timestamptz NULL` | When the low-balance email was sent. NULL = armed |
| `exhausted_alert_sent_at` | `timestamptz NULL` | When the exhausted email was sent. NULL = armed |

- **Automatic:** it runs on API container start (`scripts/start_services_docker.sh` runs `alembic upgrade head`). No manual step is needed.
- **Safe:** additive only, with no table rewrite and no data changes. Existing rows start as NULL (armed).
- **Rollback:** `alembic -c api/alembic.ini downgrade merge001`. Roll back the code first, or the old code would keep working anyway since it never reads these columns.
- **Note:** an account that is already at or below $2 at deploy time gets its alert email on its next balance change.

## Required configuration on the server

Set these in the server's `.env` (`/home/ubuntu/dograh/.env`, read by docker compose):

| Variable | Required | Value |
|---|---|---|
| `SMTP_HOST` | Yes | `smtp.gmail.com` |
| `SMTP_PORT` | Yes | `587` |
| `SMTP_FROM_EMAIL` | Yes | Sender Gmail address (e.g. `afterlife668@gmail.com`) |
| `SMTP_PASSWORD` | Yes | 16-character Gmail **App Password** for that address (not the normal password) |
| `SMTP_DISABLE_TLS` | No | Leave unset or `false` in production. Only `true` for local Mailpit |
| `UI_APP_URL` | Recommended | Public UI address, e.g. `https://<your-domain>`. Used for the "View your balance and top up" link (`<UI_APP_URL>/billing`). Falls back to `PUBLIC_BASE_URL` if unset |
| `LOW_BALANCE_ALERT_USD` | No | Low-balance level in USD. Default `2.00` |

To create a Gmail App Password:
1. Turn on 2-Step Verification on the sender account.
2. Go to https://myaccount.google.com/apppasswords.
3. Create a password and paste it into `SMTP_PASSWORD`.

These SMTP settings are also used for signup/reset OTP emails, so production may already have them.

**Network:** the EC2 security group / network must allow **outbound TCP 587** to `smtp.gmail.com`.

**Sending limits:** a regular Gmail account sends roughly 500 emails per day. This is fine at current scale; move to a transactional provider (SES, SendGrid, etc.) if volume grows.

## Deployment steps

1. Merge `feat/Mail-Automation` into `develop`, then `main` (the normal PR flow). Pushing to `main` triggers `.github/workflows/deploy-ec2.yml`.
2. Before or with the deploy, add/verify the `.env` variables above on the server.
3. The deploy runs `sudo docker compose --profile remote up -d --build`. The `api` container applies migration `wallet001` on startup, and `arq-worker` is rebuilt with the new code.
   - If `.env` is changed **after** the deploy, recreate both services so they pick it up:
     ```bash
     cd /home/ubuntu/dograh
     sudo docker compose --profile remote up -d --no-deps api arq-worker
     ```
4. Confirm the migration:
   ```bash
   sudo docker compose exec api alembic -c api/alembic.ini current
   # expected: wallet001 (head)
   ```

## Real-time (live) testing checklist

Use a **test organization**, not a real customer, and an inbox you can check.

### Prerequisites
- [ ] Deployed and migration at `wallet001`.
- [ ] SMTP variables set; `SMTP_DISABLE_TLS` is not `true`.
- [ ] The test org's owner email is a real inbox you can access.
- [ ] `DOGRAH_INTERNAL_SECRET` is available, for the internal credits API used below.

### Test 1: Low balance email (operator adjustment)
1. Look up the test org and its current balance:
   ```bash
   curl -s -X POST http://localhost:8000/api/v1/internal/credits/resolve-account \
     -H "X-Dograh-Internal-Secret: $DOGRAH_INTERNAL_SECRET" -H "Content-Type: application/json" \
     -d '{"email":"<test-owner-email>"}'
   ```
2. Bring the balance to about **$5.00** with an adjustment (amount = 5.00 minus the current balance). Use a new `idempotency_key` per call:
   ```bash
   curl -s -X POST http://localhost:8000/api/v1/internal/credits/adjust \
     -H "X-Dograh-Internal-Secret: $DOGRAH_INTERNAL_SECRET" -H "Content-Type: application/json" \
     -d '{"organization_id":<ORG_ID>,"idempotency_key":"alert-test-1","amount_usd":"<DELTA>","note":"Alert test: set to 5.00"}'
   ```
   The same steps can be done from the operator billing console (Whatsapp_Adapter dashboard).
3. Adjust by `-3.10` (balance becomes $1.90).
4. **Expected:** the `Low wallet balance warning` email arrives within a minute.
5. Adjust by `-0.50` (balance $1.40). **Expected:** no new email.

### Test 2: Balance exhausted email
1. Adjust the balance down to exactly `0.00`.
2. **Expected:** the `Wallet balance exhausted` email arrives.
3. Debit a little more (e.g. `-0.10`). **Expected:** no new email.

### Test 3: Re-arm after top-up
1. Top up / adjust the balance back above $2 (e.g. to $5).
2. Drop it below $2 again. **Expected:** the low-balance email is sent again.

### Test 4: Real call billing path (end to end)
1. Set the test org's balance just above $2 (e.g. `$2.20`).
2. Make a real call or chat with one of the org's agents.
3. When the call ends, the ARQ worker bills it. If the charge pushes the balance to $2 or below, the low-balance email arrives shortly after.
4. Keep making calls (or adjust down) to reach $0. The exhausted email arrives.
   - If `ENFORCE_CREDIT_BALANCE=true`, new calls are refused at $0. That is expected and separate from the email.

### After testing
- Restore the test org's balance with an adjustment.
- The test adjustments stay visible in that org's ledger.

### Watching logs
```bash
sudo docker compose logs -f api arq-worker | grep -i "wallet"
```
- Success: `Sent wallet 'low' alert to <email> for org <id> (balance $1.900000)`
- SMTP failure: `Failed to send wallet 'low' alert for org <id>: <error>`
- SMTP not configured: no alert log lines (debug-level skip only)

### Troubleshooting
| Symptom | Likely cause |
|---|---|
| `Failed to send ... Username and Password not accepted` | `SMTP_PASSWORD` is not a Gmail App Password, or 2-Step Verification is off |
| `Failed to send ... timed out` / connection errors | Outbound port 587 blocked |
| `Failed to send ... STARTTLS` | `SMTP_DISABLE_TLS=true` set in production; remove it |
| No email, no log lines | `SMTP_PASSWORD` empty, or `api`/`arq-worker` not recreated after editing `.env` |
| `not sent: no owner email` | The org has no user with an email address |
| Email arrives but link points to localhost | `UI_APP_URL` (or `PUBLIC_BASE_URL`) not set |
| Email in spam | Common for Gmail-sent automated mail; mark as "Not spam" |

## Local testing (for developers)

The local stack can capture emails in Mailpit instead of delivering them:

```powershell
# AgentPlatform/.env
# SMTP_HOST=mailpit  SMTP_PORT=1025  SMTP_PASSWORD=<any non-empty>  SMTP_DISABLE_TLS=true
docker compose --profile local-mail up -d mailpit
docker compose -f docker-compose.yaml -f docker-compose.override.yaml -f docker-compose.local.yaml up -d --no-deps --no-build api arq-worker
```

Read captured emails at http://localhost:8025. Verified locally: the low email at $1.90 and the exhausted email at $0.00 were both delivered to Mailpit, there were no duplicates on further debits or idempotent retries, a top-up re-armed the alerts, and a failed send was retried on the next balance change.
