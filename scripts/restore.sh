#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh. STOP the stack (api/worker/gateway) first.
#   scripts/restore.sh <BACKUP_DIR> [--yes]
# Replaces the database contents, re-uploads object storage, and replaces data/chats.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a
SRC="${1:?usage: restore.sh <backup dir> [--yes]}"
SRC="$(cd "$SRC" && pwd)"
PY="$ROOT/services/server/.venv/bin/python"
MODE="${OA_MODE:-lite}"
DATA="${OA_DATA_DIR:-$ROOT/data}"
if [ "${2:-}" != "--yes" ]; then
  read -r -p "Restore $SRC into $MODE stack at $DATA (overwrites data)? [y/N] " a
  [ "$a" = "y" ] || exit 1
fi

pg_url() { local u="${OA_DATABASE_URL:-postgresql+asyncpg://oa:oa@127.0.0.1:5432/oa}"; echo "${u/+asyncpg/}"; }

echo "→ database"
if [ "$MODE" = "full" ]; then
  if command -v pg_restore >/dev/null 2>&1; then
    pg_restore --clean --if-exists --no-owner -d "$(pg_url)" "$SRC/db.dump"
  else
    net=(--add-host=host.docker.internal:host-gateway)
    [ -n "${OA_PG_DOCKER_NETWORK:-}" ] && net=(--network "$OA_PG_DOCKER_NETWORK")
    url="$(pg_url)"; url="${url//localhost/host.docker.internal}"; url="${url//127.0.0.1/host.docker.internal}"
    docker run --rm "${net[@]}" -v "$SRC:/backup:ro" postgres:16-alpine \
      pg_restore --clean --if-exists --no-owner -d "$url" /backup/db.dump
  fi
else
  OA_DATA_DIR="$DATA" "$PY" "$ROOT/scripts/backup_storage.py" sqlite-restore "$SRC/db.sqlite"
fi

echo "→ object storage"
OA_DATA_DIR="$DATA" "$PY" "$ROOT/scripts/backup_storage.py" load "$SRC/storage.tar.gz" >/dev/null

echo "→ workspaces"
mkdir -p "$DATA"
if [ -d "$DATA/chats" ]; then mv "$DATA/chats" "$DATA/chats.pre-restore-$(date +%s)"; fi
tar -C "$DATA" -xzf "$SRC/workspaces.tar.gz"
rm -rf "$DATA/pool"  # warm-pool slots are disposable; sandboxes restart cold and reinstall node_modules

echo "→ verify"
OA_DATA_DIR="$DATA" "$PY" "$ROOT/scripts/backup_storage.py" summary
echo "✓ restored from $SRC (previous workspaces kept as chats.pre-restore-*)"
