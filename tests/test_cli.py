import asyncio

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
