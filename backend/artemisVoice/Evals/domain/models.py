from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Scenario(BaseModel):
    """A deterministic one-turn confirmation-code task used by the MVP harness."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    caller_name: str
    confirmation_code: str = Field(min_length=3, max_length=32)
    expected_database_state: dict[str, str]
    seed: int = Field(ge=0)
    forced_stt_confusion: str | None = None

    def caller_text(self) -> str:
        return f"My name is {self.caller_name}. My confirmation code is {self.confirmation_code}."

