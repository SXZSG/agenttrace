"""Command-line entry point for the deterministic demo and smoke benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Sequence

from agenttrace.evaluation import run_smoke_evaluation
from agenttrace.llm import provider_from_env
from agenttrace.runtime import AgentRuntime
from agenttrace.store import SQLiteTraceStore
from agenttrace.tools.incident import build_incident_registry
from agenttrace.reliability import AgentConfig, compare_batches, run_failure_benchmark


def _store() -> SQLiteTraceStore:
    return SQLiteTraceStore(os.getenv("AGENTTRACE_DB_PATH", "agenttrace.sqlite3"))


async def _demo(task: str) -> int:
    store = _store()
    runtime = AgentRuntime(provider=provider_from_env(), tools=build_incident_registry(), store=store)
    run = await runtime.run(task)
    print(json.dumps({"run": run.model_dump(mode="json"), "trace": [e.model_dump(mode="json") for e in store.get_events(run.run_id)]}, indent=2, ensure_ascii=True))
    return 0 if run.status == "completed" else 1


async def _evaluate() -> int:
    store = _store()
    report = await run_smoke_evaluation(store)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["passed_cases"] == report["total_cases"] else 1


async def _failure_benchmark(scenario_ids: list[str] | None, mode: str, strict: bool = False) -> int:
    store = _store()
    provider = provider_from_env() if mode == "server" else None
    report = await run_failure_benchmark(store, config=AgentConfig(mode=mode),
                                         scenario_ids=scenario_ids, provider=provider)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if not strict:
        return 0
    expected = scenario_ids if scenario_ids is not None else [row["scenario_id"] for row in report["results"]]
    results = {row.get("scenario_id"): row for row in report.get("results", [])}
    strict_ok = (
        report.get("status") == "completed"
        and len(results) == len(expected)
        and all(row.get("evaluation_status") == "ok" and row.get("passed") is True
                for row in results.values())
    )
    return 0 if strict_ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agenttrace", description="AgentTrace local demo and evaluation tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="run the deterministic incident-triage demo")
    demo.add_argument(
        "--task",
        default="Diagnose the checkout service incident and explain the likely root cause.",
        help="Task text. Mention checkout, payments, or inventory to select the synthetic service.",
    )
    subparsers.add_parser("eval", help="run the deterministic smoke benchmark")
    failure = subparsers.add_parser("failure-benchmark", help="run the versioned offline reliability benchmark")
    failure.add_argument("--scenario", action="append", dest="scenario_ids", help="scenario ID; repeat to select a subset")
    failure.add_argument("--mode", choices=("offline", "server"), default="offline",
                         help="offline scripted policy or explicit server-configured LLM")
    failure.add_argument("--strict", action="store_true",
                         help="return non-zero when any selected scenario is incomplete, errored, or not passed")
    compare = subparsers.add_parser("compare-batches", help="compare two complete reliability batches")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    args = parser.parse_args(argv)
    if args.command == "demo":
        return asyncio.run(_demo(args.task))
    if args.command == "eval":
        return asyncio.run(_evaluate())
    if args.command == "failure-benchmark":
        return asyncio.run(_failure_benchmark(args.scenario_ids, args.mode, args.strict))
    if args.command == "compare-batches":
        print(json.dumps(compare_batches(_store(), args.baseline, args.candidate), indent=2, ensure_ascii=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
