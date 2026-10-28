"""Typed data contracts shared by the runtime, API, and evaluation harness."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(StrictModel):
    """A model-proposed call to a registered tool."""

    call_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    argument_error: bool = False


class ModelDecision(StrictModel):
    """One provider response: either tool calls or a final answer."""

    tool_calls: list[ToolCall] = Field(default_factory=list)
    final_answer: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    report: dict[str, Any] | None = None


class RunRequest(StrictModel):
    task: str = Field(min_length=3, max_length=4000)


class RunRecord(StrictModel):
    run_id: str
    mode: str = "mock"
    model: str = "deterministic-mock"
    task: str
    status: Literal["running", "completed", "failed"]
    final_answer: str | None = None
    steps: int = 0
    started_at: datetime
    finished_at: datetime | None = None


class TraceEvent(StrictModel):
    run_id: str
    seq: int = Field(ge=1)
    event_type: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(StrictModel):
    success: bool
    output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    duration_ms: float = 0.0
    provenance: dict[str, Any] = Field(default_factory=dict)


class RunResponse(StrictModel):
    run: RunRecord
    trace_url: str
    replay_url: str


class ReplayStep(StrictModel):
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    success: bool
    output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: float | None = None


class ReplayResponse(StrictModel):
    run_id: str
    steps: list[ReplayStep]
    note: str = "此回放仅展示已记录的工具结果，不会重新调用模型或工具。"
