from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    RECEIVED = "received"
    CLARIFYING = "clarifying"
    PLANNED = "planned"
    WAITING_CONFIRMATION = "waiting_confirmation"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class OutputPreference(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    BOTH = "both"


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, object] = Field(default_factory=dict)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    acceptance_criteria: list[str] = Field(min_length=1)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    clarification_question: str | None = None


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=4000)
    output_preference: OutputPreference = OutputPreference.BOTH


class RepairSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=100_000)


class ToolResult(BaseModel):
    tool_name: str
    ok: bool
    message: str
    data: object | None = None
    error_category: str | None = None


class TaskRecord(BaseModel):
    task_id: str
    request: str
    status: TaskStatus
    output_preference: OutputPreference = OutputPreference.BOTH
    plan: Plan | None = None
    trace: list[ToolResult] = Field(default_factory=list)
    report: str = ""
    clarification_question: str | None = None
    retrieval_mode: str = "disabled"
    context_sources: list[str] = Field(default_factory=list)
    context_truncated: bool = False
