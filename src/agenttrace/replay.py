"""Build a read-only replay view from persisted tool events."""

from __future__ import annotations

from typing import Any

from agenttrace.models import ReplayResponse, ReplayStep, TraceEvent


def build_replay(run_id: str, events: list[TraceEvent]) -> ReplayResponse:
    starts: dict[str, dict[str, Any]] = {}
    steps: list[ReplayStep] = []
    for event in events:
        payload = event.payload
        call_id = str(payload.get("call_id", ""))
        if event.event_type == "tool_started" and call_id:
            starts[call_id] = {
                "tool_name": payload.get("tool_name", "unknown"),
                "arguments": payload.get("arguments", {}),
            }
        elif event.event_type in {"tool_completed", "tool_failed"} and call_id:
            started = starts.get(call_id, {})
            steps.append(
                ReplayStep(
                    call_id=call_id,
                    tool_name=str(payload.get("tool_name", started.get("tool_name", "unknown"))),
                    arguments=started.get("arguments", {}),
                    success=bool(payload.get("success", False)),
                    output=payload.get("output"),
                    error_code=payload.get("error_code"),
                    error_message=payload.get("error_message"),
                    duration_ms=payload.get("duration_ms"),
                )
            )
    return ReplayResponse(run_id=run_id, steps=steps)
