# Deployment Changes Log

This file documents the Git structure, deployment setup, and every custom change made to the Dograh project for Autonomiq. If something breaks — locally or on EC2 — check here first.

---

## 1. Git Structure Overview

### Remotes

| Remote name | URL | Purpose |
|---|---|---|
| `origin` | `https://github.com/Afterlife24/AgentPlatform.git` | **Our repo.** We push here. This is what gets deployed. |
| `upstream` | `https://github.com/dograh-hq/dograh.git` | **Official Dograh repo.** We only pull from here, never push. |

Check anytime with:
```bash
git remote -v
```

### Branch strategy

| Branch | Purpose | Deployed to |
|---|---|---|
| `main` | Production-ready code only | Production EC2 instance |
| `develop` | Integration/testing branch | Dev/staging EC2 instance |
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

| File | Who owns it | Committed to git? |
|---|---|---|
| `docker-compose.yaml` | **Dograh (upstream).** We never hand-edit this. Always kept identical to `upstream/main`. | Yes |
| `docker-compose.override.yaml` | **Us.** Contains only our local-only additions (currently: the `nginx-local` proxy service on port 3000). | Yes |
| `.env` | **Us, per-machine.** Secrets and endpoint URLs. Different values possible on your machine vs. EC2. | **No — gitignored** |

**How the override works:** Docker Compose automatically looks for a file literally named `docker-compose.override.yaml` in the same folder and merges it on top of `docker-compose.yaml` — no `-f` flag, no extra command. Running `docker compose up` picks up both files as one combined config. Think of `docker-compose.yaml` as Dograh's recipe card, and `docker-compose.override.yaml` as our sticky note on top of it — we never write on their card, so it never conflicts with their revisions.

**What this achieves:**
- `docker-compose.yaml` stays byte-for-byte identical to upstream at all times → `git merge upstream/main` is clean on this file
- `docker-compose.override.yaml` doesn't exist in Dograh's repo at all → nothing to conflict with, ever
- The only realistic future conflict scenario: Dograh renames a service our override depends on (`ui`, `api`) — rare, and git will clearly flag it as "this service no longer exists," not a silent content conflict

**Rule going forward:** Never add a line directly to `docker-compose.yaml` again. Any new local-only need goes into `docker-compose.override.yaml` instead.

---

## 3. Current File State (Quick Reference)

| File | Status | Notes |
|---|---|---|
| `docker-compose.yaml` | Identical to `upstream/main` | Do not edit directly |
| `docker-compose.override.yaml` | Custom, ours | Contains `nginx-local` service (port 3000 → 80, proxies to `ui:3010` / `api:8000`) |
| `.env` | Custom, ours, **not in git** | Contains `OSS_JWT_SECRET`, `BACKEND_API_ENDPOINT=http://api:8000`, `MINIO_PUBLIC_ENDPOINT=http://localhost:9000` |
| `nginx-local.conf` | Custom, ours | Used by `nginx-local` service in the override file |
| `LOCAL_SETUP.md` | Custom, ours | Windows local setup instructions |

**Local dev command — unchanged throughout all of this:**
```bash
docker compose up
```
Always access the app at **http://localhost:3000** (not 3010 — see LOCAL_SETUP.md for why).

---

## 4. Detailed Change Log

### Change 1: Cloudflared made optional (2026-06-30)

**File changed:** `docker-compose.yaml` *(superseded by Change 2 below — upstream now handles this natively)*

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

**Why:** See section 2 above — this change *is* the override pattern being introduced.

**Verified before this change:** upstream's own fixes for cloudflared and the UI healthcheck (`http://127.0.0.1:3010`) already matched what we'd patched manually, so resetting to their file didn't reintroduce old bugs.

---

### Change 3: Fixed "Backend connection failed" banner (2026-07-04)

**File changed:** `.env` (local only, gitignored, never committed)

**Symptom:** UI showed "Backend connection failed — Backend health check timed out after 3000ms while trying to reach http://api:8000" even though the app worked fine otherwise.

**Root cause:** `BACKEND_API_ENDPOINT=http://localhost:8000` matches a special case in `api/utils/common.py` (`get_backend_endpoints()`) that treats "localhost" as a signal to look for a running Cloudflare tunnel first (`api/utils/tunnel.py`). Since we don't run cloudflared, that lookup takes ~3-5s to time out before falling back. Every call to `/api/v1/health` — including the UI's server-side version-check with its 3-second timeout — got caught by this delay. The app itself worked fine because its own health checks use much longer timeouts (30-60s).

**Fix:** Changed `BACKEND_API_ENDPOINT` in `.env` from `http://localhost:8000` to `http://api:8000` (the Docker-internal hostname). Still resolves correctly for all container-to-container calls, but skips the tunnel-check path entirely since it doesn't contain "localhost" or "127.0.0.1". Health check response time dropped from ~3.1s to ~0.03s.

**Verification command:**
```bash
docker exec dograh-api-1 python -c "
import urllib.request, time
start = time.time()
urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=15).read()
print('Took:', time.time() - start, 'seconds')
"
```

**If this breaks again:** check `.env` for `BACKEND_API_ENDPOINT` — it must never contain "localhost" or "127.0.0.1" unless cloudflared is actually running (`docker compose --profile tunnel up`).

---

## 5. Security Notes for EC2 Deployment

- **PostgreSQL (5432)** and **Redis (6379)** are exposed on `0.0.0.0` in `docker-compose.yaml`. Block these ports in the EC2 security group — only allow access from within the instance itself.
- **MinIO (9000, 9001)** is already correctly bound to `127.0.0.1` (not publicly accessible).
- Always run `scripts/setup_remote.sh` on the EC2 instance before starting — it replaces all localhost references with the server's public IP and handles TLS/TURN config.
- Never commit `.env` — it's gitignored by design, and contains secrets that differ per environment (local / dev EC2 / prod EC2).
