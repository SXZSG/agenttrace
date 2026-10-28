"""Bounded agent execution loop with durable, structured tracing."""

from __future__ import annotations

import asyncio
import json
import time
from uuid import uuid4
from typing import Any

from agenttrace.llm import ProviderError
from agenttrace.evidence import find_conflicts
from agenttrace.models import RunRecord, ToolCall, ToolExecutionResult, TraceEvent
from agenttrace.providers import ModelProvider
from agenttrace.security import redact
from agenttrace.store import SQLiteTraceStore
from agenttrace.tools.registry import ToolRegistry


class AgentRuntime:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        tools: ToolRegistry,
        store: SQLiteTraceStore,
        max_steps: int = 8,
        max_tool_retries: int = 1,
        max_rounds: int = 10,
        run_timeout: float = 120.0,
        model_timeout: float = 30.0,
        max_output_chars: int = 6000,
        max_repeated_failures: int = 2,
    ) -> None:
        if min(max_steps, max_rounds, max_output_chars, max_repeated_failures) < 1:
            raise ValueError("Execution budgets must be positive")
        if max_tool_retries < 0 or min(run_timeout, model_timeout) <= 0:
            raise ValueError("Invalid retry or timeout budget")
        self.provider, self.tools, self.store = provider, tools, store
        self.max_steps, self.max_tool_retries = max_steps, max_tool_retries
        self.max_rounds, self.run_timeout = max_rounds, run_timeout
        self.model_timeout, self.max_output_chars = model_timeout, max_output_chars
        self.max_repeated_failures = max_repeated_failures

    def _safe(self, value: Any) -> Any:
        return redact(value, secrets=(getattr(self.provider, "_api_key", ""),))

    def _bounded_output(self, value: Any) -> Any:
        safe = self._safe(value)
        encoded = json.dumps(safe, ensure_ascii=False)
        if len(encoded) > self.max_output_chars:
            return {
                "truncated": True,
                "preview": encoded[: self.max_output_chars],
                "note": "工具结果已截断，仅可依据可见内容。",
            }
        return safe

    async def run(self, task: str) -> RunRecord:
        with self.tools.injector.session():
            return await self._run(task)

    async def _run(self, task: str) -> RunRecord:
        run_id = uuid4().hex
        mode = getattr(self.provider, "mode", "mock")
        model = self._safe(getattr(self.provider, "model", type(self.provider).__name__))
        task = self._safe(task)
        self.store.create_run(run_id, task, mode=mode, model=model)
        seq, steps, attempts_total, round_number = 0, 0, 0, 0
        history: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        failures: dict[str, int] = {}
        permanent: set[str] = set()
        recorded_conflicts: set[str] = set()
        deadline = time.monotonic() + self.run_timeout

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            self.store.append_event(
                TraceEvent(
                    run_id=run_id,
                    seq=seq,
                    event_type=event_type,
                    payload=self._safe({"round": round_number, **payload}),
                )
            )

        emit(
            "run_started",
            {
                "task": task,
                "provider": type(self.provider).__name__,
                "mode": mode,
                "model": model,
                "prompt_version": getattr(self.provider, "prompt_version", "legacy"),
                "tool_backend": "synthetic",
                "fault_config": self.tools.injector.config.model_dump(),
                "limits": {
                    "rounds": self.max_rounds,
                    "tool_calls": self.max_steps,
                    "seconds": self.run_timeout,
                    "max_tool_retries": self.max_tool_retries,
                    "max_repeated_failures": self.max_repeated_failures,
                },
            },
        )
        final_answer, status = None, "failed"
        try:
            for round_number in range(1, self.max_rounds + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError("run_timeout", "任务整体执行超时，已停止。")
                started = time.perf_counter()
                emit("model_started", {"model": model, "mode": mode, "attempt": 1})
                emit("model_input", {"observations": [h for h in history if h["role"] == "tool"]})
                try:
                    decision = await asyncio.wait_for(
                        self.provider.decide(
                            task=task,
                            history=history,
                            available_tools=self.tools.describe(),
                        ),
                        timeout=min(self.model_timeout, remaining),
                    )
                except TimeoutError:
                    code = "run_timeout" if remaining <= self.model_timeout else "model_timeout"
                    raise ProviderError(
                        code, "任务整体执行超时。" if code == "run_timeout" else "模型请求超时。"
                    ) from None
                except Exception as exc:
                    emit(
                        "model_failed",
                        {
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempt": 1,
                            "retries": 0,
                            "usage": exc.usage if isinstance(exc, ProviderError) else {},
                        },
                    )
                    raise
                finally:
                    emit(
                        "model_request_finished",
                        {
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                            "attempt": 1,
                            "retries": 0,
                        },
                    )
                emit(
                    "model_decision",
                    {
                        "tool_calls": [call.model_dump() for call in decision.tool_calls],
                        "has_final_answer": decision.final_answer is not None,
                        "usage": decision.usage,
                        "evidence_refs": decision.evidence_refs,
                    },
                )
                if decision.final_answer is not None:
                    final_answer = self._safe(decision.final_answer)
                    status = "completed"
                    emit(
                        "run_completed",
                        {
                            "status": status,
                            "steps": steps,
                            "final_answer": final_answer,
                            "evidence_refs": decision.evidence_refs,
                            "report": decision.report,
                            "termination_reason": "final_answer",
                        },
                    )
                    break
                if not decision.tool_calls:
                    raise ProviderError("empty_model_decision", "模型未返回工具调用或最终结论。")
                if any(c.call_id in seen_ids for c in decision.tool_calls):
                    raise ProviderError("duplicate_call_id", "模型重复使用工具调用 ID，已停止。")
                seen_ids.update(c.call_id for c in decision.tool_calls)
                history.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call.call_id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(
                                        self._safe(call.arguments), ensure_ascii=False
                                    ),
                                },
                            }
                            for call in decision.tool_calls
                        ],
                    }
                )
                for call in decision.tool_calls:
                    if attempts_total >= self.max_steps:
                        emit("budget_exhausted", {"max_steps": self.max_steps, "steps": steps})
                        raise ProviderError(
                            "tool_budget", "工具调用预算（budget）已耗尽，已安全停止。"
                        )
                    steps += 1
                    signature = call.name + json.dumps(call.arguments, sort_keys=True)
                    if signature in permanent or failures.get(signature, 0) >= self.max_repeated_failures:
                        emit("tool_blocked", {"call_id": call.call_id, "tool_name": call.name,
                                              "reason": "repeated_tool_failure"})
                        raise ProviderError("repeated_tool_failure", "相同工具和参数已持续失败，安全停止；证据不足，结论未知。")
                    result, used = await self._run_tool(
                        call,
                        emit,
                        deadline=deadline,
                        call_number=steps,
                        attempt_budget=self.max_steps - attempts_total,
                    )
                    attempts_total += used
                    if not result.success:
                        failures[signature] = failures.get(signature, 0) + 1
                        if result.error_code == "permanent_error":
                            permanent.add(signature)
                        emit("recovery_observation", {"call_id": call.call_id, "tool_name": call.name,
                             "error_code": result.error_code, "source": "tool", "actor": "model_next_decision"})
                    elif failures.get(signature):
                        emit("tool_recovered", {"call_id": call.call_id, "tool_name": call.name,
                                                "actor": "model_followup"})
                        failures[signature] = 0
                    history.append(
                        {
                            "role": "tool",
                            "name": call.name,
                            "call_id": call.call_id,
                            "success": result.success,
                            "content": result.output
                            if result.success
                            else {
                                "error_code": result.error_code,
                                "error_message": self._safe(result.error_message),
                            },
                        }
                    )
                    for conflict in find_conflicts(history):
                        identity = json.dumps(conflict, sort_keys=True)
                        if identity not in recorded_conflicts:
                            emit("evidence_conflict", conflict)
                            recorded_conflicts.add(identity)
            else:
                raise ProviderError("round_budget", "达到最大 Agent 决策轮数，已安全停止。")
        except ProviderError as exc:
            final_answer = str(exc)
            emit(
                "run_failed",
                {
                    "reason": exc.code,
                    "error_source": "runtime" if exc.code in {"run_timeout", "tool_budget", "round_budget", "repeated_tool_failure"} else "model",
                    "message": final_answer,
                    "termination_reason": exc.code,
                    "steps": steps,
                },
            )
        except Exception:
            final_answer = "Agent 执行异常，已停止；请检查服务端配置与执行轨迹。"
            emit("run_failed", {"reason": "runtime_exception", "error_source": "runtime", "steps": steps})
        return self.store.finish_run(
            run_id, status=status, final_answer=self._safe(final_answer), steps=steps
        )

    async def _run_tool(
        self, call: ToolCall, emit: Any, *, deadline: float, call_number: int, attempt_budget: int
    ):
        for attempt in range(1, min(self.max_tool_retries + 1, attempt_budget) + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("run_timeout", "任务整体执行超时，已停止。")
            common = {
                "call_id": call.call_id,
                "tool_name": call.name,
                "call_number": call_number,
                "attempt": attempt,
            }
            emit("tool_started", {**common, "arguments": call.arguments})
            try:
                if call.argument_error:
                    result = ToolExecutionResult(
                        success=False,
                        error_code="invalid_arguments",
                        error_message="工具参数不是有效 JSON 对象。",
                    )
                else:
                    result = await asyncio.wait_for(
                        self.tools.execute(call.name, call.arguments),
                        timeout=remaining,
                    )
            except TimeoutError:
                emit(
                    "tool_failed",
                    {
                        **common,
                        "success": False,
                        "error_code": "run_timeout",
                        "error_message": "整体执行时限已到。",
                        "duration_ms": remaining * 1000,
                    },
                )
                raise ProviderError("run_timeout", "任务整体执行超时，已停止。") from None
            result.output = self._bounded_output(result.output)
            if result.provenance.get("source") == "injected_fault":
                emit("fault_injected", {**common, **result.provenance})
            if result.success and result.output in (None, "", [], {}):
                result = result.model_copy(
                    update={
                        "success": False,
                        "error_code": "empty_tool_result",
                        "error_message": "工具返回空结果，暂无可用证据。",
                    }
                )
            emit(
                "tool_completed" if result.success else "tool_failed",
                {
                    **common,
                    **result.model_dump(),
                    "duration_ms": round(result.duration_ms, 3),
                },
            )
            if result.success and attempt > 1:
                emit("tool_recovered", {**common, "actor": "runtime_auto_retry"})
            if (
                not result.success
                and result.retryable
                and attempt < min(self.max_tool_retries + 1, attempt_budget)
            ):
                emit("tool_retry", {**common, "next_attempt": attempt + 1})
                continue
            return result, attempt
