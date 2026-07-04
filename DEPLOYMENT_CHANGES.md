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
