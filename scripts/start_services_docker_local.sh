#!/usr/bin/env bash
# Local override: same as start_services_docker.sh but adds --proxy-headers
# to uvicorn so Twilio signature validation works behind nginx/ngrok.
set -e

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
ENV_FILE="$BASE_DIR/api/.env"

ARQ_WORKERS=${ARQ_WORKERS:-1}
FASTAPI_WORKERS=${FASTAPI_WORKERS:-1}
UVICORN_BASE_PORT=${UVICORN_BASE_PORT:-8000}

cd "$BASE_DIR"
echo "Starting Dograh Services (DOCKER-LOCAL) at $(date) in BASE_DIR: ${BASE_DIR}"

if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

alembic -c "$BASE_DIR/api/alembic.ini" upgrade head

pids=()

shutdown() {
  echo "Received shutdown signal, stopping services..."
  for pid in "${pids[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  wait
  exit 0
}

trap shutdown TERM INT

start() {
  local name=$1
  shift
  echo "→ Starting $name"
  "$@" &
  pids+=($!)
  echo "  $name PID $!"
}

start ari_manager           python -m api.services.telephony.ari_manager
start campaign_orchestrator python -m api.services.campaign.campaign_orchestrator

for ((i=0; i<FASTAPI_WORKERS; i++)); do
  port=$((UVICORN_BASE_PORT + i))
  start "uvicorn$i" uvicorn api.app:app --host 0.0.0.0 --port "$port" --workers 1 --proxy-headers --forwarded-allow-ips '*'
done

for ((i=1; i<=ARQ_WORKERS; i++)); do
  start "arq$i" python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG
done

wait -n
echo "A service exited; tearing down container."
shutdown
