"""Gate B: scripted model responses exercise the unchanged LLM request loop."""
from test_llm import answer, call, response, runtime
from test_faults import fault

from agenttrace.tools.incident import build_incident_registry


async def test_transient_error_auto_retry_is_not_model_recovery():
    runner, store, create = runtime([response(calls=[call()]), answer('call_1')])
    runner.tools = build_incident_registry(faults=fault('TransientError'))
    run = await runner.run('诊断结算服务')
    assert run.status == 'completed' and create.await_count == 2
    events = store.get_events(run.run_id)
    assert sum(e.event_type == 'tool_started' for e in events) == 2
    recovered = [e for e in events if e.event_type == 'tool_recovered']
    assert recovered[0].payload['actor'] == 'runtime_auto_retry'
    observed = [e for e in events if e.event_type == 'model_input'][-1].payload['observations']
    assert observed[0]['success'] is True


async def test_model_sees_failure_then_retries_with_new_decision():
    runner, store, create = runtime([
        response(calls=[call()]), response(calls=[call(cid='second')]), answer('second')
    ], max_tool_retries=0)
    runner.tools = build_incident_registry(faults=fault('TransientError'))
    run = await runner.run('诊断结算服务')
    assert run.status == 'completed' and create.await_count == 3
    events = store.get_events(run.run_id)
    inputs = [e for e in events if e.event_type=='model_input']
    assert inputs[1].payload['observations'][0]['content']['error_code']=='transient_error'
    assert any(e.event_type=='tool_recovered' and e.payload['actor']=='model_followup' for e in events)
    assert events[-1].payload['report']['facts'][0]['evidence_ref']=='second'


async def test_permanent_error_has_no_auto_retry_and_can_stop_without_facts():
    runner, store, _ = runtime([response(calls=[call()]), answer()])
    runner.tools = build_incident_registry(faults=fault('PermanentError'))
    run = await runner.run('诊断结算服务')
    events=store.get_events(run.run_id)
    assert sum(e.event_type=='tool_started' for e in events)==1
    assert events[-1].payload['report']['facts']==[]
    assert events[-1].payload['report']['unknowns']
    assert '尚无工具证据' in run.final_answer


async def test_repeating_permanent_failure_is_blocked_before_dispatch():
    runner, store, _ = runtime([response(calls=[call()]), response(calls=[call(cid='again')])])
    runner.tools = build_incident_registry(faults=fault('PermanentError', (1,2,3)))
    run=await runner.run('诊断结算服务')
    events=store.get_events(run.run_id)
    assert run.status=='failed'
    assert events[-1].payload['reason']=='repeated_tool_failure'
    assert sum(e.event_type=='tool_started' for e in events)==1


async def test_invalid_and_empty_results_become_failures_in_model_context():
    for kind,code in [('EmptyResult','empty_tool_result'),('InvalidResult','invalid_tool_result')]:
        runner,store,_=runtime([response(calls=[call()]),answer()])
        runner.tools=build_incident_registry(faults=fault(kind))
        run=await runner.run('诊断结算服务')
        events=store.get_events(run.run_id)
        observation=[e for e in events if e.event_type=='model_input'][-1].payload['observations'][0]
        assert observation['success'] is False
        assert observation['content']['error_code']==code
        assert events[-1].payload['report']['facts']==[]


async def test_conflict_detected_but_not_inserted_as_instruction():
    runner,store,create=runtime([
        response(calls=[call(),call('search_error_logs',cid='logs')]),
        answer('call_1','healthy',unknowns=['证据冲突 [call_1] [logs]，原因未知'])
    ])
    runner.tools=build_incident_registry(faults=fault('ContradictoryEvidence'))
    run=await runner.run('诊断结算服务')
    events=store.get_events(run.run_id)
    conflict=next(e for e in events if e.event_type=='evidence_conflict')
    assert conflict.payload['evidence_refs']==['call_1','logs']
    assert create.call_args_list[0].kwargs['messages'][0]==create.call_args_list[1].kwargs['messages'][0]
    assert '冲突' in run.final_answer


async def test_repeated_nonretryable_empty_calls_stop_at_limit():
    runner,store,_=runtime([response(calls=[call(cid=str(i))]) for i in range(3)],max_tool_retries=0)
    runner.tools=build_incident_registry(faults=fault('EmptyResult',(1,2,3)))
    run=await runner.run('诊断结算服务')
    events=store.get_events(run.run_id)
    assert events[-1].payload['reason']=='repeated_tool_failure'
    assert sum(e.event_type=='tool_started' for e in events)==2
