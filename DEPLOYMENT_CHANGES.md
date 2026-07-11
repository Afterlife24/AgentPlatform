# Deployment Changes Log

This file documents the Git structure, deployment setup, and every custom change made to the Dograh project for Autonomiq. If something breaks — locally or on EC2 — check here first.

---

## 1. Git Structure Overview

### Remotes

| Remote name | URL                                                | Purpose                                                       |
| ----------- | -------------------------------------------------- | ------------------------------------------------------------- |
| `origin`    | `https://github.com/Afterlife24/AgentPlatform.git` | **Our repo.** We push here. This is what gets deployed.       |
| `upstream`  | `https://github.com/dograh-hq/dograh.git`          | **Official Dograh repo.** We only pull from here, never push. |

Check anytime with:

```bash
git remote -v
```

### Branch strategy

| Branch           | Purpose                                                          | Deployed to                                    |
| ---------------- | ---------------------------------------------------------------- | ---------------------------------------------- |
| `main`           | Production-ready code only                                       | Production EC2 instance                        |
| `develop`        | Integration/testing branch                                       | Dev/staging EC2 instance                       |
| feature branches | Individual features or upstream syncs, e.g. `sync/upstream-main` | Local testing only, then merged into `develop` |

**Flow:** feature branch → tested locally → merged into `develop` → tested on the dev EC2 instance (load testing, live testing, etc.) → merged into `main` → deployed to production EC2.

### Pulling updates from official Dograh

Dograh ships weekly updates. To bring them in without breaking our setup:

```bash
git fetch upstream
git merge upstream/main        # do this on a feature branch first, not directly on main
# resolve anything if needed (should be rare — see section 2 below)
# test locally with docker compose up
# then merge that branch into develop, test on dev EC2, then into main
```

---

## 2. Why We Don't Get Merge Conflicts (the override pattern)

**The problem we solved:** Early on, we edited `docker-compose.yaml` directly to fix local Windows issues (port conflicts, cloudflared, etc.). This meant every time we pulled Dograh's updates, git would flag conflicts on those same lines — even in cases where Dograh had already fixed the same issue their own way. This would have gotten worse every single week.

**The fix — split the config into two files:**

| File                           | Who owns it                                                                                               | Committed to git?   |
| ------------------------------ | --------------------------------------------------------------------------------------------------------- | ------------------- |
| `docker-compose.yaml`          | **Dograh (upstream).** We never hand-edit this. Always kept identical to `upstream/main`.                 | Yes                 |
| `docker-compose.override.yaml` | **Us.** Contains only our local-only additions (currently: the `nginx-local` proxy service on port 3000). | Yes                 |
| `.env`                         | **Us, per-machine.** Secrets and endpoint URLs. Different values possible on your machine vs. EC2.        | **No — gitignored** |

**How the override works:** Docker Compose automatically looks for a file literally named `docker-compose.override.yaml` in the same folder and merges it on top of `docker-compose.yaml` — no `-f` flag, no extra command. Running `docker compose up` picks up both files as one combined config. Think of `docker-compose.yaml` as Dograh's recipe card, and `docker-compose.override.yaml` as our sticky note on top of it — we never write on their card, so it never conflicts with their revisions.

**What this achieves:**

- `docker-compose.yaml` stays byte-for-byte identical to upstream at all times → `git merge upstream/main` is clean on this file
- `docker-compose.override.yaml` doesn't exist in Dograh's repo at all → nothing to conflict with, ever
- The only realistic future conflict scenario: Dograh renames a service our override depends on (`ui`, `api`) — rare, and git will clearly flag it as "this service no longer exists," not a silent content conflict

**Rule going forward:** Never add a line directly to `docker-compose.yaml` again. Any new local-only need goes into `docker-compose.override.yaml` instead.

---

## 3. Current File State (Quick Reference)

| File                           | Status                       | Notes                                                                                                            |
| ------------------------------ | ---------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `docker-compose.yaml`          | Identical to `upstream/main` | Do not edit directly                                                                                             |
| `docker-compose.override.yaml` | Custom, ours                 | Contains `nginx-local` service (port 3000 → 80, proxies to `ui:3010` / `api:8000`)                               |
| `.env`                         | Custom, ours, **not in git** | Contains `OSS_JWT_SECRET`, `BACKEND_API_ENDPOINT=http://api:8000`, `MINIO_PUBLIC_ENDPOINT=http://localhost:9000` |
| `nginx-local.conf`             | Custom, ours                 | Used by `nginx-local` service in the override file                                                               |
| `LOCAL_SETUP.md`               | Custom, ours                 | Windows local setup instructions                                                                                 |

