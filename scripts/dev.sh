#!/usr/bin/env bash
# Starts api (+socket.io), [arq worker], gateway and web. Ctrl-C stops everything.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# .env fills in defaults; variables already set in the environment win (same rule as pydantic-settings)
if [ -f .env ]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    [ -z "${!k+x}" ] && export "$k=$v"
  done < .env
fi
export OA_INTERNAL_TOKEN="${OA_INTERNAL_TOKEN:-dev-internal-token}"
export OA_PUBLIC_WEB_URL="${OA_PUBLIC_WEB_URL:-http://localhost:3000}"
API_PORT="${OA_API_PORT:-8100}" GW_PORT="${OA_GATEWAY_PORT:-8080}" WEB_PORT="${OA_WEB_PORT:-3000}"
export OA_API_URL="${OA_API_URL:-http://127.0.0.1:$API_PORT}"
export OA_PUBLIC_API_URL="${OA_PUBLIC_API_URL:-$OA_API_URL}"
export OA_APP_DOMAIN="${OA_APP_DOMAIN:-app.localhost:$GW_PORT}"
export NEXT_PUBLIC_OA_SOCKET_URL="${NEXT_PUBLIC_OA_SOCKET_URL:-$OA_API_URL}"
mkdir -p data/logs
pids=()
cleanup() {
  trap - EXIT INT TERM
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  # sandboxes started by this api (local provider) are children of uvicorn's process tree
  pkill -f "$ROOT/services/sandboxd/bin/sandboxd .*--root ${OA_DATA_DIR:-$ROOT/data}" 2>/dev/null || true
  # unclaimed warm-pool sandboxes (empty slot workspace); claimed ones hold a project and are reclaimed on next start
  if [ "${OA_SANDBOX_PROVIDER:-local}" = "docker" ] && command -v docker >/dev/null; then
    for c in $(docker ps -aq --filter label=openatoms.pool=1 2>/dev/null); do
      slot=$(docker inspect -f '{{index .Config.Labels "openatoms.slot"}}' "$c" 2>/dev/null)
      ws="${OA_DATA_DIR:-$ROOT/data}/pool/$slot/ws"
      if [ -n "$slot" ] && [ -d "$ws" ] && [ -z "$(ls -A "$ws")" ]; then docker rm -f "$c" >/dev/null 2>&1 || true; fi
    done
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd services/server && exec .venv/bin/uvicorn oa.main:app --host 127.0.0.1 --port "$API_PORT") > >(sed -u 's/^/[api] /') 2>&1 &
pids+=($!)
if [ "${OA_QUEUE:-inproc}" = "arq" ]; then
  (cd services/server && exec .venv/bin/arq oa.worker.WorkerSettings) > >(sed -u 's/^/[worker] /') 2>&1 &
  pids+=($!)
fi
GATEWAY_LISTEN=":$GW_PORT" apps/gateway/bin/gateway > >(sed -u 's/^/[gateway] /') 2>&1 &
pids+=($!)
if [ "${OA_SKIP_WEB:-0}" != "1" ]; then
  (cd apps/web && if [ -d .next ] && [ "${OA_WEB_PROD:-0}" = "1" ]; then exec pnpm start -p "$WEB_PORT"; else exec pnpm dev -p "$WEB_PORT"; fi) > >(sed -u 's/^/[web] /') 2>&1 &
  pids+=($!)
fi
echo "OpenAtoms: web http://localhost:$WEB_PORT  api $OA_API_URL  previews http://*.$OA_APP_DOMAIN"
wait
