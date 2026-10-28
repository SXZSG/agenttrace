"""Gate A: faults are deterministic tool-boundary effects, not model instructions."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agenttrace.faults import FaultConfig, FaultRule
from agenttrace.models import ModelDecision, ToolCall
from agenttrace.runtime import AgentRuntime
from agenttrace.store import SQLiteTraceStore
from agenttrace.tools.incident import build_incident_registry


def fault(kind, calls=(1,), tool='get_service_health'):
    return FaultConfig(enabled=True, rules=[FaultRule(tool=tool, calls=list(calls), kind=kind)])


@pytest.mark.parametrize('kind,success,code', [
    ('Timeout', False, 'tool_timeout'), ('TransientError', False, 'transient_error'),
    ('PermanentError', False, 'permanent_error'), ('EmptyResult', True, None),
    ('InvalidResult', False, 'invalid_tool_result'), ('ContradictoryEvidence', True, None),
])
async def test_all_six_faults(kind, success, code):
    tools = build_incident_registry(faults=fault(kind))
    result = await tools.execute('get_service_health', {'service': 'checkout'})
    assert result.success is success and result.error_code == code
    assert result.provenance['fault']['kind'] == kind
    assert result.provenance['tool_ordinal'] == 1
    if kind == 'EmptyResult':
        assert result.output is None
    if kind == 'ContradictoryEvidence':
        assert result.output['status'] == 'healthy'
    if kind == 'PermanentError':
        assert result.retryable is False


async def test_target_tool_and_ordinal_only_and_invalid_args_do_not_consume():
    tools = build_incident_registry(faults=fault('Timeout', (2,)))
    assert (await tools.execute('get_service_health', {'service':'wrong'})).error_code == 'invalid_arguments'
    assert (await tools.execute('get_service_health', {'service':'checkout'})).success
    assert (await tools.execute('get_runbook', {'service':'checkout'})).success
    assert (await tools.execute('get_service_health', {'service':'checkout'})).error_code == 'tool_timeout'
    assert (await tools.execute('get_service_health', {'service':'checkout'})).success


async def test_default_disabled_identical_outputs():
    plain = build_incident_registry()
    disabled = build_incident_registry(faults=FaultConfig(rules=fault('Timeout').rules))
    for name in plain.names():
        a = await plain.execute(name, {'service':'checkout'})
        b = await disabled.execute(name, {'service':'checkout'})
        assert a.success == b.success and a.output == b.output
        assert b.provenance['source'] == 'tool_backend'


async def test_injection_at_dispatch_skips_handler_and_sessions_isolate():
    tools = build_incident_registry(faults=fault('Timeout'))
    original = tools._tools['get_service_health']
    from dataclasses import replace
    handler = AsyncMock(return_value={'service':'checkout','status':'degraded','error_rate_pct':1.,'p95_latency_ms':2.})
    tools._tools['get_service_health'] = replace(original, handler=handler)
    async def session():
        with tools.injector.session():
            first = await tools.execute('get_service_health', {'service':'checkout'})
            second = await tools.execute('get_service_health', {'service':'checkout'})
            return first, second
    pairs = await asyncio.gather(session(), session())
    assert all(a.error_code == 'tool_timeout' and b.success for a,b in pairs)
    assert handler.await_count == 2


@pytest.mark.parametrize('config', [
    {'enabled':True, 'rules':[{'tool':'shell','calls':[1],'kind':'Timeout'}]},
    {'rules':[{'tool':'get_runbook','calls':[0],'kind':'Timeout'}]},
    {'rules':[{'tool':'get_runbook','calls':[1,1],'kind':'Timeout'}]},
    {'rules':[{'tool':'get_runbook','calls':[1],'kind':'Timeout','path':'C:/private'}]},
    {'rules':[{'tool':'get_runbook','calls':[1],'kind':'Timeout'},
              {'tool':'get_runbook','calls':[1],'kind':'PermanentError'}]},
])
def test_unsafe_or_ambiguous_config_rejected(config):
    with pytest.raises(ValidationError):
        FaultConfig.model_validate(config)


async def test_fault_trace_has_exact_rule_and_reproducible_ordinal():
    class Provider:
        async def decide(self, *, history, **kwargs):
            return ModelDecision(final_answer='证据不足，未知') if history else ModelDecision(
                tool_calls=[ToolCall(name='get_service_health', arguments={'service':'checkout'})])
    store = SQLiteTraceStore(':memory:')
    runner = AgentRuntime(provider=Provider(), tools=build_incident_registry(faults=fault('PermanentError')), store=store)
    for _ in range(2):
        run = await runner.run('诊断结算服务')
        injected = [e for e in store.get_events(run.run_id) if e.event_type=='fault_injected']
        assert len(injected) == 1
        assert injected[0].payload['tool_ordinal'] == 1
        assert injected[0].payload['fault'] == fault('PermanentError').rules[0].model_dump()
