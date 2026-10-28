import asyncio

from agenttrace.evaluation import run_smoke_evaluation
from agenttrace.security import redact
from agenttrace.store import SQLiteTraceStore


def test_sensitive_values_are_redacted_recursively() -> None:
    value = {"api_key": "dont-log-me", "nested": {"password": "hidden", "safe": "visible"}}
    result = redact(value)
    assert result["api_key"] == "[REDACTED]"
    assert result["nested"]["password"] == "[REDACTED]"
    assert result["nested"]["safe"] == "visible"


def test_deterministic_smoke_evaluation_passes_all_cases() -> None:
    report = asyncio.run(run_smoke_evaluation(SQLiteTraceStore(":memory:")))
    assert report["benchmark"] == "agenttrace-incident-smoke-v1"
    assert report["total_cases"] == 3
    assert report["passed_cases"] == 3
    assert report["pass_rate"] == 1.0
    assert report["mode"] == "deterministic_mock"
