from fastapi.testclient import TestClient

from agenttrace.api import create_app
from agenttrace.store import SQLiteTraceStore


def test_api_run_trace_replay_and_not_found() -> None:
    app = create_app(store=SQLiteTraceStore(":memory:"))
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert "任务工作台" in home.text
    assert "版本对比" in home.text

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    overview = client.get("/overview")
    assert overview.status_code == 200
    assert overview.json()["tool_backend"] == "synthetic"

    response = client.post("/runs", json={"task": "Diagnose the checkout service incident."})
    assert response.status_code == 201
    payload = response.json()
    run_id = payload["run"]["run_id"]
    assert payload["run"]["status"] == "completed"
    assert "结算服务" in payload["run"]["final_answer"]
    listed = client.get("/runs?limit=10")
    assert listed.status_code == 200
    assert listed.json()[0]["run_id"] == run_id

    trace = client.get(f"/runs/{run_id}/trace")
    assert trace.status_code == 200
    assert any(event["event_type"] == "tool_completed" for event in trace.json())

    replay = client.get(f"/runs/{run_id}/replay")
    assert replay.status_code == 200
    assert len(replay.json()["steps"]) == 4
    assert "不会重新调用模型或工具" in replay.json()["note"]

    assert client.get("/runs/does-not-exist").status_code == 404


def test_demo_evaluation_endpoint() -> None:
    client = TestClient(create_app(store=SQLiteTraceStore(":memory:")))
    response = client.post("/evaluations/demo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "deterministic_mock"
    assert payload["total_cases"] == 3
    assert payload["passed_cases"] == 3
    assert payload["pass_rate"] == 1.0
