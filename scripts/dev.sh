#!/usr/bin/env bash
# Runs the whole platform in development mode:
#   Postgres (docker, host :5433) + Studio API :8000 + Studio UI :3000
#   + Artemis API :8001 + Artemis dashboard :5173
# Ctrl-C stops every process this script started (Postgres keeps running; `make down` stops it).
# Logs are prefixed per service and also written to .dev-logs/<service>.log.
#
# Env knobs: STUDIO_RELOAD=1 (uvicorn --reload for the Studio API; off by default because a
# reload drops live WebRTC calls), SKIP_POSTGRES=1 (use your own Postgres on :5433), LOG_LEVEL.
# Written for bash 3.2 (macOS /bin/bash): no `wait -n`, no associative arrays.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.dev-logs"
mkdir -p "$LOG_DIR"

STUDIO_API_PORT=8000
STUDIO_UI_PORT=3000
ARTEMIS_API_PORT=8001
DASHBOARD_PORT=5173

PIDS=""
NAMES=""

say() { printf '\033[1m[dev]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[dev] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

kill_tree() {
  local pid=$1 child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill -TERM "$pid" 2>/dev/null || true
}

CLEANED=0
cleanup() {
  [ "$CLEANED" = 1 ] && return
  CLEANED=1
  trap - INT TERM EXIT
  [ -n "$PIDS" ] || return 0
  say "stopping dev servers..."
  local pid
  for pid in $PIDS; do kill_tree "$pid"; done
  wait 2>/dev/null || true
  say "stopped. Postgres is still running (make down to stop it)."
}
trap cleanup EXIT
trap 'exit 130' INT TERM

port_free() {
  ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

# start <name> <dir> <command...>
start() {
  local name=$1 dir=$2
  shift 2
  : >"$LOG_DIR/$name.log"
  (
    (cd "$dir" && exec "$@") 2>&1 \
      | tee -a "$LOG_DIR/$name.log" \
      | awk -v p="[$name] " '{ print p $0; fflush() }'
  ) &
  PIDS="$PIDS $!"
  NAMES="$NAMES $name"
}

# ---- preflight ---------------------------------------------------------------
"$ROOT/scripts/check-submodule.sh"
command -v uv >/dev/null || die "uv not found (https://docs.astral.sh/uv/). Then: make setup"
command -v npm >/dev/null || die "npm not found (install Node 22+). Then: make setup"
[ -x "$ROOT/.venv/bin/uvicorn" ] || die "Artemis venv missing (.venv/bin/uvicorn). Run: make setup"
[ -d "$ROOT/voiceflow-studio/frontend/node_modules" ] || die "Studio UI deps missing. Run: make setup"
[ -d "$ROOT/frontend/node_modules" ] || die "Artemis dashboard deps missing. Run: make setup"
[ -f "$ROOT/voiceflow-studio/backend/.env" ] \
  || say "note: voiceflow-studio/backend/.env not found; live calls need provider keys (cp voiceflow-studio/backend/.env.example voiceflow-studio/backend/.env)"

for p in $STUDIO_API_PORT $STUDIO_UI_PORT $ARTEMIS_API_PORT $DASHBOARD_PORT; do
  port_free "$p" || die "port $p is already in use (lsof -nP -iTCP:$p -sTCP:LISTEN). Is 'make up' running? Try 'make down'."
done

if [ "${SKIP_POSTGRES:-0}" != 1 ]; then
  command -v docker >/dev/null || die "docker not found. Install Docker Desktop or set SKIP_POSTGRES=1 with your own Postgres on :5433"
  docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker Desktop (or SKIP_POSTGRES=1)."
  say "starting Postgres (host :5433)..."
  docker compose -f "$ROOT/compose.yaml" up -d --wait postgres
fi

# ---- processes ---------------------------------------------------------------
STUDIO_RELOAD_FLAG=""
[ "${STUDIO_RELOAD:-0}" = 1 ] && STUDIO_RELOAD_FLAG="--reload"

# Exported values win over voiceflow-studio/backend/.env (pydantic-settings precedence).
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

start studio-api "$ROOT/voiceflow-studio/backend" env \
  ARTEMIS_API_URL="http://localhost:$ARTEMIS_API_PORT" \
  ARTEMIS_DASHBOARD_URL="http://localhost:$DASHBOARD_PORT" \
  CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:$STUDIO_UI_PORT,http://localhost:$DASHBOARD_PORT}" \
  uv run uvicorn app.main:app --host 127.0.0.1 --port "$STUDIO_API_PORT" $STUDIO_RELOAD_FLAG

start studio-ui "$ROOT/voiceflow-studio/frontend" env \
  NEXT_PUBLIC_API_URL="http://localhost:$STUDIO_API_PORT" \
  NEXT_PUBLIC_ARTEMIS_DASHBOARD_URL="http://localhost:$DASHBOARD_PORT" \
  npm run dev -- --port "$STUDIO_UI_PORT"

# Artemis reads provider keys from the process env; load the root .env if present.
# shellcheck disable=SC2016  # expanded by the inner bash, after .env is sourced
start artemis-api "$ROOT" env \
  STUDIO_API_URL="http://localhost:$STUDIO_API_PORT" \
  STUDIO_UI_URL="http://localhost:$STUDIO_UI_PORT" \
  ARTEMIS_CORS_ORIGINS="http://localhost:$DASHBOARD_PORT,http://localhost:$STUDIO_UI_PORT" \
  ARTEMIS_PORT="$ARTEMIS_API_PORT" \
  bash -c '
    if [ -f .env ]; then set -a; . ./.env; set +a; fi
    export ARTEMIS_DATABASE_PATH="${ARTEMIS_DATABASE_PATH:-artemis_voice.db}"
    exec .venv/bin/uvicorn artemisVoice.Evals.api.app:app \
      --host 127.0.0.1 --port "$ARTEMIS_PORT" --reload --reload-dir backend
  '

start artemis-dashboard "$ROOT/frontend" env \
  VITE_STUDIO_UI_URL="http://localhost:$STUDIO_UI_PORT" \
  VITE_STUDIO_API_URL="http://localhost:$STUDIO_API_PORT" \
  npm run dev -- --port "$DASHBOARD_PORT" --strictPort

cat <<URLS

  VoiceFlow Studio UI   http://localhost:$STUDIO_UI_PORT
  VoiceFlow Studio API  http://localhost:$STUDIO_API_PORT/api/health
  Artemis dashboard     http://localhost:$DASHBOARD_PORT
  Artemis API           http://localhost:$ARTEMIS_API_PORT/health
  Postgres              localhost:5433 (voiceflow/voiceflow)
  Logs                  $LOG_DIR/<service>.log

  Ctrl-C stops all dev servers.

URLS

# Exit (and clean up the rest) as soon as any server dies.
while :; do
  # shellcheck disable=SC2086  # intentional word splitting of the name list
  set -- $NAMES
  for pid in $PIDS; do
    name=$1
    shift
    if ! kill -0 "$pid" 2>/dev/null; then
      say "$name exited; see $LOG_DIR/$name.log. Shutting down."
      exit 1
    fi
  done
  sleep 2
done
