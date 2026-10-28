import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from agenttrace.models import ModelDecision, ToolCall, TraceEvent
from agenttrace.providers import MockModelProvider
from agenttrace.runtime import AgentRuntime
from agenttrace.store import SQLiteTraceStore
from agenttrace.tools.incident import build_incident_registry
from agenttrace.tools.registry import ToolRegistry


async def test_incident_demo_completes_and_records_expected_tools(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "trace.db")
    runtime = AgentRuntime(provider=MockModelProvider(), tools=build_incident_registry(), store=store)
    run = await runtime.run("Diagnose the checkout service incident.")

    assert run.status == "completed"
    assert run.steps == 4
    assert "结算服务" in (run.final_answer or "")
    assert "连接池" in (run.final_answer or "")
    events = store.get_events(run.run_id)
    completed_tools = [e.payload["tool_name"] for e in events if e.event_type == "tool_completed"]
    assert completed_tools == [
        "get_service_health",
        "search_error_logs",
        "get_recent_deployments",
        "get_runbook",
    ]
    assert [event.seq for event in events] == list(range(1, len(events) + 1))


class AlwaysCallsProvider:
    async def decide(self, *, task: str, history: list[dict[str, Any]], available_tools: list[dict[str, Any]]) -> ModelDecision:
        del task, history, available_tools
        return ModelDecision(tool_calls=[ToolCall(name="get_service_health", arguments={"service": "checkout"})])


async def test_max_step_budget_stops_execution(tmp_path: Path) -> None:
    store = SQLiteTraceStore(tmp_path / "budget.db")
    runtime = AgentRuntime(
        provider=AlwaysCallsProvider(), tools=build_incident_registry(), store=store, max_steps=1
    )
    run = await runtime.run("Inspect checkout")
    assert run.status == "failed"
    assert run.steps == 1
    assert "budget" in (run.final_answer or "").casefold()
    assert any(event.event_type == "budget_exhausted" for event in store.get_events(run.run_id))


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryProvider:
    async def decide(self, *, task: str, history: list[dict[str, Any]], available_tools: list[dict[str, Any]]) -> ModelDecision:
        del task, available_tools
        if history:
            return ModelDecision(final_answer="recovered")
        return ModelDecision(tool_calls=[ToolCall(name="flaky", arguments={})])


async def test_retryable_timeout_is_retried_once(tmp_path: Path) -> None:
    calls = 0

    async def flaky(_: EmptyArgs) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.05)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register("flaky", "A transiently slow tool", EmptyArgs, flaky, timeout_seconds=0.01)
    store = SQLiteTraceStore(tmp_path / "retry.db")
    runtime = AgentRuntime(provider=RetryProvider(), tools=registry, store=store, max_tool_retries=1)
    run = await runtime.run("Exercise retry")
    events = store.get_events(run.run_id)

    assert run.status == "completed"
    assert calls == 2
    assert any(event.event_type == "tool_retry" for event in events)
    assert sum(event.event_type == "tool_completed" for event in events) == 1


def test_trace_store_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    first = SQLiteTraceStore(path)
    run = first.create_run("persisted-run", "test persistence")
    first.finish_run(run.run_id, status="completed", final_answer="ok", steps=0)
    first.append_event(
        TraceEvent(run_id=run.run_id, seq=1, event_type="run_completed", payload={"status": "completed"})
    )
    first.close()

    reopened = SQLiteTraceStore(path)
    restored = reopened.get_run("persisted-run")
    assert restored is not None
    assert restored.status == "completed"
    assert reopened.get_events("persisted-run")[0].event_type == "run_completed"
