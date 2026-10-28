"""A small deterministic smoke benchmark; not a claim about general LLM quality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agenttrace.models import RunRecord
from agenttrace.providers import MockModelProvider
from agenttrace.runtime import AgentRuntime
from agenttrace.store import SQLiteTraceStore
from agenttrace.tools.incident import build_incident_registry

EXPECTED_TOOLS = [
    "get_service_health",
    "search_error_logs",
    "get_recent_deployments",
    "get_runbook",
]


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    task: str
    expected_terms: tuple[str, ...]


DEFAULT_CASES = [
    EvaluationCase(
        case_id="checkout-incident",
        task="Diagnose the checkout service incident and explain the likely root cause.",
        expected_terms=("结算服务", "连接池", "2.4.1"),
    ),
    EvaluationCase(
        case_id="payments-health",
        task="Investigate the payments service health and recent errors.",
        expected_terms=("支付服务", "正常", "重试预算"),
    ),
    EvaluationCase(
        case_id="inventory-timeout",
        task="Investigate the inventory service timeout incident.",
        expected_terms=("库存服务", "商品目录", "3.2.0"),
    ),
]


async def run_smoke_evaluation(
    store: SQLiteTraceStore,
    *,
    cases: list[EvaluationCase] | None = None,
    runtime_factory: Callable[[], AgentRuntime] | None = None,
) -> dict[str, Any]:
    selected = cases or DEFAULT_CASES

    def default_runtime() -> AgentRuntime:
        return AgentRuntime(
            provider=MockModelProvider(), tools=build_incident_registry(), store=store, max_steps=8
        )

    factory = runtime_factory or default_runtime
    results: list[dict[str, Any]] = []
    for case in selected:
        runtime = factory()
        run: RunRecord = await runtime.run(case.task)
        events = store.get_events(run.run_id)
        actual_tools = [
            str(event.payload.get("tool_name"))
            for event in events
            if event.event_type == "tool_completed"
        ]
        answer = (run.final_answer or "").casefold()
        term_hits = [term for term in case.expected_terms if term.casefold() in answer]
        sequence_match = actual_tools == EXPECTED_TOOLS
        terms_match = len(term_hits) == len(case.expected_terms)
        passed = run.status == "completed" and sequence_match and terms_match
        results.append(
            {
                "case_id": case.case_id,
                "run_id": run.run_id,
                "passed": passed,
                "status": run.status,
                "expected_tools": EXPECTED_TOOLS,
                "actual_tools": actual_tools,
                "tool_sequence_match": sequence_match,
                "expected_terms": list(case.expected_terms),
                "matched_terms": term_hits,
                "required_terms_match": terms_match,
                "final_answer": run.final_answer,
            }
        )
    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    return {
        "benchmark": "agenttrace-incident-smoke-v1",
        "mode": "deterministic_mock",
        "note": "This benchmark validates the runtime and scenario wiring; it does not measure real LLM quality.",
        "total_cases": total,
        "passed_cases": passed,
        "pass_rate": passed / total if total else 0.0,
        "tool_sequence_match_rate": sum(r["tool_sequence_match"] for r in results) / total if total else 0.0,
        "required_terms_match_rate": sum(r["required_terms_match"] for r in results) / total if total else 0.0,
        "results": results,
    }