**Local dev command — unchanged throughout all of this:**

```bash
docker compose up
```

Always access the app at **http://localhost:3000** (not 3010 — see LOCAL_SETUP.md for why).

---

## 4. Detailed Change Log

### Change 1: Cloudflared made optional (2026-06-30)

**File changed:** `docker-compose.yaml` _(superseded by Change 2 below — upstream now handles this natively)_

**What was done:**

1. Added a profile restriction to the `cloudflared` service so it doesn't start by default
2. Removed `cloudflared` from the `api` service's `depends_on` list

**Why:**

- Cloudflared creates a random public tunnel URL on every startup
- On EC2 (production), nginx + SSL handles this — cloudflared isn't needed
- The API had a hard dependency on cloudflared; if Cloudflare was unreachable, the whole stack failed to start
- It also exposed an unknown public URL to the API (security risk)

**Note:** As of Change 2, upstream's own `docker-compose.yaml` already solved this the same way (`profiles: ["tunnel"]`, no `depends_on`), so our manual fix became unnecessary and was reverted when we reset the file to match upstream.

---

### Change 2: Moved local customizations to docker-compose.override.yaml (2026-07-04)

**Files changed:**

- `docker-compose.yaml` — reset to be byte-for-byte identical to `upstream/main`
- `docker-compose.override.yaml` — new file, contains the `nginx-local` service
- `.env` — added `BACKEND_API_ENDPOINT` and `MINIO_PUBLIC_ENDPOINT` explicitly (upstream removed their old localhost defaults)

**Why:** See section 2 above — this change _is_ the override pattern being introduced.

**Verified before this change:** upstream's own fixes for cloudflared and the UI healthcheck (`http://127.0.0.1:3010`) already matched what we'd patched manually, so resetting to their file didn't reintroduce old bugs.

---

### Change 3: Fixed "Backend connection failed" banner (2026-07-04)

**File changed:** `.env` (local only, gitignored, never committed)

**Symptom:** UI showed "Backend connection failed — Backend health check timed out after 3000ms while trying to reach http://api:8000" even though the app worked fine otherwise.

**Root cause:** `BACKEND_API_ENDPOINT=http://localhost:8000` matches a special case in `api/utils/common.py` (`get_backend_endpoints()`) that treats "localhost" as a signal to look for a running Cloudflare tunnel first (`api/utils/tunnel.py`). Since we don't run cloudflared, that lookup takes ~3-5s to time out before falling back. Every call to `/api/v1/health` — including the UI's server-side version-check with its 3-second timeout — got caught by this delay. The app itself worked fine because its own health checks use much longer timeouts (30-60s).

**Fix (attempted, then reverted — see Change 4):** Changed `BACKEND_API_ENDPOINT` in `.env` from `http://localhost:8000` to `http://api:8000` (the Docker-internal hostname). This fixed the health-check delay but broke actual app functionality — see Change 4 below. **Do not repeat this fix.**

**Verification command (for the health-check timing itself):**

```bash
docker exec dograh-api-1 python -c "
import urllib.request, time
start = time.time()
urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=15).read()
print('Took:', time.time() - start, 'seconds')
"
```

**Correct takeaway:** The 3-second delay on the version-check banner is a **cosmetic, acceptable tradeoff**. `BACKEND_API_ENDPOINT` must stay `http://localhost:8000` — see Change 4 for why.

---

## 5. Security Notes for EC2 Deployment

- **PostgreSQL (5432)** and **Redis (6379)** are exposed on `0.0.0.0` in `docker-compose.yaml`. Block these ports in the EC2 security group — only allow access from within the instance itself.
- **MinIO (9000, 9001)** is already correctly bound to `127.0.0.1` (not publicly accessible).
- Always run `scripts/setup_remote.sh` on the EC2 instance before starting — it replaces all localhost references with the server's public IP and handles TLS/TURN config.
- Never commit `.env` — it's gitignored by design, and contains secrets that differ per environment (local / dev EC2 / prod EC2).

---

