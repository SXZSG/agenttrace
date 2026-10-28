"""FastAPI application exposing runs, traces, replay, and the smoke benchmark."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse

from agenttrace.evaluation import run_smoke_evaluation
from agenttrace.models import ReplayResponse, RunRecord, RunRequest, RunResponse, TraceEvent
from agenttrace.llm import provider_from_env
from agenttrace.security import redact
from agenttrace.replay import build_replay
from agenttrace.runtime import AgentRuntime
from agenttrace.store import SQLiteTraceStore
from agenttrace.tools.incident import build_incident_registry
from agenttrace.reliability import AgentConfig, batch_report, compare_batches, counterfactual_pairs, run_failure_benchmark
from agenttrace.scenarios import scenarios as benchmark_scenarios


def create_app(
    *, store: SQLiteTraceStore | None = None, provider: Any | None = None
) -> FastAPI:
    trace_store = store or SQLiteTraceStore(os.getenv("AGENTTRACE_DB_PATH", "agenttrace.sqlite3"))
    model_provider = provider or provider_from_env()
    app = FastAPI(
        title="AgentTrace API",
        version="0.2.0",
        description="本地智能体执行、轨迹回放与确定性评测工作台。",
    )
    app.state.trace_store = trace_store
    app.state.provider = model_provider

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI's default validation payload echoes rejected input, including secrets.
        return JSONResponse(status_code=422, content={"detail": "请求参数无效，请检查任务长度和字段。"})

    def make_runtime() -> AgentRuntime:
        return AgentRuntime(
            provider=app.state.provider,
            tools=build_incident_registry(),
            store=app.state.trace_store,
            max_steps=8,
        )

    def display_batch(raw: dict[str, Any]) -> dict[str, Any]:
        """Project an immutable batch into a read-only UI summary.

        Legacy rows with incomplete result fields remain readable. Their
        unavailable metrics are represented as unknown rather than inferred.
        """
        try:
            report = batch_report(trace_store, raw["batch_id"])
            report["metrics_available"] = True
        except (KeyError, TypeError, ValueError):
            total = len(raw.get("manifest", {}).get("scenarios", []))
            report = {
                **raw,
                "status": "completed" if len(raw.get("results", [])) == total else "partial",
                "metrics": {"pass_rate": {"numerator": None, "denominator": None, "value": None}},
                "metrics_available": False,
                "metrics_note": "历史结果字段不完整，比例指标未知",
            }
        total = len(report.get("manifest", {}).get("scenarios", []))
        report["scenario_total"] = total
        report["executed_scenarios"] = len(report.get("results", []))
        report["complete"] = report["status"] == "completed"
        report["display_only"] = True
        return report

    @app.get("/", include_in_schema=False)
    async def home() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/health", summary="健康检查", description="检查本地 AgentTrace API 是否可用。")
    async def health() -> dict[str, str]:
        configured = app.state.provider
        error = getattr(configured, "configuration_error", None)
        return {"status": "configuration_error" if error else "ok",
                "provider_mode": type(configured).__name__,
                "mode": getattr(configured, "mode", "mock"),
                "model": redact(getattr(configured, "model", "unknown"),
                                secrets=(getattr(configured, "_api_key", ""),)),
                "tool_backend": "synthetic", "configuration_error": error or ""}

    @app.get("/overview", summary="工作台总览")
    async def overview() -> dict[str, Any]:
        """Return only persisted/read-only data for the product overview."""
        configured = app.state.provider
        batches = trace_store.list_batches(5)
        return {
            "product": "AgentTrace",
            "value_proposition": "追踪 Agent 执行、定位失败原因、复现故障并验证改进。",
            "mode": getattr(configured, "mode", "mock"),
            "model": redact(getattr(configured, "model", "unknown"),
                            secrets=(getattr(configured, "_api_key", ""),)),
            "provider_status": "configuration_error" if getattr(configured, "configuration_error", None) else "ok",
            "tool_backend": "synthetic",
            "recent_runs": [r.model_dump(mode="json") for r in trace_store.list_runs(limit=5)],
            "recent_batches": [{"batch_id": b["batch_id"], "created_at": b["created_at"],
                                "status": "completed" if len(b["results"]) == len(b["manifest"]["scenarios"]) else "partial",
                                "scenario_count": len(b["manifest"]["scenarios"])} for b in batches],
        }

    @app.get("/runs", response_model=list[RunRecord], summary="列出最近运行记录")
    async def list_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[RunRecord]:
        return trace_store.list_runs(limit=limit)

    @app.post("/runs", response_model=RunResponse, status_code=201, summary="提交并执行任务")
    async def create_run(request: RunRequest) -> RunResponse:
        run = await make_runtime().run(request.task)
        return RunResponse(run=run, trace_url=f"/runs/{run.run_id}/trace", replay_url=f"/runs/{run.run_id}/replay")

    @app.get("/runs/{run_id}", response_model=RunRecord, summary="获取任务结果")
    async def get_run(run_id: str) -> RunRecord:
        run = trace_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="未找到对应的运行记录")
        return run

    @app.get("/runs/{run_id}/trace", response_model=list[TraceEvent], summary="获取执行轨迹")
    async def get_trace(run_id: str) -> list[TraceEvent]:
        if trace_store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="未找到对应的运行记录")
        return trace_store.get_events(run_id)

    @app.get("/runs/{run_id}/replay", response_model=ReplayResponse, summary="查看执行回放")
    async def get_replay(run_id: str) -> ReplayResponse:
        if trace_store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="未找到对应的运行记录")
        return build_replay(run_id, trace_store.get_events(run_id))

    @app.post("/evaluations/demo", summary="运行内置确定性评测")
    async def evaluate_demo() -> dict[str, Any]:
        return await run_smoke_evaluation(trace_store)

    @app.get("/reliability/scenarios", summary="列出故障评测场景")
    async def reliability_scenarios() -> list[dict[str, Any]]:
        return [s.model_dump(mode="json") for s in benchmark_scenarios()]

    @app.get("/reliability/batches", summary="列出故障评测批次")
    async def reliability_batches(limit: int = Query(default=30, ge=1, le=100)) -> list[dict[str, Any]]:
        # Project immutable SQLite rows into a read-only display summary.  The
        # summary is computed from the stored results each time and never
        # writes metrics back into the historical batch record.
        summaries = []
        for raw in trace_store.list_batches(limit):
            summaries.append(display_batch(raw))
        return summaries

    @app.get("/reliability/batches/{batch_id}", summary="查看故障评测批次")
    async def reliability_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_report(trace_store, batch_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="未找到评测批次")

    @app.get("/reliability/batches/{batch_id}/counterfactuals", summary="查看反事实对照")
    async def reliability_counterfactuals(batch_id: str) -> list[dict[str, Any]]:
        batch = trace_store.get_batch(batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="未找到评测批次")
        return counterfactual_pairs(batch)

    @app.post("/reliability/benchmark", summary="运行离线故障评测")
    async def reliability_benchmark(body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        if body.get("mode", "offline") != "offline":
            raise HTTPException(status_code=400, detail="工作台仅允许离线评测；真实模型请通过受控脚本显式运行")
        try:
            config = AgentConfig(**{k: v for k, v in body.items() if k in AgentConfig.model_fields})
            ids = body.get("scenario_ids")
            return await run_failure_benchmark(trace_store, config=config, scenario_ids=ids)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/reliability/compare/{baseline_id}/{candidate_id}", summary="比较两个评测批次")
    async def reliability_compare(baseline_id: str, candidate_id: str) -> dict[str, Any]:
        try:
            return compare_batches(trace_store, baseline_id, candidate_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="未找到一个或多个评测批次")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return app


app = create_app()
