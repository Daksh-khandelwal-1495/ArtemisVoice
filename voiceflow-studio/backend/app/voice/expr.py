"""Runtime wrapper around :mod:`app.services.expressions` (shared with the flow validator).

The runtime must never crash on a bad expression, so syntax errors evaluate to False.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.services.expressions import ExpressionError, parse_names  # noqa: F401
from app.services.expressions import evaluate as _evaluate


def evaluate(expr: str, vars: dict[str, Any]) -> bool:
    try:
        return bool(_evaluate(expr, vars))
    except Exception as e:  # noqa: BLE001 - includes ExpressionError
        logger.warning(f"expression {expr!r} failed: {e}")
        return False
