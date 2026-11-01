#!/usr/bin/env bash
# Fails with an actionable message when the Pipecat submodule is not initialised.
# VoiceFlow Studio's backend resolves pipecat-ai from voiceflow-studio/vendor/pipecat.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPECAT="$ROOT/voiceflow-studio/vendor/pipecat"

if [ ! -f "$PIPECAT/pyproject.toml" ]; then
  cat >&2 <<MSG
ERROR: the Pipecat submodule is not initialised ($PIPECAT is empty).
VoiceFlow Studio cannot be installed, tested or built without it. Run:

    git submodule update --init voiceflow-studio/vendor/pipecat
    # or: make setup

MSG
  exit 1
fi
