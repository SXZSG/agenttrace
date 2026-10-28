"""Versioned, JSON-serializable benchmark inputs and independent expectations."""

import hashlib
import json
from copy import deepcopy
from typing import Literal

from pydantic import Field
from agenttrace.faults import FaultConfig, FaultRule, ToolName
from agenttrace.models import StrictModel
from agenttrace.tools.incident import (
    ServiceArgs, LogSearchArgs, get_service_health, search_error_logs,
    get_recent_deployments, get_runbook,
)

SUITE_VERSION = "failure-benchmark-v1"
RULE_VERSION = "evidence-rules-v4"
OUTCOME = Literal["Completed", "Recovered", "SafeTermination", "Incorrect"]
HEALTH, LOGS, DEPLOYS, RUNBOOK = (
    "get_service_health", "search_error_logs", "get_recent_deployments", "get_runbook"
)
TOOLS = [HEALTH, LOGS, DEPLOYS, RUNBOOK]


class Expectations(StrictModel):
    required_tools: list[ToolName] = Field(default_factory=lambda: [HEALTH])
    investigate_degraded: bool = True
    max_calls: int = 8
    # Both valid completion and honest uncertainty can be acceptable behavior.
    safe_termination_allowed: bool = False
    allowed_outcomes: list[OUTCOME] = Field(default_factory=lambda: ["Completed"])


class Scenario(StrictModel):
    scenario_id: str
    title: str
    category: str
    task: str
    fixtures: dict[str, dict]
    faults: FaultConfig = Field(default_factory=FaultConfig)
    expectations: Expectations = Field(default_factory=Expectations)
    step_cap: int = 8
    pair_id: str | None = None
    condition: Literal["A", "B"] | None = None


def fixture_data(service="checkout"):
    args = ServiceArgs(service=service)
    return {HEALTH: get_service_health(args), LOGS: search_error_logs(LogSearchArgs(service=service)),
            DEPLOYS: get_recent_deployments(args), RUNBOOK: get_runbook(args)}


def injected(kind, tool=HEALTH, calls=(1,)):
    return FaultConfig(enabled=True, rules=[FaultRule(tool=tool, calls=list(calls), kind=kind)])


def scenarios() -> list[Scenario]:
    """Fresh objects per request; no scenario or counter state is shared."""
    task = "诊断 checkout 模拟服务；若健康指标异常，调查日志、近期部署与手册。基于证据报告事实、推测和未知。"
    base = dict(task=task, fixtures=fixture_data())
    result = [Scenario(scenario_id=f"NORMAL-{service}", title=f"{service} 正常工具链",
                       category="normal", task=task.replace("checkout", service),
                       fixtures=fixture_data(service))
              for service in ("checkout", "payments", "inventory")]
    for kind in ("Timeout", "TransientError", "PermanentError", "EmptyResult", "InvalidResult"):
        allowed = ["Recovered", "SafeTermination"] if kind in ("Timeout", "TransientError") else ["SafeTermination"]
        result.append(Scenario(scenario_id=f"FAULT-{kind}", title=f"健康工具 {kind}",
                               category="single_fault", faults=injected(kind),
                               expectations=Expectations(allowed_outcomes=allowed, safe_termination_allowed=True), **deepcopy(base)))
    result.append(Scenario(scenario_id="MULTI-transient", title="健康与日志分别临时失败",
        category="multi_fault", expectations=Expectations(allowed_outcomes=["Recovered", "SafeTermination"], safe_termination_allowed=True), faults=FaultConfig(enabled=True, rules=[
            FaultRule(tool=t, calls=[1], kind="TransientError") for t in (HEALTH, LOGS)]), **deepcopy(base)))
    result.append(Scenario(scenario_id="MULTI-permanent", title="日志与部署永久失败",
        category="multi_fault", expectations=Expectations(allowed_outcomes=["SafeTermination"], safe_termination_allowed=True), faults=FaultConfig(enabled=True, rules=[
            FaultRule(tool=t, calls=list(range(1,9)), kind="PermanentError") for t in (LOGS, DEPLOYS)]), **deepcopy(base)))
    result.append(Scenario(scenario_id="RECOVERY-exhausted", title="持续超时无法恢复",
        category="recovery_failure", expectations=Expectations(allowed_outcomes=["SafeTermination"], safe_termination_allowed=True),
        faults=injected("Timeout", calls=tuple(range(1,9))), **deepcopy(base)))
    result.append(Scenario(scenario_id="BUDGET-one", title="仅一次调用预算",
        category="budget", expectations=Expectations(allowed_outcomes=["SafeTermination"], safe_termination_allowed=True),
        step_cap=1, **deepcopy(base)))
    for pair in ("CF-01", "CF-02", "CF-03", "CF-04"):
        a = deepcopy(base)
        expectations = Expectations()
        if pair == "CF-01":
            a["fixtures"][HEALTH].update(status="healthy", error_rate_pct=0.1, p95_latency_ms=90)
        if pair in ("CF-03", "CF-04"):
            a["task"] = "核对 checkout 同一模拟时间窗口的健康指标、错误日志、近期部署与手册；证据缺失或冲突时明确未知，不作确定诊断。"
            expectations = Expectations(required_tools=TOOLS)
        b = deepcopy(a)
        b_faults = FaultConfig()
        if pair == "CF-01":
            b["fixtures"] = fixture_data()
        elif pair == "CF-02":
            b_faults = injected("Timeout")
        elif pair == "CF-03":
            b_faults = injected("EmptyResult", tool=LOGS, calls=tuple(range(1,9)))
        else:
            b_faults = injected("ContradictoryEvidence", calls=tuple(range(1,9)))
        if pair == "CF-02":
            b_allowed = ["Recovered", "SafeTermination"]
        elif pair in ("CF-03", "CF-04"):
            b_allowed = ["SafeTermination"]
        else:
            b_allowed = ["Completed"]
        for condition, data, config in (("A", a, FaultConfig()), ("B", b, b_faults)):
            condition_allowed = ["Completed"] if condition == "A" else b_allowed
            result.append(Scenario(scenario_id=f"{pair}-{condition}", title=f"{pair} 条件 {condition}",
                category="counterfactual", pair_id=pair, condition=condition, faults=config,
                expectations=expectations.model_copy(update={
                    "allowed_outcomes": condition_allowed,
                    "safe_termination_allowed": condition == "B" and pair in ("CF-02", "CF-03", "CF-04"),
                }, deep=True), **data))
    return result


def manifest(selected: list[Scenario]) -> dict:
    content = {"suite_version": SUITE_VERSION, "rule_version": RULE_VERSION,
               "scenarios": [s.model_dump() for s in sorted(selected, key=lambda x: x.scenario_id)]}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**content, "manifest_hash": hashlib.sha256(encoded.encode()).hexdigest()}
