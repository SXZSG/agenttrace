"""Synthetic incident-triage tools used for deterministic demonstrations."""

from __future__ import annotations

from typing import Literal
from copy import deepcopy

from pydantic import BaseModel, ConfigDict

from agenttrace.tools.registry import ToolRegistry
from agenttrace.faults import FaultConfig
from agenttrace.tools.outputs import HealthOutput, LogsOutput, DeploymentsOutput, RunbookOutput

ServiceName = Literal["checkout", "payments", "inventory"]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceArgs(ToolArgs):
    service: ServiceName


class LogSearchArgs(ToolArgs):
    service: ServiceName
    severity: Literal["error", "warning", "all"] = "error"


_HEALTH = {
    "checkout": {"status": "degraded", "error_rate_pct": 18.4, "p95_latency_ms": 1820},
    "payments": {"status": "healthy", "error_rate_pct": 0.3, "p95_latency_ms": 145},
    "inventory": {"status": "degraded", "error_rate_pct": 7.8, "p95_latency_ms": 940},
}
_LOGS = {
    "checkout": [
        {"timestamp": "14:37:12Z", "severity": "error", "message": "connection pool exhausted", "count": 184},
        {"timestamp": "14:37:18Z", "severity": "error", "message": "request timed out waiting for database connection", "count": 92},
    ],
    "payments": [
        {"timestamp": "14:35:02Z", "severity": "warning", "message": "retry budget elevated but within threshold", "count": 3}
    ],
    "inventory": [
        {"timestamp": "14:36:44Z", "severity": "error", "message": "upstream catalog request timed out", "count": 51},
        {"timestamp": "14:36:51Z", "severity": "error", "message": "retry budget exhausted for catalog dependency", "count": 17},
    ],
}
_DEPLOYS = {
    "checkout": [
        {"version": "2.4.1", "time": "14:31:00Z", "summary": "connection pool limit changed from 20 to 5"}
    ],
    "payments": [{"version": "1.8.0", "time": "13:50:00Z", "summary": "dependency patch; no configuration changes"}],
    "inventory": [{"version": "3.2.0", "time": "14:20:00Z", "summary": "catalog client timeout changed from 800ms to 250ms"}],
}
_RUNBOOKS = {
    "checkout": "Check database connection pool saturation. Compare the latest pool configuration change. If symptoms began immediately after deployment, validate rollback safety and follow the incident commander approval process.",
    "payments": "Review payment provider latency and retry budget. Escalate only if error rate or p95 latency exceeds the service SLO for five consecutive minutes.",
    "inventory": "Check catalog dependency health and client timeout configuration. Compare timeout settings with the dependency SLO before changing retry policy.",
}


def get_service_health(args: ServiceArgs) -> dict[str, object]:
    return {"service": args.service, **_HEALTH[args.service]}


def search_error_logs(args: LogSearchArgs) -> dict[str, object]:
    entries = _LOGS[args.service]
    if args.severity != "all":
        entries = [entry for entry in entries if entry["severity"] == args.severity]
    return {"service": args.service, "entries": entries, "matched_entries": len(entries)}


def get_recent_deployments(args: ServiceArgs) -> dict[str, object]:
    return {"service": args.service, "deployments": _DEPLOYS[args.service]}


def get_runbook(args: ServiceArgs) -> dict[str, object]:
    return {"service": args.service, "guidance": _RUNBOOKS[args.service]}


def build_incident_registry(*, tool_timeout_seconds: float = 2.0,
                            faults: FaultConfig | None = None,
                            fixtures: dict[str, dict] | None = None) -> ToolRegistry:
    registry = ToolRegistry(faults=faults)
    registry.register(
        "get_service_health",
        "Read synthetic health indicators for a known service.",
        ServiceArgs,
        get_service_health,
        output_model=HealthOutput,
        timeout_seconds=tool_timeout_seconds,
    )
    registry.register(
        "search_error_logs",
        "Search synthetic service logs by severity.",
        LogSearchArgs,
        search_error_logs,
        output_model=LogsOutput,
        timeout_seconds=tool_timeout_seconds,
    )
    registry.register(
        "get_recent_deployments",
        "Read recent synthetic deployments for a service.",
        ServiceArgs,
        get_recent_deployments,
        output_model=DeploymentsOutput,
        timeout_seconds=tool_timeout_seconds,
    )
    registry.register(
        "get_runbook",
        "Read approved synthetic runbook guidance for a service.",
        ServiceArgs,
        get_runbook,
        output_model=RunbookOutput,
        timeout_seconds=tool_timeout_seconds,
    )
    if fixtures is not None:
        from dataclasses import replace
        if set(fixtures) - set(registry.names()):
            raise ValueError("Unknown fixture tool")
        snapshot = deepcopy(fixtures)
        for name, output in snapshot.items():
            definition = registry._tools[name]
            definition.output_model.model_validate(output, strict=True)
            registry._tools[name] = replace(
                definition, handler=lambda args, value=output: deepcopy(value))
    return registry
