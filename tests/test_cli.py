import asyncio
import io
import json
import sys

from agenttrace import cli


def _report(*rows, status="completed"):
    return {
        "status": status,
        "results": list(rows),
        "metrics": {},
    }


def _row(scenario_id, *, passed=True, evaluation_status="ok"):
    return {"scenario_id": scenario_id, "passed": passed, "evaluation_status": evaluation_status}


def test_failure_benchmark_strict_success_returns_zero(monkeypatch):
    async def fake_run(*args, **kwargs):
        return _report(_row("CF-04-B"))

    monkeypatch.setattr(cli, "run_failure_benchmark", fake_run)
    monkeypatch.setattr(cli, "_store", lambda: object())
    assert asyncio.run(cli._failure_benchmark(["CF-04-B"], "offline", strict=True)) == 0


def test_failure_benchmark_strict_failed_scenario_returns_nonzero(monkeypatch):
    async def fake_run(*args, **kwargs):
        return _report(_row("CF-04-B", passed=False))

    monkeypatch.setattr(cli, "run_failure_benchmark", fake_run)
    monkeypatch.setattr(cli, "_store", lambda: object())
    assert asyncio.run(cli._failure_benchmark(["CF-04-B"], "offline", strict=True)) == 1


def test_failure_benchmark_strict_evaluation_error_returns_nonzero(monkeypatch):
    async def fake_run(*args, **kwargs):
        return _report(_row("CF-04-B", passed=False, evaluation_status="error"))

    monkeypatch.setattr(cli, "run_failure_benchmark", fake_run)
    monkeypatch.setattr(cli, "_store", lambda: object())
    assert asyncio.run(cli._failure_benchmark(["CF-04-B"], "offline", strict=True)) == 1


def test_failure_benchmark_json_is_loadable_with_cp1252_stdout(monkeypatch):
    class Cp1252Stdout(io.StringIO):
        encoding = "cp1252"

        def write(self, value):
            value.encode(self.encoding)
            return super().write(value)

    async def fake_run(*args, **kwargs):
        report = _report(_row("CF-04-B"))
        report["message"] = "中文评测结果"
        return report

    stream = Cp1252Stdout()
    monkeypatch.setattr(cli, "run_failure_benchmark", fake_run)
    monkeypatch.setattr(cli, "_store", lambda: object())
    monkeypatch.setattr(sys, "stdout", stream)
    assert asyncio.run(cli._failure_benchmark(["CF-04-B"], "offline", strict=True)) == 0
    assert json.loads(stream.getvalue())["message"] == "中文评测结果"
