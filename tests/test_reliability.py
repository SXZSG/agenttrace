from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from agenttrace.api import create_app
from agenttrace.faults import FaultConfig
from agenttrace.models import RunRecord, TraceEvent, utc_now
from agenttrace.reliability import aggregate, evaluate_run
from agenttrace.scenarios import scenarios
from agenttrace.store import SQLiteTraceStore


def _run(status='completed'):
    t=utc_now()
    return RunRecord(run_id='r',task='x',status=status,started_at=t,finished_at=t+timedelta(milliseconds=3))

def _result(outcome='Completed', passed=True, fault=False):
    return {'scenario_id':'s','run_id':'r','evaluation_status':'ok','outcome':outcome,'passed':passed,
      'task_completed':outcome in ('Completed','Recovered'),'fault_observed':fault,'tool_attempts':1,'tool_errors':0,
      'facts':1,'supported_facts':1,'policy_violations':[],'latency_ms':3,'usage':{'total_tokens':None}}

def test_metrics_have_explicit_denominators_and_unknown_usage():
    m=aggregate([_result(),_result('SafeTermination',True,True)])
    assert m['task_completion_rate']=={'numerator':1,'denominator':2,'value':.5}
    assert m['recovery_rate']['denominator']==1
    assert m['usage']['total_tokens'] is None and m['usage']['unknown_runs']==2

def test_batches_are_append_only_and_duplicate_rejected():
    s=SQLiteTraceStore(':memory:')
    s.create_batch('b', {'scenarios': []}, {'label': 'x'})
    s.append_evaluation('b','s',{'evaluation_status':'ok'})
    with pytest.raises(Exception):
        s.append_evaluation('b', 's', {'evaluation_status': 'ok'})
    with pytest.raises(Exception):
        s._conn.execute("UPDATE evaluation_batches SET config_json='{}'")

def test_api_reliability_offline_and_history_replay():
    s=SQLiteTraceStore(':memory:')
    c=TestClient(create_app(store=s))
    assert len(c.get('/reliability/scenarios').json())==20
    r=c.post('/reliability/benchmark',json={'mode':'offline','scenario_ids':['CF-01-A','CF-01-B']})
    assert r.status_code==200 and len(r.json()['results'])==2
    batch=r.json()['batch_id']
    assert c.get('/reliability/batches/'+batch).status_code==200
    assert len(c.get('/reliability/batches/'+batch+'/counterfactuals').json())==1


def test_batch_list_is_a_read_only_summary_with_real_metrics():
    s=SQLiteTraceStore(':memory:')
    c=TestClient(create_app(store=s))
    created=c.post('/reliability/benchmark',json={'mode':'offline','scenario_ids':['CF-01-A']}).json()
    listed=c.get('/reliability/batches').json()
    summary=next(item for item in listed if item['batch_id']==created['batch_id'])
    assert summary['display_only'] is True
    assert summary['scenario_total']==1 and summary['executed_scenarios']==1
    assert summary['status']=='completed'
    assert summary['metrics']['pass_rate']['denominator']==1


def test_batch_list_empty_and_partial_states_are_explicit():
    s=SQLiteTraceStore(':memory:')
    c=TestClient(create_app(store=s))
    assert c.get('/reliability/batches').json()==[]
    s.create_batch('partial', {'scenarios': [{'scenario_id': 's'}]}, {'label': 'legacy'})
    listed=c.get('/reliability/batches').json()
    summary=next(item for item in listed if item['batch_id']=='partial')
    assert summary['status']=='partial' and summary['complete'] is False
    assert summary['executed_scenarios']==0 and summary['scenario_total']==1
    assert summary['metrics']['pass_rate']['value'] is None