### Change 4: Reverted Change 3 — BACKEND_API_ENDPOINT must stay "localhost" (2026-07-04)

**File changed:** `.env` (local only, gitignored, never committed)

**Symptom after Change 3:** Creating/opening agents failed completely. Browser console showed:

```
GET http://api:8000/api/v1/workflow/fetch/1 net::ERR_NAME_NOT_RESOLVED
GET http://api:8000/api/v1/user/onboarding-state net::ERR_NAME_NOT_RESOLVED
POST http://api:8000/api/v1/workflow/create/template net::ERR_NAME_NOT_RESOLVED
```

**Root cause:** `BACKEND_API_ENDPOINT` is not purely a server-side/internal value. The API includes it in its `/health` response (`backend_api_endpoint` field), and the **browser** reads that value to decide where to send API calls directly — see `ui/src/lib/apiClient.ts` (`resolveBrowserBackendUrl`) and `ui/src/context/AppConfigContext.tsx`. `http://api:8000` is a Docker-internal hostname; it means nothing to a browser running on the host machine, so every direct client-side API call failed with `ERR_NAME_NOT_RESOLVED`. `http://localhost:8000` works because docker-compose publishes port 8000 to the host (`ports: ["8000:8000"]` in `docker-compose.yaml`), so the browser can actually reach it.

**Fix:** Reverted `BACKEND_API_ENDPOINT` back to `http://localhost:8000`.

**Net result:** We're back to accepting the cosmetic ~3s delay on the version-check banner (Change 3's original symptom) as an acceptable tradeoff, since the alternative breaks the app entirely. If we want the banner delay gone AND working browser calls, the real fix is to actually run cloudflared (`docker compose --profile tunnel up`) so the tunnel lookup in `api/utils/tunnel.py` succeeds instead of timing out — not worth the complexity for local dev.

**Rule going forward:** `BACKEND_API_ENDPOINT` in `.env` must always be a URL the **browser** can resolve (i.e. `http://localhost:8000`), never a Docker-internal hostname like `http://api:8000`. This applies to `MINIO_PUBLIC_ENDPOINT` too, for the same reason — it's used to fetch audio recordings directly from the browser.

---

## 6. EC2 Deployment (Mumbai, ap-south-1)

Two long-lived EC2 instances, one per branch, replacing the earlier Frankfurt (eu-central-1) attempt which was torn down entirely (instance, security group, key pair all deleted — nothing to migrate from there).

| Environment | Branch    | Instance type       | Public URL                        | Server IP        |
| ----------- | --------- | ------------------- | --------------------------------- | ---------------- |
| Develop     | `develop` | t3.large, 15 GB gp3 | `https://devagents.autonomiq.ae`  | `3.108.185.4`    |
| Main (prod) | `main`    | t3.large, 30 GB gp3 | `https://mainagents.autonomiq.ae` | `13.203.227.111` |

Both provisioned in `ap-south-1`, security group: SSH restricted to a single office/home IP (`/32`), HTTP (80) and HTTPS (443) open to `0.0.0.0/0`. TURN/STUN ports (3478, 5349, 49152–49200) are **not** opened at the security-group level yet — only add them once voice-call testing actually needs them, per the "don't open ports until required" rule we've followed throughout.

**Important — deployment source of truth:** `scripts/setup_remote.sh` (prebuilt mode) downloads its own copy of `docker-compose.yaml` and support files from `raw.githubusercontent.com/dograh-hq/dograh` into a nested subfolder, completely disconnected from the actual git clone it's run from. On both instances this was corrected — the stack now runs directly out of `/home/ubuntu/dograh` (the real `git clone` of `Afterlife24/AgentPlatform`), with `.env` and `certs/` copied in and the nested folder retired. **Any manual work on these servers must happen in `/home/ubuntu/dograh`, not a nested `dograh/dograh` folder** — if you ever see one, something regressed.

**t3.large is required, not optional.** t3.medium (4 GB RAM) was tried first for the develop box and caused the instance to hang/become SSH-unreachable under the combined load of 7 running containers plus a `next build` (webpack + 1,085 npm packages) — likely OOM. Instance had to be stopped, resized to t3.large (8 GB RAM) via `aws ec2 modify-instance-attribute`, and restarted. **Resizing changes the public IP** (unless an Elastic IP is attached) — after a resize, `.env`'s `SERVER_IP`/`PUBLIC_HOST`/`PUBLIC_BASE_URL` and the TLS cert must be regenerated for the new IP/domain, and the API container recreated to pick up the new env.

