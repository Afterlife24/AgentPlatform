# CORS & URL Configuration Guide

## How Dograh Resolves URLs

Dograh uses different URLs for different purposes:

| Variable                  | Purpose                                                                | Who uses it              |
| ------------------------- | ---------------------------------------------------------------------- | ------------------------ |
| `BACKEND_API_ENDPOINT`    | Webhook/callback URL given to external services (Twilio, etc.)         | Twilio, external callers |
| `NEXT_PUBLIC_BACKEND_URL` | Browser-side API base URL (what the frontend JS calls)                 | User's browser           |
| `BACKEND_URL`             | Server-side (SSR) API URL (container-to-container)                     | Next.js server           |
| `PUBLIC_BASE_URL`         | Master public origin — other vars derive from it if not set explicitly | All subsystems           |

### URL Resolution Chain (Browser)

```
NEXT_PUBLIC_BACKEND_URL  →  backendApiEndpoint from /health  →  window.location.origin
        (build-time)              (runtime, from API)               (fallback)
```

If `NEXT_PUBLIC_BACKEND_URL` is not set, the UI reads `backend_api_endpoint` from the `/api/v1/health` response (which equals `BACKEND_API_ENDPOINT`). If that's an external-only URL (like ngrok), the browser tries cross-origin requests → CORS failure.

---

## Local Development Setup

### The Problem

- Backend needs a public URL for Twilio webhooks (ngrok/Cloudflare tunnel)
- Browser needs to talk to the API on the same origin (localhost) to avoid CORS
- These are two different URLs for two different audiences

### The Solution

```
                    ngrok (port 3000)
                         │
                    ┌────▼────┐
                    │  nginx  │ :3000 (host) → :80 (container)
                    └────┬────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    /whatsapp        /api/v1/           /*
         │               │               │
  ┌──────▼──────┐  ┌────▼────┐    ┌────▼────┐
  │  whatsapp   │  │  dograh │    │   UI    │
  │  adapter    │  │   API   │    │ Next.js │
  │   :8080     │  │  :8000  │    │  :3010  │
  └─────────────┘  └─────────┘    └─────────┘
```

**`.env` (root — `AgentPlatform/.env`):**

```env
# External webhook URL (ngrok for dev) — used by Twilio only
BACKEND_API_ENDPOINT=https://your-subdomain.ngrok-free.dev
```

**`docker-compose.override.yaml`:**

```yaml
services:
  api:
    # Mount local startup script with --proxy-headers for Twilio signature validation
    volumes:
      - ./scripts/start_services_docker_local.sh:/app/scripts/start_services_docker_local.sh:ro
    command: ["./scripts/start_services_docker_local.sh"]

  ui:
    build:
      args:
        - NEXT_PUBLIC_BACKEND_URL=http://localhost:3000
    environment:
      NEXT_PUBLIC_BACKEND_URL: "http://localhost:3000"

  whatsapp-adapter:
    build:
      context: ../whatsapp_adapter
      dockerfile: Dockerfile
    environment:
      DOGRAH_API_BASE: "http://api:8000/api/v1"
    depends_on:
      api:
        condition: service_healthy
    networks:
      - app-network
```

### Twilio Signature Validation (--proxy-headers)

When behind nginx, FastAPI's `request.url` shows the internal Docker URL (e.g., `http://api:8000/...`) instead of the public ngrok URL that Twilio signed against. This causes signature validation failures.

**Fix:** The local startup script (`scripts/start_services_docker_local.sh`) runs uvicorn with:

```bash
uvicorn api.app:app --host 0.0.0.0 --port 8000 --workers 1 \
    --proxy-headers --forwarded-allow-ips '*'
```

And nginx forwards the original headers:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
proxy_set_header X-Forwarded-Host $host;
```

This ensures `request.url` reconstructs to the public URL → Twilio signature validates correctly.

### nginx Configuration (Local Dev)

```nginx
upstream api_backend {
    server api:8000;
}

upstream ui_frontend {
    server ui:3010;
}

upstream whatsapp_adapter {
    server whatsapp-adapter:8080;
}

