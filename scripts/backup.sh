#!/usr/bin/env bash
# Backup OpenAtoms state: database, object storage, project workspaces (PLAN P8 "备份与恢复").
#   scripts/backup.sh [OUT_DIR]          (env: same OA_* settings as the running stack, e.g. from .env)
# Produces OUT_DIR/oa-backup-<ts>/{db.dump|db.sqlite, storage.tar.gz, workspaces.tar.gz, manifest.json}.
# Postgres dumps use pg_dump if installed, otherwise the postgres:16-alpine image (OA_PG_DOCKER_NETWORK
# selects the docker network; default: host-gateway mapping of localhost).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a
PY="$ROOT/services/server/.venv/bin/python"
MODE="${OA_MODE:-lite}"
DATA="${OA_DATA_DIR:-$ROOT/data}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-$ROOT/backups}/oa-backup-$TS"
mkdir -p "$OUT"

pg_url() {
  local u="${OA_DATABASE_URL:-postgresql+asyncpg://oa:oa@127.0.0.1:5432/oa}"
  echo "${u/+asyncpg/}"
}

pg_run() { # pg_run <tool> args...  (local binary or dockerized client)
  local tool="$1"; shift
  if command -v "$tool" >/dev/null 2>&1; then "$tool" "$@"; return; fi
  local net=(--add-host=host.docker.internal:host-gateway)
  [ -n "${OA_PG_DOCKER_NETWORK:-}" ] && net=(--network "$OA_PG_DOCKER_NETWORK")
  local args=()
  for a in "$@"; do a="${a//localhost/host.docker.internal}"; args+=("${a//127.0.0.1/host.docker.internal}"); done
  docker run --rm -i "${net[@]}" -v "$OUT:/backup" postgres:16-alpine "$tool" "${args[@]}"
}

echo "→ database ($MODE)"
if [ "$MODE" = "full" ]; then
  if command -v pg_dump >/dev/null 2>&1; then pg_dump -Fc -f "$OUT/db.dump" "$(pg_url)"
  else pg_run pg_dump -Fc -f /backup/db.dump "$(pg_url)"; fi
else
  OA_DATA_DIR="$DATA" "$PY" "$ROOT/scripts/backup_storage.py" sqlite-backup "$OUT/db.sqlite"
fi

echo "→ object storage (${OA_STORAGE:-local})"
OBJECTS=$(OA_DATA_DIR="$DATA" "$PY" "$ROOT/scripts/backup_storage.py" dump "$OUT/storage.tar.gz")

echo "→ workspaces"
# -h follows chats/<id>/workspace symlinks of sandboxes attached to warm-pool slots; node_modules is rebuilt on wake
if [ -d "$DATA/chats" ]; then
  tar -C "$DATA" -czhf "$OUT/workspaces.tar.gz" --exclude='node_modules' --exclude='.pnpm-store' --exclude='dist' \
      --exclude='sandboxd.log' chats
else
  tar -czf "$OUT/workspaces.tar.gz" -T /dev/null
fi

SUMMARY=$(OA_DATA_DIR="$DATA" "$PY" "$ROOT/scripts/backup_storage.py" summary)
cat > "$OUT/manifest.json" <<JSON
{"created_at": "$TS", "mode": "$MODE", "storage": "${OA_STORAGE:-local}", "objects": $OBJECTS, "summary": $SUMMARY}
JSON
echo "✓ backup written to $OUT"
echo "$OUT"
