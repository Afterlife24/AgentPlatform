# Dograh Local Setup Guide

<!-- CI/CD pipeline test commit: verifying auto-deploy to devagents.autonomiq.ae -->


## Prerequisites

- **Docker Desktop** installed and running (Engine running, green icon in system tray)
- **Windows 10/11** with WSL 2 enabled (`wsl --install` in admin PowerShell, restart once)

## Steps to Run

### 1. Open PowerShell and navigate to the dograh folder

```powershell
cd C:\Users\Ashrith\Desktop\websiteRevamp\dograh
```

### 2. Make sure the `.env` file exists

It should already be there with:
```
OSS_JWT_SECRET=dograh-local-secret-2024-xyz
```

If missing, create it:
```powershell
[System.IO.File]::WriteAllText("$PWD\.env", "OSS_JWT_SECRET=dograh-local-secret-2024-xyz`n", [System.Text.UTF8Encoding]::new($false))
```

### 3. Start the stack
First start the docker desktop by opening the app
```powershell
docker compose up
```

First run pulls images (2-5 min). Wait until you see:
```
api-1  | Uvicorn running on http://0.0.0.0:8000
ui-1   | Application ready at http://localhost:3010
```

### 4. Open the dashboard

Go to **http://localhost:3000** in your browser (NOT 3010).

Port 3000 is the nginx proxy that correctly routes both HTTP and WebSocket connections.

### 5. Create account and build agents

- Sign up with any email/password (stays local)
- Click "Voice Agents" → "Create Agent" → "Use Agent Builder"
- Design your workflow, then click "Test Agent" to do a web call

## Common Commands

| Action | Command |
|--------|---------|
| Start | `docker compose up` |
| Start in background | `docker compose up -d` |
| Stop | Ctrl+C (or `docker compose down`) |
| View logs (background) | `docker compose logs -f` |
| Reset all data | `docker compose down -v` |

## Ports

| Port | Service |
|------|---------|
| 3000 | **Use this** — nginx proxy (routes everything correctly) |
| 3010 | UI direct (WebSocket won't work here) |
| 8000 | API direct |
| 9001 | MinIO console (admin/minioadmin) |

## Notes

- Always use **http://localhost:3000** for testing voice agents
- The built-in Dograh LLM/STT/TTS provider works out of the box (calls services.dograh.com)
- For fully offline/free usage, configure the "Speaches" provider with a local Ollama instance