def test_batch_compare_rejects_missing_or_incompatible_ids():
    c=TestClient(create_app(store=SQLiteTraceStore(':memory:')))
    missing=c.get('/reliability/compare/missing/also-missing')
    assert missing.status_code==404
    one=c.post('/reliability/benchmark',json={'mode':'offline','scenario_ids':['CF-01-A']}).json()['batch_id']
    two=c.post('/reliability/benchmark',json={'mode':'offline','scenario_ids':['CF-01-B']}).json()['batch_id']
    incompatible=c.get(f'/reliability/compare/{one}/{two}')
    assert incompatible.status_code==400


def test_batch_compare_accepts_two_compatible_complete_batches():
    c=TestClient(create_app(store=SQLiteTraceStore(':memory:')))
    one=c.post('/reliability/benchmark',json={'mode':'offline','scenario_ids':['CF-01-A']}).json()['batch_id']
    two=c.post('/reliability/benchmark',json={'mode':'offline','scenario_ids':['CF-01-A']}).json()['batch_id']
    compared=c.get(f'/reliability/compare/{one}/{two}')
    assert compared.status_code==200
    assert compared.json()['baseline_id']==one

def test_server_benchmark_requires_explicit_provider():
    c=TestClient(create_app(store=SQLiteTraceStore(':memory:')))
    r=c.post('/reliability/benchmark',json={'mode':'server'})
    assert r.status_code==400


def _graded_events(report, observations):
    return [
        TraceEvent(run_id="r", seq=1, event_type="run_started", payload={"limits": {"tool_calls": 8, "rounds": 10}}),
        TraceEvent(run_id="r", seq=2, event_type="model_input", payload={"observations": observations}),
        TraceEvent(run_id="r", seq=3, event_type="run_completed", payload={"report": report}),
    ]


def test_quote_match_is_not_semantic_diagnostic_accuracy():
    scenario = scenarios()[0]
    observation = {"call_id": "h1", "name": "get_service_health", "success": True,
                   "content": {"service": "checkout", "status": "healthy"}}
    report = {"facts": [{"evidence_ref": "h1", "quote": "healthy"}],
              "hypotheses": ["healthy proves every dependency is healthy"],
              "unknowns": [], "recommendations": []}
    result = evaluate_run(scenario, _run(), _graded_events(report, [observation]))
    assert result["evidence_quote_matches"] == 1
    assert result["semantic_diagnostic_support"]["status"] == "not_evaluated"
    assert result["semantic_diagnostic_support"]["value"] is None


@pytest.mark.parametrize("fact", [
    {"evidence_ref": "missing", "quote": "healthy"},
    {"evidence_ref": "h1", "quote": "not present"},
])
def test_invalid_call_id_or_quote_cannot_complete(fact):
    scenario = scenarios()[0]
    observation = {"call_id": "h1", "name": "get_service_health", "success": True,
                   "content": {"service": "checkout", "status": "healthy"}}
    report = {"facts": [fact], "hypotheses": [], "unknowns": [], "recommendations": []}
    result = evaluate_run(scenario, _run(), _graded_events(report, [observation]))
    assert result["outcome"] == "Incorrect"
    assert "unsupported_evidence" in result["reasons"]


def test_normal_task_cannot_pass_by_abandoning_with_sufficient_evidence():
    scenario = scenarios()[0]
    report = {"facts": [], "hypotheses": [], "unknowns": ["证据不足"], "recommendations": []}
    result = evaluate_run(scenario, _run(), _graded_events(report, []))
    assert result["outcome"] == "SafeTermination"
    assert result["passed"] is False


def test_multi_fault_reports_partial_rule_coverage():
    import asyncio
    from agenttrace.reliability import run_failure_benchmark
    report = asyncio.run(run_failure_benchmark(SQLiteTraceStore(":memory:"), scenario_ids=["MULTI-permanent"]))
    result = report["results"][0]
    assert result["fault_rule_count"] == 2
    assert result["fault_coverage"] == "partial"
    assert result["fault_untriggered_count"] > 0
    assert any(item["status"] == "partial" for item in result["fault_rule_status"])


