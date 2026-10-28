"""Deterministic faults at the registered-tool boundary, never model routing."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from pydantic import Field, model_validator

from agenttrace.models import StrictModel, ToolExecutionResult

ToolName = Literal[
    "get_service_health", "search_error_logs", "get_recent_deployments", "get_runbook"
]
FaultKind = Literal[
    "Timeout", "TransientError", "PermanentError", "EmptyResult", "InvalidResult",
    "ContradictoryEvidence",
]


class FaultRule(StrictModel):
    tool: ToolName
    calls: list[int] = Field(min_length=1, max_length=64)
    kind: FaultKind

    @model_validator(mode="after")
    def valid_calls(self):
        if len(set(self.calls)) != len(self.calls) or any(n < 1 or n > 64 for n in self.calls):
            raise ValueError("calls must be distinct ordinals in 1..64")
        return self


class FaultConfig(StrictModel):
    enabled: bool = False
    rules: list[FaultRule] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unambiguous(self):
        targets = [(r.tool, n) for r in self.rules for n in r.calls]
        if len(targets) != len(set(targets)):
            raise ValueError("Only one fault can target a tool invocation")
        return self


class FaultInjector:
    """Ordinals count validated dispatch attempts, including automatic retries.

    Timeout is a deterministic timeout outcome, not a real sleep or latency claim.
    Each runtime session has context-local counters, including concurrent runs.
    """

    def __init__(self, config: FaultConfig | None = None):
        self.config = (config or FaultConfig()).model_copy(deep=True)
        self._counts: ContextVar[dict[str, int] | None] = ContextVar("fault_counts", default=None)

    @contextmanager
    def session(self):
        token = self._counts.set({})
        try:
            yield
        finally:
            self._counts.reset(token)

    def select(self, name: str) -> tuple[FaultRule | None, int]:
        # wait_for executes in a child task. Share this session's counter object
        # with child tasks; separate sessions still receive separate dictionaries.
        counts = self._counts.get()
        if counts is None:
            counts = {}
            self._counts.set(counts)
        counts[name] = counts.get(name, 0) + 1
        ordinal = counts[name]
        if self.config.enabled:
            for rule in self.config.rules:
                if rule.tool == name and ordinal in rule.calls:
                    return rule, ordinal
        return None, ordinal

    def inject(self, rule: FaultRule, arguments: dict) -> ToolExecutionResult:
        failures = {
            "Timeout": ("tool_timeout", True, "模拟工具超时"),
            "TransientError": ("transient_error", True, "模拟可恢复临时错误"),
            "PermanentError": ("permanent_error", False, "模拟不可恢复错误"),
        }
        if rule.kind in failures:
            code, retryable, message = failures[rule.kind]
            return ToolExecutionResult(success=False, error_code=code,
                                       retryable=retryable, error_message=message)
        if rule.kind == "EmptyResult":
            return ToolExecutionResult(success=True, output=None)
        if rule.kind == "InvalidResult":
            return ToolExecutionResult(success=True, output={"unexpected": "invalid structure"})
        service = arguments.get("service", "checkout")
        # Deliberately conflict with the normal checkout incident evidence.
        outputs = {
            "get_service_health": {"service": service, "status": "healthy",
                                   "error_rate_pct": 0.0, "p95_latency_ms": 80},
            "search_error_logs": {"service": service, "entries": [{"timestamp": "14:37:12Z",
                "severity": "error", "message": "service unavailable", "count": 200}],
                "matched_entries": 1},
            "get_recent_deployments": {"service": service, "deployments": [
                {"version": "2.4.1", "time": "14:31:00Z", "summary": "no configuration changes"}]},
            "get_runbook": {"service": service, "guidance": "No incident is currently active."},
        }
        return ToolExecutionResult(success=True, output=outputs[rule.tool])
