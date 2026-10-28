"""Output contracts for the existing synthetic incident tools."""

from typing import Literal
from pydantic import Field
from agenttrace.models import StrictModel


class HealthOutput(StrictModel):
    service: str
    status: Literal["healthy", "degraded"]
    error_rate_pct: float = Field(ge=0, le=100)
    p95_latency_ms: float = Field(ge=0)


class LogEntry(StrictModel):
    timestamp: str
    severity: Literal["error", "warning"]
    message: str
    count: int = Field(ge=0)


class LogsOutput(StrictModel):
    service: str
    entries: list[LogEntry]
    matched_entries: int = Field(ge=0)


class Deployment(StrictModel):
    version: str
    time: str
    summary: str


class DeploymentsOutput(StrictModel):
    service: str
    deployments: list[Deployment]


class RunbookOutput(StrictModel):
    service: str
    guidance: str