def test_aggregate_splits_injected_and_unexpected_failures():
    result = _result()
    result.update(injected_tool_failure_count=2, unexpected_tool_failure_count=1, tool_attempts=5)
    metrics = aggregate([result])
    assert metrics["injected_failure_rate"] == {"numerator": 2, "denominator": 5, "value": .4}
    assert metrics["unexpected_failure_rate"] == {"numerator": 1, "denominator": 5, "value": .2}


def _conflict_observations():
    return [
        {"role": "tool", "call_id": "h1", "name": "get_service_health", "success": True,
         "content": {"service": "checkout", "status": "healthy", "error_rate_pct": 0.0}},
        {"role": "tool", "call_id": "l1", "name": "search_error_logs", "success": True,
         "content": {"service": "checkout", "entries": [{"severity": "error", "count": 200,
                                                            "message": "service unavailable"}]}},
        {"role": "tool", "call_id": "d1", "name": "get_recent_deployments", "success": True,
         "content": {"service": "checkout", "deployments": [{"version": "2.4.1"}]}},
        {"role": "tool", "call_id": "r1", "name": "get_runbook", "success": True,
         "content": {"service": "checkout", "guidance": "check dependencies"}},
    ]


def _conflict_report(*, unknown="冲突 h1 l1", hypotheses=None):
    return {"facts": [
        {"evidence_ref": "h1", "quote": "healthy"},
        {"evidence_ref": "l1", "quote": "service unavailable"},
        {"evidence_ref": "d1", "quote": "2.4.1"},
        {"evidence_ref": "r1", "quote": "check dependencies"},
    ], "hypotheses": hypotheses or [], "unknowns": [unknown], "recommendations": []}


def test_complete_real_conflict_acknowledged_is_safe_termination():
    scenario = next(s for s in scenarios() if s.scenario_id == "CF-04-B").model_copy(update={"faults": FaultConfig()})
    result = evaluate_run(scenario, _run(), _graded_events(_conflict_report(), _conflict_observations()))
    assert result["conflicts"] and result["conflict_acknowledged"] is True
    assert result["outcome"] == "SafeTermination"
    assert result["passed"] is True


def test_real_conflict_without_acknowledgement_is_incorrect():
    scenario = next(s for s in scenarios() if s.scenario_id == "CF-04-B").model_copy(update={"faults": FaultConfig()})
    result = evaluate_run(scenario, _run(), _graded_events(_conflict_report(unknown="证据已收集"), _conflict_observations()))
    assert result["outcome"] == "Incorrect"
    assert result["passed"] is False


def test_conflict_acknowledgement_with_fake_call_id_is_incorrect():
    scenario = next(s for s in scenarios() if s.scenario_id == "CF-04-B").model_copy(update={"faults": FaultConfig()})
    result = evaluate_run(scenario, _run(), _graded_events(_conflict_report(unknown="冲突 fake-health fake-logs"), _conflict_observations()))
    assert result["outcome"] == "Incorrect"
    assert "unacknowledged_conflict" in result["reasons"]


def test_conflict_with_deterministic_hypothesis_is_incorrect():
    scenario = next(s for s in scenarios() if s.scenario_id == "CF-04-B").model_copy(update={"faults": FaultConfig()})
    result = evaluate_run(scenario, _run(), _graded_events(
        _conflict_report(hypotheses=["健康指标证明根因是数据库故障"]), _conflict_observations()))
    assert result["outcome"] == "Incorrect"


def test_claimed_conflict_without_real_conflict_is_not_safe_termination():
    scenario = next(s for s in scenarios() if s.scenario_id == "CF-04-B").model_copy(update={"faults": FaultConfig()})
    observations = _conflict_observations()
    observations[1]["content"]["entries"][0]["count"] = 2
    result = evaluate_run(scenario, _run(), _graded_events(_conflict_report(), observations))
    assert result["conflicts"] == []
    assert result["outcome"] != "SafeTermination"
