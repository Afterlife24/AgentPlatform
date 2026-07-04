# Deployment Changes Log

This file documents all custom changes made to the Dograh project for Autonomiq's deployment. If something breaks in production, check here first.

---

## Change 1: Cloudflared made optional (2026-06-30)

**File changed:** `docker-compose.yaml`

**What was done:**
1. Added `profiles: ["local-tunnel"]` to the `cloudflared` service
2. Removed `cloudflared` from the `api` service's `depends_on` list

**Why:**
- Cloudflared creates a random public Cloudflare tunnel URL on every startup
- On EC2 (production), we use nginx with SSL — cloudflared is not needed
- The API had a hard dependency on cloudflared, meaning if Cloudflare was unreachable, the entire stack would fail to start
- It also exposed an unknown public URL to the API (security risk)

**Impact:**
- `docker compose up` no longer starts cloudflared
- API now starts as soon as Postgres, Redis, and MinIO are healthy (faster boot)
- No random public tunnel URL is created

**How to enable cloudflared (if needed for local webhook testing):**
```bash
docker compose --profile local-tunnel up
```

**How to revert (if something breaks):**
In `docker-compose.yaml`:
1. Remove `profiles: ["local-tunnel"]` from the cloudflared service
2. Add back to the api service's depends_on:
```yaml
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
      cloudflared:
        condition: service_started
```

---

## Security Notes for EC2 Deployment

- **PostgreSQL (5432)** and **Redis (6379)** are exposed on 0.0.0.0 in docker-compose. Block these ports in the EC2 security group — only allow access from within the instance itself.
- **MinIO (9000, 9001)** is already correctly bound to 127.0.0.1 (not publicly accessible).
- Always run `scripts/setup_remote.sh` on the EC2 instance before starting — it replaces all localhost references with the server's public IP.


---

## Change 2: Moved local customizations to docker-compose.override.yaml (2026-07-04)

**Files changed:**
- `docker-compose.yaml` — reset to be byte-for-byte identical to `upstream/main` (official Dograh repo)
- `docker-compose.override.yaml` — new file, contains the `nginx-local` service (port 3000 proxy)
- `.env` — added `BACKEND_API_ENDPOINT` and `MINIO_PUBLIC_ENDPOINT` explicitly (upstream removed their localhost defaults)

**Why:**
Previously, local Windows-only fixes (cloudflared profile, UI healthcheck, nginx-local proxy) were edited directly into `docker-compose.yaml`. This meant every time we pulled updates from the official Dograh repo (`upstream/main`), git would flag conflicts on lines we'd customized — even when upstream had already fixed the same issue a different way. This was going to compound every week as Dograh releases updates.

**The fix:**
Docker Compose automatically merges `docker-compose.yaml` + `docker-compose.override.yaml` at runtime — no `-f` flag needed, `docker compose up` just works. So:
- `docker-compose.yaml` stays a clean, unmodified mirror of upstream → future `git fetch upstream && git merge upstream/main` should be conflict-free on this file
- `docker-compose.override.yaml` only exists in our repo, upstream will never touch it → this file also never conflicts
- Our only local addition (nginx-local on port 3000, needed because Windows Docker Desktop had port conflicts) now lives safely in the override file

**Note:** upstream's own fixes for cloudflared (now `profiles: ["tunnel"]`, no `depends_on` from api) and the UI healthcheck (`http://127.0.0.1:3010`) already matched what we'd patched manually — so resetting to their file didn't reintroduce those bugs.

**Local dev workflow — unchanged:**
```bash
docker compose up      # still works exactly the same, still use http://localhost:3000
```

**Upstream sync workflow (going forward):**
```bash
git fetch upstream
git merge upstream/main     # should be clean; conflicts now only happen if upstream renames
                             # a service the override file depends on (ui, api)
git push origin main        # or push to a feature/develop branch first for testing
```

**Remotes:**
- `origin` → `https://github.com/Afterlife24/AgentPlatform.git` (our repo — push here)
- `upstream` → `https://github.com/dograh-hq/dograh.git` (official Dograh repo — pull updates from here)
