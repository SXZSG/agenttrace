import asyncio

from pydantic import BaseModel, ConfigDict

from agenttrace.tools.registry import ToolRegistry


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CountArgs(BaseModel):
    count: int
    model_config = ConfigDict(extra="forbid")


def test_unknown_tool_is_rejected() -> None:
    result = asyncio.run(ToolRegistry().execute("not_registered", {}))
    assert not result.success
    assert result.error_code == "unknown_tool"


def test_invalid_arguments_are_rejected() -> None:
    registry = ToolRegistry()
    registry.register("count", "Return a count", CountArgs, lambda args: {"count": args.count})
    result = asyncio.run(registry.execute("count", {"count": "not-an-int", "extra": True}))
    assert not result.success
    assert result.error_code == "invalid_arguments"


def test_sync_tool_exception_is_reported_without_leaking_stack_trace() -> None:
    def explode(_: EmptyArgs) -> dict[str, str]:
        raise RuntimeError("sensitive detail should not be returned")

    registry = ToolRegistry()
    registry.register("explode", "A failing tool", EmptyArgs, explode)
    result = asyncio.run(registry.execute("explode", {}))
    assert not result.success
    assert result.error_code == "tool_exception"
    assert "sensitive detail" not in (result.error_message or "")


def test_async_tool_timeout_is_reported() -> None:
    async def slow(_: EmptyArgs) -> dict[str, bool]:
        await asyncio.sleep(0.05)
        return {"done": True}

    registry = ToolRegistry()
    registry.register("slow", "A slow tool", EmptyArgs, slow, timeout_seconds=0.005)
    result = asyncio.run(registry.execute("slow", {}))
    assert not result.success
    assert result.error_code == "tool_timeout"
    assert result.retryable
