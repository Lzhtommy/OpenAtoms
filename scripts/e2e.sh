#!/usr/bin/env bash
# Boots the lite stack in a throwaway data dir (no web), runs scripts/e2e.py, tears down.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE="${1:-lite}"
# the e2e checks script against the deterministic mock model, whatever .env selects
export OA_DEFAULT_MODEL="${OA_E2E_MODEL:-oa-mock}" OA_BASIC_MODEL="${OA_E2E_MODEL:-oa-mock}"
export OA_DATA_DIR="${OA_DATA_DIR:-$(mktemp -d)/data}" OA_SKIP_WEB=1 OA_MOCK_DELAY="${OA_MOCK_DELAY:-0.002}"
mkdir -p "$OA_DATA_DIR"
./scripts/dev.sh > "$OA_DATA_DIR/stack.log" 2>&1 &
STACK=$!
trap 'kill $STACK 2>/dev/null; wait $STACK 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -sf --noproxy "*" "http://127.0.0.1:${OA_API_PORT:-8100}/api/v1/health" >/dev/null && break; sleep 1; done
uv run scripts/e2e.py --mode "$MODE" "${@:2}" --api "http://127.0.0.1:${OA_API_PORT:-8100}" --gateway "http://127.0.0.1:${OA_GATEWAY_PORT:-8080}" || { echo "--- stack log tail ---"; tail -80 "$OA_DATA_DIR/stack.log"; exit 1; }
