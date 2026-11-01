#!/usr/bin/env bash
# Seeds (idempotent upsert) the VoiceFlow Studio demo bots, datasets and oracles
# (Tata Motors + tata-* fault variants), then prints Studio health.
# Uses the studio-api container when `make up` is running, otherwise the host venv.
# DEMO_RESET=1 drops and recreates all Studio tables first (destroys sessions).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARGS=()
[ "${DEMO_RESET:-0}" = 1 ] && ARGS+=(--reset)

if docker compose -f "$ROOT/compose.yaml" ps --status running --services 2>/dev/null | grep -qx studio-api; then
  echo "[demo-seed] seeding via the studio-api container"
  docker compose -f "$ROOT/compose.yaml" exec -T studio-api python -m app.seed.loader ${ARGS[@]+"${ARGS[@]}"}
else
  "$ROOT/scripts/check-submodule.sh"
  echo "[demo-seed] seeding via the host venv (voiceflow-studio/backend, DATABASE_URL from backend/.env)"
  (cd "$ROOT/voiceflow-studio/backend" && uv run python -m app.seed.loader ${ARGS[@]+"${ARGS[@]}"})
fi

if command -v curl >/dev/null; then
  echo "[demo-seed] Studio health:"
  curl -fsS http://localhost:8000/api/health || echo "(Studio API not reachable on :8000; start it with make dev or make up)"
  echo
fi
