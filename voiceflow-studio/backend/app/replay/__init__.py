"""Replay sandbox, test oracles, fault injection and patch candidates
(docs/INTEGRATION_CONTRACT.md §2-§3).

* :mod:`app.replay.driver`   - text driver over the real compiled flow (FlowRuntime) + LLM.
* :mod:`app.replay.sandbox`  - dataset snapshots, sandboxed lookups / api calls.
* :mod:`app.replay.faults`   - FaultProfile model and deterministic per-layer fault functions.
* :mod:`app.replay.oracle`   - outcome extraction + oracle check.
* :mod:`app.replay.runtrace` - Artemis RunTrace (schema 1.0) conversion.
* :mod:`app.replay.patches`  - bounded PatchSpec application.
* :mod:`app.replay.service`  - simulate / replay orchestration used by the API routers.
"""

from app.replay.sandbox import snapshot_datasets

__all__ = ["snapshot_datasets"]
