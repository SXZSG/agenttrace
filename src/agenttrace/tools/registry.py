"""Allowlisted tool registry with argument validation and bounded execution."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from agenttrace.models import ToolExecutionResult
from agenttrace.faults import FaultConfig, FaultInjector

ArgsModel = TypeVar("ArgsModel", bound=BaseModel)
ToolHandler = Callable[[Any], Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    timeout_seconds: float
    output_model: type[BaseModel] | None = None


class ToolRegistry:
    """Only explicitly registered functions can be executed by an agent."""

    def __init__(self, *, faults: FaultConfig | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.injector = FaultInjector(faults)

    def register(
        self,
        name: str,
        description: str,
        args_model: type[ArgsModel],
        handler: ToolHandler,
        *,
        timeout_seconds: float = 2.0,
        output_model: type[BaseModel] | None = None,
    ) -> None:
        if not name or name in self._tools:
            raise ValueError(f"Tool name must be unique and non-empty: {name!r}")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            args_model=args_model,
            handler=handler,
            timeout_seconds=timeout_seconds,
            output_model=output_model,
        )

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "arguments_schema": tool.args_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        return list(self._tools)

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        started = time.perf_counter()
        definition = self._tools.get(name)
        if definition is None:
            return ToolExecutionResult(
                success=False,
                error_code="unknown_tool",
                error_message=f"Tool '{name}' is not registered.",
                retryable=False,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            parsed_args = definition.args_model.model_validate(arguments)
        except ValidationError as exc:
            # Do not include rejected input values in traces or API responses.
            details = exc.errors(include_url=False)
            summary = "; ".join(
                f"{'.'.join(str(part) for part in item.get('loc', ())) or 'arguments'}: {item.get('type', 'invalid')}"
                for item in details[:3]
            )
            return ToolExecutionResult(
                success=False,
                error_code="invalid_arguments",
                error_message=f"Arguments failed schema validation ({summary}).",
                retryable=False,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        rule, ordinal = self.injector.select(name)
        provenance = {"source": "injected_fault" if rule else "tool_backend",
                      "tool_ordinal": ordinal}
        if rule:
            provenance["fault"] = rule.model_dump()
            result = self.injector.inject(rule, arguments)
            result.provenance = provenance
            result.duration_ms = (time.perf_counter() - started) * 1000
            return self._validate_output(result, definition)

        try:
            if inspect.iscoroutinefunction(definition.handler):
                output = await asyncio.wait_for(
                    definition.handler(parsed_args), timeout=definition.timeout_seconds
                )
            else:
                output = await asyncio.wait_for(
                    asyncio.to_thread(definition.handler, parsed_args),
                    timeout=definition.timeout_seconds,
                )
            return self._validate_output(ToolExecutionResult(
                success=True,
                output=output,
                provenance=provenance,
                duration_ms=(time.perf_counter() - started) * 1000,
            ), definition)
        except TimeoutError:
            return ToolExecutionResult(
                success=False,
                error_code="tool_timeout",
                provenance=provenance,
                error_message=f"Tool '{name}' exceeded its {definition.timeout_seconds:.2f}s timeout.",
                retryable=True,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:  # Tool failures are surfaced as structured observations.
            return ToolExecutionResult(
                success=False,
                error_code="tool_exception",
                provenance=provenance,
                error_message=f"Tool '{name}' failed with {type(exc).__name__}.",
                retryable=False,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

    @staticmethod
    def _validate_output(result: ToolExecutionResult, definition: ToolDefinition):
        if result.success and result.output not in (None, "", [], {}) and definition.output_model:
            try:
                definition.output_model.model_validate(result.output, strict=True)
            except ValidationError:
                return result.model_copy(update={"success": False, "error_code": "invalid_tool_result",
                    "error_message": "工具结果结构不符合已注册的输出契约。", "retryable": False})
        return result