---

## 7. Fix: `/embed` widget script gated behind login (2026-07-05)

**File changed:** `ui/src/middleware.ts`

**Symptom:** Embedding the voice widget on an external site (`<script src=".../embed/dograh-widget.js?...">`) silently failed — no icon, no console error dialog, just nothing. Direct `curl` to the script URL returned `307 Temporary Redirect` to `/auth/login`.

**Root cause:** The OSS auth middleware redirects any unauthenticated request to `/auth/login`, using a `PUBLIC_PATHS` allowlist (`/auth/login`, `/auth/signup`) to skip that check for the login pages themselves. `/embed/dograh-widget.js` wasn't in that allowlist — so anonymous visitors on a third-party site (who obviously have no Dograh login cookie) got redirected to a login page instead of the JS file. This defeats the entire purpose of an embeddable widget.

**Fix:**

```ts
const PUBLIC_PATHS = ["/auth/login", "/auth/signup", "/embed"];
```

**Verification:**

```bash
curl -s -D - -o /dev/null http://localhost:3010/embed/dograh-widget.js
# must return 200, not 307
```

**This fix is upstream-independent** — `ui/src/middleware.ts` is our fork's own file, not something Dograh's weekly `upstream/main` sync will touch, so this survives future `git merge upstream/main` runs untouched.

---

## 8. Fix: prebuilt `ui` image doesn't include our patches (2026-07-05)

**File changed:** `docker-compose.override.yaml`

**Symptom:** Change 7's fix worked immediately after a manual `docker build`, but reappeared (307 again) after later merging `develop` into `main` and redeploying with `setup_remote.sh` (prebuilt mode).

**Root cause:** `setup_remote.sh` in prebuilt mode pulls `dograhai/dograh-ui:latest` — Dograh's own official image on Docker Hub, built from **their** repo, not our fork. Merging a fix into our `main` branch on GitHub has zero effect on that Docker Hub image; only Dograh publishing a new image would change it, and our patch obviously isn't in their codebase. Any future deploy that does a plain `--pull always` (the prebuilt default) silently reverts to the unpatched image.

**Fix:** Added a `ui` build override so `ui` is always built from our fork's source instead of pulled:

```yaml
services:
  ui:
    build:
      context: .
      dockerfile: ui/Dockerfile
    image: dograh-local/dograh-ui:local
    pull_policy: never
```

`api` is untouched and still pulls the prebuilt tag — our patch only touches `ui/`.

**Rule going forward:** every deploy (manual or CI) must run

```bash
docker compose --profile remote up -d --build
```

using `--build`, **never** `--pull always`. Because this directive lives in the committed `docker-compose.override.yaml`, any environment that runs `git pull` + the command above automatically stays patched — no manual step required per deploy. This is also why Section 6's "run from the real git clone, not the nested upstream-downloaded folder" fix matters: the override only takes effect if Compose is actually invoked from a directory that has this file.

---

## 9. Custom domains via Route 53 (2026-07-05)

**Zone:** `autonomiq.ae`, hosted in a **different AWS account** than the EC2 instances (AWS CLI profile `default`, not `autonomiq`). The existing zone already serves the main marketing site (CloudFront) and mail (MX/A records) — untouched.

**Records added** (simple `A` records, no alias, TTL 300):

| Record                    | Value            |
| ------------------------- | ---------------- |
| `devagents.autonomiq.ae`  | `3.108.185.4`    |
| `mainagents.autonomiq.ae` | `13.203.227.111` |

