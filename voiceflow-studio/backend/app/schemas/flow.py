"""Flow graph schema: the single source of truth shared by API, runtime and frontend.

A bot's workflow is a directed graph of nodes. Edges are not stored separately; they are
derived from each node's ``transitions``. The voice runtime compiles this into
``pipecat.flows.NodeConfig`` objects at call time.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

NodeType = Literal["start", "collect", "conversation", "decision", "lookup", "api_call", "transfer", "end"]
FieldType = Literal[
    "string", "number", "integer", "boolean", "enum", "date", "phone", "email",
    "pincode", "pan", "aadhaar_last4", "amount", "vehicle_reg",
]
TransitionKind = Literal["on_complete", "intent", "expression", "fallback"]
ContextStrategyName = Literal["append", "reset", "reset_with_summary"]
FailureAction = Literal["goto", "transfer", "end", "skip"]


class Position(BaseModel):
    x: float = 0
    y: float = 0


class FieldValidation(BaseModel):
    regex: str | None = None
    min: float | None = None          # numeric min, or min length for strings
    max: float | None = None          # numeric max, or max length for strings
    options: list[str] | None = None  # required for enum
    future_only: bool = False         # dates
    past_only: bool = False           # dates (e.g. DOB)


class CaptureField(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")  # snake_case, used as LLM function arg
    label: str
    type: FieldType = "string"
    required: bool = True
    description: str = ""             # shown to the LLM to explain what to capture
    validation: FieldValidation = Field(default_factory=FieldValidation)
    confirm: bool = False             # bot must read the value back and get a yes before submitting
    sensitive: bool = False           # masked in logs / UI (PAN, phone ...)
    max_retries: int = Field(default=3, ge=0, le=10)
    example: str | None = None


class Transition(BaseModel):
    id: str
    target: str                       # node id
    kind: TransitionKind = "on_complete"
    # intent: natural-language description used as the LLM tool description
    # expression: safe boolean expression over captured vars, e.g. "loan_amount > 500000 and city == 'Mumbai'"
    condition: str = ""
    label: str = ""
    priority: int = 0                 # lower evaluated first among expression transitions


class NodeSettings(BaseModel):
    respond_immediately: bool = True
    context_strategy: ContextStrategyName = "append"
    allow_interruptions: bool = True
    silence_timeout_secs: int | None = Field(default=None, ge=3, le=120)  # re-prompt after user silence
    max_attempts: int = Field(default=3, ge=1, le=10)  # node-level failures before on_failure fires
    on_failure: FailureAction = "transfer"
    on_failure_target: str | None = None  # node id when on_failure == "goto"


class LookupConfig(BaseModel):
    dataset: str                       # datasets.name in Postgres
    match: dict[str, str] = Field(default_factory=dict)  # dataset column -> captured var name
    match_mode: Literal["exact", "icontains"] = "exact"
    save_as: str                       # var name the result list/row is stored under
    single: bool = True                # store first row or list
    limit: int = Field(default=5, ge=1, le=50)


class ApiCallConfig(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH"] = "POST"
    url: str                           # may contain {{var}} placeholders
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, Any] | None = None  # {{var}} placeholders resolved from captured vars
    timeout_secs: float = Field(default=8, gt=0, le=30)
    save_as: str = "api_result"
    mock_response: dict[str, Any] | None = None  # used when url is empty or MOCK mode is on


class FlowNode(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    type: NodeType
    label: str
    position: Position = Field(default_factory=Position)
    task_prompt: str = ""              # what the agent should do in this node; supports {{var}}
    say_on_enter: str | None = None    # verbatim TTS line on entry (supports {{var}})
    fields: list[CaptureField] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    settings: NodeSettings = Field(default_factory=NodeSettings)
    lookup: LookupConfig | None = None
    api_call: ApiCallConfig | None = None
    transfer_number: str | None = None   # transfer nodes
    end_reason: str | None = None        # end nodes, e.g. "completed", "not_interested"


class FlowGraph(BaseModel):
    initial_node: str
    global_prompt: str = ""            # persona / role message for all nodes
    variables: dict[str, Any] = Field(default_factory=dict)  # static vars available as {{var}}
    nodes: list[FlowNode]

    @model_validator(mode="after")
    def _basic_integrity(self) -> "FlowGraph":
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node ids")
        if self.initial_node not in ids:
            raise ValueError(f"initial_node '{self.initial_node}' does not exist")
        return self


class FlowIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    node_id: str | None = None
    field: str | None = None
    transition_id: str | None = None


class FlowValidationResult(BaseModel):
    valid: bool                        # false if any error-severity issue
    issues: list[FlowIssue]
