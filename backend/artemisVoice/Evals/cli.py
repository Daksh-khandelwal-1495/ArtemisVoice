from __future__ import annotations

import argparse
import json
from pathlib import Path

from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.storage.sqlite import SQLiteTraceStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic ArtemisVoice scenario")
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=Path("artemis_voice.db"))
    args = parser.parse_args()

    scenario = Scenario.model_validate(json.loads(args.scenario.read_text(encoding="utf-8")))
    trace = DeterministicConfirmationAgent().run(scenario)
    store = SQLiteTraceStore(args.database)
    try:
        store.save(trace)
    finally:
        store.close()
    print(trace.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
