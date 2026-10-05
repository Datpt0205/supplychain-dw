#!/usr/bin/env bash
# Run API + worker + web concurrently for local development.
# Prerequisite: `make infra-up` (or point env vars at your own services).
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# .env.example gains keys over time and `make bootstrap` only writes .env when
# it is absent, so a stale .env otherwise fails deep inside one child process
# whose stack trace scrolls past in the shared log.
for var in DW_API_DATABASE_URL DW_WORKER_DATABASE_URL; do
  [ -n "${!var:-}" ] || {
    echo "!! $var is missing — copy the new keys from .env.example into .env" >&2
    exit 1
  }
done

# `make dev` and `make docker-up` both want 3000/8000/8100, and so does a second
# `make dev`. Left alone the loser dies quietly in a shared log and the stack
# looks half-up, so name the conflict and stop.
busy=""
for spec in "${DW_API_PORT:-8000}:API" "${DW_WEB_PORT:-3000}:web"; do
  port="${spec%%:*}"
  if ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then
    busy="$busy  port $port (${spec##*:}) is already in use"$'\n'
  fi
done
if [ -n "$busy" ]; then
  echo "!! cannot start dev:" >&2
  printf '%s' "$busy" >&2
  echo "   Something already serves these - the Docker stack (\`make docker-down\`)," >&2
  echo "   another \`make dev\`, or another checkout. Stop it, or set DW_API_PORT," >&2
  echo "   DW_WEB_PORT and DW_CHAT_PORT to free ports." >&2
  exit 1
fi

pids=()
cleanup() {
  echo ""
  echo ">> shutting down dev processes"
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# `--reload-dir` is not tidiness. Left to itself uvicorn watches the whole
# working directory, which here means `node_modules`, `.venv` and `.git` as well
# as the source - and measured on 2026-08-19 it then fired not once in four and a
# half hours: the API kept serving the code it had imported at startup while the
# browser hot-reloaded around it, which surfaced as an HTTP 422 from a request
# shape the running process had never heard of. Naming the two source roots keeps
# the watch small enough to work.
RELOAD_DIRS=(--reload-dir packages/python --reload-dir apps/api --reload-dir configs)

echo ">> starting API on :${DW_API_PORT:-8000}"
uv run uvicorn dw_api.main:app --reload "${RELOAD_DIRS[@]}" --port "${DW_API_PORT:-8000}" &
pids+=($!)

echo ">> starting worker"
DW_WORKER_HEARTBEAT_FILE="${DW_WORKER_HEARTBEAT_FILE:-.dw/worker-heartbeat}" \
  uv run python -m dw_worker.main &
pids+=($!)

pids+=($!)

echo ">> starting web on :${DW_WEB_PORT:-3000}"
pnpm --filter @dw/web dev --port "${DW_WEB_PORT:-3000}" &
pids+=($!)

wait
