from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.evaluation.judges import JudgeResult


class ReviewState(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class ReviewItem(BaseModel):
    """A human-review record; no judge can approve its own abstention or safety flag."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    state: ReviewState = ReviewState.PENDING
    reason: str
    judge_results: list[JudgeResult]