server {
    listen 80;

    # WebSocket signaling
    location /api/v1/ws/ {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    # Telephony WebSocket (Twilio media streams)
    location /api/v1/telephony/ws/ {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    # WhatsApp adapter
    location /whatsapp {
        proxy_pass http://whatsapp_adapter;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WhatsApp dashboard endpoints
    location /conversations { proxy_pass http://whatsapp_adapter; }
    location /messages/     { proxy_pass http://whatsapp_adapter; }
    location /takeover      { proxy_pass http://whatsapp_adapter; }
    location /release       { proxy_pass http://whatsapp_adapter; }
    location /send-message  { proxy_pass http://whatsapp_adapter; }

    # API requests
    location /api/v1/ {
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_set_header X-Forwarded-Host $host;
    }

    # UI (catch-all)
    location / {
        proxy_pass http://ui_frontend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### Twilio Webhook URLs (Dev)

| Agent    | Twilio Console Field           | URL                                                                     |
| -------- | ------------------------------ | ----------------------------------------------------------------------- |
| Voice    | Voice Configuration → URL      | `https://your-ngrok.ngrok-free.dev/api/v1/telephony/inbound/run` (POST) |
| WhatsApp | Messaging → A message comes in | `https://your-ngrok.ngrok-free.dev/whatsapp` (POST)                     |

Both go through the same ngrok tunnel → nginx → respective services.

### Running Locally

```bash
# Terminal 1: ngrok (point to nginx port)
ngrok http 3000

# Terminal 2: Docker stack (everything)
cd AgentPlatform
docker compose up --build
```

After ngrok starts, copy the HTTPS URL into `AgentPlatform/.env` as `BACKEND_API_ENDPOINT` and restart (`docker compose up --build`).

---

## Production Deployment

In production, everything is behind a single domain with HTTPS. No CORS issues because browser, API, and webhooks all share the same origin.

### Single-Domain Setup (Recommended)

```
┌─────────────┐         ┌──────────────┐        ┌─────────────┐
│   Browser   │────────▶│    nginx     │───────▶│  API :8000  │
│             │         │  :443 (TLS)  │───────▶│  UI  :3010  │
└─────────────┘         └──────────────┘───────▶│  WA  :8080  │
                               ▲                └─────────────┘
┌─────────────┐                │
│   Twilio    │────────────────┘
│(Voice + WA) │
└─────────────┘
```

**`.env` (root):**

```env
# Single public origin — everything derives from this
PUBLIC_BASE_URL=https://voice.yourdomain.com

# These are auto-derived from PUBLIC_BASE_URL if not set:
# BACKEND_API_ENDPOINT=https://voice.yourdomain.com
# MINIO_PUBLIC_ENDPOINT=https://voice.yourdomain.com
```

No `NEXT_PUBLIC_BACKEND_URL` needed — when `BACKEND_API_ENDPOINT` matches the browser's origin, there's no CORS conflict.

No `--proxy-headers` workaround needed — nginx sets proper `X-Forwarded-Proto: https` and the URL matches what Twilio signs.

### nginx Production Config

```nginx
server {
    listen 443 ssl http2;
    server_name voice.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/voice.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/voice.yourdomain.com/privkey.pem;

    # WebSocket endpoints (signaling + telephony streams)
    location /api/v1/ws/ {
        proxy_pass http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }

    location /api/v1/telephony/ws/ {
        proxy_pass http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }

    # WhatsApp adapter
    location /whatsapp {
        proxy_pass http://whatsapp-adapter:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /conversations { proxy_pass http://whatsapp-adapter:8080; }
    location /messages/     { proxy_pass http://whatsapp-adapter:8080; }
    location /takeover      { proxy_pass http://whatsapp-adapter:8080; }
    location /release       { proxy_pass http://whatsapp-adapter:8080; }
    location /send-message  { proxy_pass http://whatsapp-adapter:8080; }

    # API
    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # MinIO/S3 audio files
    location /voice-audio/ {
        proxy_pass http://minio:9000/voice-audio/;
        proxy_set_header Host $host;
    }

    # UI (catch-all)
    location / {
        proxy_pass http://ui:3010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

# HTTP → HTTPS redirect
server {
    listen 80;
    server_name voice.yourdomain.com;
    return 301 https://$host$request_uri;
}
```

### Production `.env` Checklist

```env
# ─── Required ───
PUBLIC_BASE_URL=https://voice.yourdomain.com
OSS_JWT_SECRET=<random-64-char-string>

# ─── Database (use strong passwords) ───
POSTGRES_PASSWORD=<strong-random-password>
DATABASE_URL=postgresql+asyncpg://postgres:<password>@postgres:5432/postgres

# ─── Redis ───
REDIS_PASSWORD=<strong-random-password>
REDIS_URL=redis://:<password>@redis:6379

# ─── MinIO / S3 ───
MINIO_ACCESS_KEY=<random>
MINIO_SECRET_KEY=<random>
MINIO_BUCKET=voice-audio

# ─── WhatsApp Adapter ───
WHATSAPP_DOGRAH_API_KEY=<your-dograh-api-key>
WHATSAPP_DOGRAH_TRIGGER_PATH=<workflow-trigger-uuid>
TWILIO_ACCOUNT_SID=<your-twilio-sid>
TWILIO_AUTH_TOKEN=<your-twilio-auth-token>
TWILIO_WHATSAPP_NUMBER=+1XXXXXXXXXX
WHATSAPP_MONGODB_URL=<your-mongodb-connection-string>
WHATSAPP_MONGODB_DATABASE=WhatsappChat24hrs

# ─── Twilio Voice (configured per-org in the UI, not in .env) ───

# ─── Optional overrides (only if split deployment) ───
# BACKEND_API_ENDPOINT=https://api.yourdomain.com
# MINIO_PUBLIC_ENDPOINT=https://storage.yourdomain.com
# UI_APP_URL=https://app.yourdomain.com
```

### Twilio Webhook URLs (Production)

| Agent    | Twilio Console Field           | URL                                                         |
| -------- | ------------------------------ | ----------------------------------------------------------- |
| Voice    | Voice Configuration → URL      | `https://voice.yourdomain.com/api/v1/telephony/inbound/run` |
| WhatsApp | Messaging → A message comes in | `https://voice.yourdomain.com/whatsapp`                     |

---

## Troubleshooting CORS Issues

### Symptom: Browser console shows CORS errors

**Diagnosis steps:**

1. Open DevTools → Network tab
2. Look at the failing request's URL
3. Compare it to the page's origin (`window.location.origin`)

| Request URL                     | Page Origin                   | Problem                                                |
| ------------------------------- | ----------------------------- | ------------------------------------------------------ |
| `https://ngrok.../api/...`      | `http://localhost:3000`       | UI is using the external webhook URL for browser calls |
| `http://localhost:8000/api/...` | `http://localhost:3000`       | Different port = different origin                      |
| `https://api.example.com/...`   | `https://app.example.com/...` | Split domain without CORS headers                      |

### Fix by Scenario

**Scenario 1: Dev with ngrok (most common)**

```yaml
# docker-compose.override.yaml
ui:
  build:
    args:
      - NEXT_PUBLIC_BACKEND_URL=http://localhost:3000
  environment:
    NEXT_PUBLIC_BACKEND_URL: "http://localhost:3000"
```

**Scenario 2: Split domains in production**

If API and UI are on different subdomains, add CORS headers in nginx:

```nginx
location /api/ {
    add_header Access-Control-Allow-Origin "https://app.yourdomain.com" always;
    add_header Access-Control-Allow-Methods "GET, POST, PUT, PATCH, DELETE, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
    add_header Access-Control-Allow-Credentials "true" always;

    if ($request_method = OPTIONS) {
        return 204;
    }

    proxy_pass http://api:8000;
}
```

**Scenario 3: Same domain, still getting CORS**

Check that the browser isn't caching an old redirect or service worker. Hard refresh (Cmd+Shift+R) or clear site data.

### Symptom: Twilio webhook signature validation failed

**Cause:** nginx passes internal Docker URL to FastAPI → `request.url` doesn't match what Twilio signed.

**Fix (dev):** Use `start_services_docker_local.sh` which adds `--proxy-headers --forwarded-allow-ips '*'` to uvicorn, and ensure nginx forwards `X-Forwarded-Proto` and `Host` headers.

**Fix (prod):** Not needed — nginx sets `X-Forwarded-Proto $scheme` which equals `https`, and the Host matches the public domain. Standard uvicorn handles this correctly when deployed with the remote profile.

### Symptom: WebSocket 404 on /api/v1/telephony/ws/

**Cause:** nginx doesn't have a WebSocket-enabled location for `/api/v1/telephony/ws/`.

**Fix:** Add a dedicated location block with `proxy_http_version 1.1`, `Upgrade`, and `Connection "upgrade"` headers. The generic `/api/v1/` block doesn't upgrade connections.

---

## Quick Reference: Which `.env` File Does What

| File                           | Read by                          | When                                                 |
| ------------------------------ | -------------------------------- | ---------------------------------------------------- |
| `AgentPlatform/.env`           | Docker Compose                   | Variable substitution in `docker-compose.yaml`       |
| `AgentPlatform/api/.env`       | uvicorn (direct, non-Docker dev) | `python-dotenv` loads it at startup                  |
| `AgentPlatform/ui/.env.local`  | Next.js (direct, non-Docker dev) | Next.js auto-loads `.env.local`                      |
| `docker-compose.override.yaml` | Docker Compose                   | Merged on top of `docker-compose.yaml` automatically |
| `whatsapp_adapter/.env`        | uvicorn (direct, non-Docker dev) | Only when running adapter outside Docker             |

**Rule of thumb:** If running via Docker Compose, the root `.env` and `docker-compose.override.yaml` are your config surfaces. The `api/.env`, `ui/.env.local`, and `whatsapp_adapter/.env` only matter when running services directly (no Docker).

---

## Migration Checklist: Dev → Production

- [ ] Get a domain and point DNS to your server's IP
- [ ] Set up TLS (Let's Encrypt / Certbot or Cloudflare)
- [ ] Set `PUBLIC_BASE_URL=https://yourdomain.com` in root `.env`
- [ ] Remove `NEXT_PUBLIC_BACKEND_URL` override (not needed when same-origin)
- [ ] Remove ngrok `BACKEND_API_ENDPOINT` (derived from `PUBLIC_BASE_URL`)
- [ ] Remove `command:` override and volume mount for `start_services_docker_local.sh`
- [ ] Update Twilio Voice webhook: `https://yourdomain.com/api/v1/telephony/inbound/run`
- [ ] Update Twilio WhatsApp webhook: `https://yourdomain.com/whatsapp`
- [ ] Configure nginx with TLS + WebSocket locations for both `/api/v1/ws/` and `/api/v1/telephony/ws/`
- [ ] Use `docker compose --profile remote up -d --build`
- [ ] Verify: browser DevTools shows no CORS errors
- [ ] Verify: Twilio test call connects and voice pipeline starts
- [ ] Verify: WhatsApp message gets a reply
