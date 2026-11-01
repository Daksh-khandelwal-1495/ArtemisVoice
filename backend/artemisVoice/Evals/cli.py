from __future__ import annotations

import argparse
import json
from pathlib import Path

from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.evaluation.self_validation import (
    SelfValidationRunner,
    SuiteSplit,
    load_seeded_suite,
)
from artemisVoice.Evals.storage.sqlite import SQLiteTraceStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic ArtemisVoice scenario")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--scenario", type=Path)
    input_group.add_argument("--seeded-suite", type=Path)
    parser.add_argument("--split", choices=list(SuiteSplit), default=SuiteSplit.BLIND)
    parser.add_argument("--database", type=Path, default=Path("artemis_voice.db"))
    args = parser.parse_args()

    if args.seeded_suite:
        report = SelfValidationRunner().run(
            load_seeded_suite(args.seeded_suite), split=SuiteSplit(args.split)
        )
        print(report.model_dump_json(indent=2))
        return

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