**Per-server steps after adding the DNS record** (this is what Dograh's own `scripts/setup_custom_domain.sh` automates, done manually here since the instances were already running):

1. Update `.env`: `PUBLIC_HOST=<domain>`, `PUBLIC_BASE_URL=https://<domain>` (leave `SERVER_IP` as the raw IP — coturn needs it).
2. Regenerate the bootstrap self-signed cert for the new CN, recreate `api`/`nginx` so the API picks up the new `PUBLIC_HOST`.
3. `sudo certbot certonly --webroot -w certs -d <domain> ...` for a real Let's Encrypt cert, then copy `fullchain.pem`/`privkey.pem` into `certs/local.crt`/`certs/local.key` and restart `nginx_https`.

The original sslip.io URLs (`15-207-85-16.sslip.io`, `13-203-227-111.sslip.io`, etc.) stop working once `PUBLIC_HOST` is repointed — always use the current domain from the table in Section 6.

**`certs/` is gitignored** (added in this change) — each server holds its own cert for its own domain; never commit these.

---

## 10. CI/CD: auto-deploy on push to develop/main (2026-07-05)

**File added:** `.github/workflows/deploy-ec2.yml`

**Trigger:** `push` to `develop` or `main` only — i.e. fires when a PR is merged into the branch, not on every commit inside an open PR. Matches the intended flow: feature branch → PR (reviewed/approved) → merged into `develop` → manual testing on devagents → merged into `main` → auto-deployed to prod.

**What it does, per branch:**

```bash
cd /home/ubuntu/dograh
git fetch origin
git checkout <branch>
git reset --hard origin/<branch>
sudo docker compose --profile remote up -d --build
```

`git reset --hard` guarantees the server exactly matches GitHub, no drift from any manual SSH edits. Safe because `.env` and `certs/` are gitignored/untracked — `reset --hard` never touches untracked files.

**Runs synchronously** — the job blocks until the `--build` step finishes (several minutes for a `ui` rebuild), so pass/fail is visible in the Actions tab before the workflow completes, not fire-and-forget.

### Why SSH-based deploy (`appleboy/ssh-action`) was tried first and abandoned

The first version of this workflow SSHed from GitHub's own hosted runners into each EC2 box. It failed with `dial tcp <ip>:22: i/o timeout` — **not** a bad key, an actual network timeout. GitHub-hosted runners run on Microsoft/GitHub infrastructure with IPs outside our security group's "SSH from my IP only" rule, so the connection never reached the box.

**Considered and rejected:** opening port 22 to GitHub's published Actions IP ranges. `https://api.github.com/meta` currently lists **7,292** individual CIDR ranges for Actions — far beyond a security group's rule limit, and the only realistic way to "allow GitHub" is `0.0.0.0/0`, which removes the SSH IP restriction entirely. Rejected to keep the existing SSH exposure unchanged.

### Solution: self-hosted runners

A GitHub Actions runner is installed as a **systemd service** directly on each EC2 instance (`/home/ubuntu/actions-runner`). It polls GitHub over an outbound connection for jobs — no inbound access from GitHub is ever needed, so the security group is completely untouched.

| Instance | Runner name             | Label     |
| -------- | ----------------------- | --------- |
| develop  | `dograh-develop-runner` | `develop` |
| main     | `dograh-main-runner`    | `main`    |

The workflow targets the matching runner by label:

```yaml
runs-on: [self-hosted, "${{ github.ref_name }}"]
```

**Setup steps (for reference / replacing an instance later):**

```bash
# Generate a fresh single-use registration token (repo admin, via gh CLI or API):
gh api -X POST repos/Afterlife24/AgentPlatform/actions/runners/registration-token --jq .token

# On the target EC2 instance:
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/download/v2.328.0/actions-runner-linux-x64-2.328.0.tar.gz
tar xzf actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/Afterlife24/AgentPlatform --token <TOKEN> \
  --name <dograh-develop-runner|dograh-main-runner> --labels <develop|main> --work _work --unattended --replace
sudo ./svc.sh install ubuntu
sudo ./svc.sh start
```

**Repository secrets** (`Settings → Secrets and variables → Actions`) from the earlier SSH-based attempt (`DEV_SSH_KEY`, `MAIN_SSH_KEY`, `DEV_HOST`, `MAIN_HOST`) are no longer used by the workflow but were left in place rather than deleted — harmless, and saves re-adding them if a future change needs direct SSH again.

**Verifying a runner is online:**

```bash
gh api repos/Afterlife24/AgentPlatform/actions/runners
# status should be "online" for both dograh-develop-runner and dograh-main-runner
```

---

## 11. Build API from source + ARQ worker service (2026-07-09)

**Files changed:**

- `docker-compose.override.yaml` — added `api` and `arq-worker` build overrides
- `docker-compose.yaml` — added `arq-worker` service definition
- `ui/Dockerfile` — reduced Node heap from 4096 MB to 2048 MB

### Problem 1: UI build OOM in Docker

**Symptom:** `docker compose up --build` failed during the Next.js production build:

```
process "npm run build" did not complete successfully: cannot allocate memory
```

**Root cause:** `NODE_OPTIONS="--max-old-space-size=4096"` in `ui/Dockerfile` requested 4 GB for the webpack build. Combined with other containers building concurrently, Docker Desktop didn't have enough memory.

**Fix:** Reduced to 2048 MB:

```dockerfile
ENV NODE_OPTIONS="--max-old-space-size=2048"
```

---

### Problem 2: Public text-chat routes missing (404)

**Symptom:** The WhatsApp adapter calling `POST /api/v1/public/agent/text-chat/test/workflow/{trigger}/message` always got `{"detail":"Not Found"}`. Checking the OpenAPI spec confirmed the routes weren't registered.

**Root cause:** `docker-compose.yaml` pulls `dograhai/dograh-api:latest` — the official upstream prebuilt image. Our fork adds `api/routes/public_text_chat.py` (the public text-chat API for WhatsApp/SMS), but that code isn't in upstream's image. Same pattern as Change 8 (UI patches lost when pulling prebuilt image).

**Fix:** Added API build override in `docker-compose.override.yaml`:

```yaml
services:
  api:
    build:
      context: .
      dockerfile: api/Dockerfile
    image: dograh-local/dograh-api:local
    pull_policy: never

  arq-worker:
    build:
      context: .
      dockerfile: api/Dockerfile
    image: dograh-local/dograh-api:local
    pull_policy: never
```

---

### Problem 3: ARQ worker not in Docker Compose

**Symptom:** Background jobs (knowledge base processing, webhooks) never ran. Had to start the worker manually:

```bash
source .venv/bin/activate && python -m arq api.tasks.arq.WorkerSettings
```

**Root cause:** `docker-compose.yaml` only defines the API HTTP server (`uvicorn`), not the ARQ background worker process.

**Fix:** Added `arq-worker` service to `docker-compose.yaml` that reuses the API image with a different command:

```yaml
arq-worker:
  image: ${REGISTRY:-dograhai}/dograh-api:latest
  command: ["python", "-m", "arq", "api.tasks.arq.WorkerSettings"]
  # ... same env vars as the api service ...
  depends_on:
    postgres: { condition: service_healthy }
    redis: { condition: service_healthy }
    minio: { condition: service_healthy }
```

**Note:** This is one of the rare exceptions to the "never edit docker-compose.yaml" rule (Section 2). The ARQ worker is infrastructure we need regardless of upstream — it's not a local-only customization. If upstream adds their own worker service later, `git merge upstream/main` will flag a conflict on this block, which we'll resolve by taking upstream's version (likely identical or better). The override file then ensures it builds from source via the `arq-worker` block above.

---

### Commands (local dev)

```bash
cd AgentPlatform
docker compose down
docker compose up --build
```

- `--build` is **required** — forces Docker to rebuild the `api` and `ui` images from our fork's source code, picking up any local patches not in the upstream prebuilt images.
- Without `--build`, Docker reuses cached images which may be stale/prebuilt.
- Access the app at **http://localhost:3000** (via nginx-local proxy).
- API available at **http://localhost:8000**.
- Verify public text-chat route exists:
  ```bash
  curl http://localhost:8000/api/v1/openapi.json 2>/dev/null | \
    python3 -c "import sys,json; paths=json.load(sys.stdin)['paths']; [print(p) for p in paths if 'text-chat' in p]"
  # Should show /api/v1/public/agent/text-chat/... routes
  ```

### Commands (EC2 deploy)

```bash
cd /home/ubuntu/dograh
git fetch origin
git checkout <branch>
git reset --hard origin/<branch>
sudo docker compose --profile remote up -d --build
```

Same `--build` flag ensures EC2 also builds from source. The CI workflow (`.github/workflows/deploy-ec2.yml`) already uses `--build`.

---

### Updated Section 3 addendum

| File                           | Status                          | Notes                                                          |
| ------------------------------ | ------------------------------- | -------------------------------------------------------------- |
| `docker-compose.yaml`          | **Modified** (arq-worker added) | Exception to the no-edit rule — see note above                 |
| `docker-compose.override.yaml` | Custom, ours                    | Now also overrides `api` and `arq-worker` to build from source |
| `ui/Dockerfile`                | Modified                        | `--max-old-space-size` reduced from 4096 to 2048               |
