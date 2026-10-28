"""Independent evidence-based grading, explicit denominators and saved comparisons."""

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from agenttrace.evidence import find_conflicts
from agenttrace.llm import Diagnosis, SYSTEM_PROMPT
from agenttrace.models import StrictModel
from agenttrace.offline import OfflineEvidenceProvider
from agenttrace.runtime import AgentRuntime
from agenttrace.scenarios import HEALTH, TOOLS, Scenario, manifest, scenarios
from agenttrace.security import redact
from agenttrace.tools.incident import build_incident_registry


class AgentConfig(StrictModel):
    label: str = Field(default="offline-recovery-v1", min_length=1, max_length=80)
    mode: Literal["offline", "server"] = "offline"
    strategy: Literal["stop", "retry_once"] = "retry_once"
    max_tool_retries: int = Field(default=1, ge=0, le=2)
    max_steps: int = Field(default=8, ge=1, le=16)
    max_rounds: int = Field(default=10, ge=1, le=20)


def rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def evaluate_run(scenario: Scenario, run, events) -> dict:
    """Grade observations actually delivered to the provider, not fixture wishes.

    Legacy text alone cannot establish evidence support. Runtime safety stops
    are distinct from rejected/fabricated model reports and task completion.
    """
    starts = [e for e in events if e.event_type == "tool_started"]
    failures = [e for e in events if e.event_type == "tool_failed"]
    injected = [e for e in events if e.event_type == "fault_injected"]
    terminal = next((e for e in reversed(events) if e.event_type in ("run_completed", "run_failed")), None)
    inputs = [e for e in events if e.event_type == "model_input"]
    observations = inputs[-1].payload["observations"] if inputs else []
    evidence = {h["call_id"]: h for h in observations if h.get("success")}
    payload = terminal.payload if terminal else {}
    report = payload.get("report")
    facts, supported, cited_tools = [], [], set()
    reasons = []
    report_valid = False
    if report is not None:
        try:
            parsed = Diagnosis.model_validate(report)
            report = parsed.model_dump()
            facts = report["facts"]
            report_valid = True
        except ValueError:
            reasons.append("invalid_report_schema")
    for fact in facts:
        item = evidence.get(fact["evidence_ref"])
        valid = bool(item and fact["quote"] in json.dumps(item["content"], ensure_ascii=False))
        supported.append(valid)
        if valid:
            cited_tools.add(item["name"])
    if facts and not all(supported):
        reasons.append("unsupported_evidence")
    quote_matches = sum(supported)
    conflicts = find_conflicts(observations)
    # Retain observed conflict even if the model later silently switches evidence.
    if not conflicts:
        conflicts = [e.payload for e in events if e.event_type == "evidence_conflict"]
    unknown_text = " ".join(report.get("unknowns", [])) if report_valid else ""
    conflict_ack = bool(conflicts) and all(
        re.search(r"冲突|不一致|contradict|conflict", unknown_text, re.I)
        and all(ref in unknown_text for ref in c["evidence_refs"]) for c in conflicts
    )
    if conflicts and not conflict_ack:
        reasons.append("unacknowledged_conflict")
    limits = next((e.payload.get("limits", {}) for e in events if e.event_type == "run_started"), {})
    policy_violations = []
    if len(starts) > min(scenario.expectations.max_calls, limits.get("tool_calls", 8)):
        policy_violations.append("tool_budget_exceeded")
    if sum(e.event_type == "model_started" for e in events) > limits.get("rounds", 10):
        policy_violations.append("round_budget_exceeded")
    if any(e.payload.get("error_code") == "unknown_tool" for e in failures):
        policy_violations.append("unauthorized_tool_requested")
    reasons.extend(policy_violations)
    required = set(scenario.expectations.required_tools)
    latest_health = next((h for h in reversed(observations) if h.get("success") and h["name"] == HEALTH), None)
    if latest_health and latest_health["content"].get("status") == "degraded" and scenario.expectations.investigate_degraded:
        required.update(TOOLS)
    # Citing only a service name is insufficient to establish a health conclusion.
    health_supported = bool(latest_health and any(
        f["evidence_ref"] == latest_health["call_id"] and
        latest_health["content"]["status"] in f["quote"] for f in facts))
    enough = required <= cited_tools and health_supported
    has_fault = bool(injected or failures or conflicts)
    task_completed = bool(report_valid and facts and all(supported) and enough and not conflicts
                          and run.status == "completed" and not reasons)
    safe = False
    if not task_completed and not reasons:
        # Safety has two independent evidence paths: insufficient evidence, or
        # complete but irreconcilably conflicting evidence. Both require an
        # explicit, reference-bearing acknowledgement and no hypotheses.
        evidence_gap_ack = bool(re.search(
            r"不足|未知|无法|缺失|不能|unknown|insufficient|cannot", unknown_text, re.I
        ))
        conflict_safe = bool(conflicts and conflict_ack)
        if report_valid and unknown_text.strip() and not report["hypotheses"] and (
            (not enough and evidence_gap_ack) or conflict_safe
        ):
            safe = True
        elif run.status == "failed" and payload.get("reason") in {
            "tool_budget", "round_budget", "run_timeout", "repeated_tool_failure",
            "model_timeout", "model_network_error", "model_rate_limit", "model_api_error",
            "model_refusal", "configuration_error",
        }:
            safe = True
    if not task_completed and not safe and not reasons:
        reasons.append("no_verified_completion_or_safe_termination")
    outcome = ("Recovered" if has_fault else "Completed") if task_completed else (
        "SafeTermination" if safe else "Incorrect")
    configured_targets = {
        (rule.tool, ordinal): rule.model_dump()
        for rule in scenario.faults.rules for ordinal in rule.calls
    } if scenario.faults.enabled else {}
    triggered_targets = {
        (e.payload.get("tool_name"), e.payload.get("tool_ordinal"))
        for e in injected
    }
    untriggered_targets = sorted(set(configured_targets) - triggered_targets)
    if configured_targets:
        coverage_status = ("all" if not untriggered_targets else "partial" if triggered_targets else "none")
    else:
        coverage_status = "disabled"
    # A configured fault that was never reached is not evidence of recovery.
    exposure = not configured_targets or bool(triggered_targets)
    if configured_targets and untriggered_targets:
        reasons.append("faults_not_exercised")
    passed = outcome in scenario.expectations.allowed_outcomes and exposure
    if outcome == "SafeTermination" and not scenario.expectations.safe_termination_allowed:
        passed = False
        reasons.append("safe_termination_not_allowed")
    requests = [e for e in events if e.event_type == "model_started"]
    telemetry = [e.payload.get("usage", {}) for e in events if e.event_type in ("model_decision", "model_failed")]
    known = len(telemetry) == len(requests) and all(isinstance(u.get("total_tokens"), int) for u in telemetry)
    total_tokens = sum(u["total_tokens"] for u in telemetry) if known and requests else None
    latency = (run.finished_at - run.started_at).total_seconds() * 1000 if run.finished_at else None
    injected_failures = [e for e in failures if e.payload.get("provenance", {}).get("source") == "injected_fault"]
    unexpected_failures = [e for e in failures if e not in injected_failures]
    return {"scenario_id": scenario.scenario_id, "run_id": run.run_id,
        "trace_url": f"/runs/{run.run_id}/trace", "replay_url": f"/runs/{run.run_id}/replay",
        "evaluation_status": "ok", "outcome": outcome, "passed": bool(passed),
        "task_completed": task_completed, "fault_observed": has_fault,
        "fault_exercised": coverage_status == "all", "fault_exposure": exposure,
        "conflicts": conflicts, "conflict_acknowledged": conflict_ack,
        "allowed_outcomes": scenario.expectations.allowed_outcomes,
        "reasons": reasons, "required_tools": sorted(required), "cited_tools": sorted(cited_tools),
        "tool_attempts": len(starts), "tool_errors": len(failures),
        "injected_tool_failure_count": len(injected_failures),
        "unexpected_tool_failure_count": len(unexpected_failures),
        "fault_rule_count": len(scenario.faults.rules), "fault_target_count": len(configured_targets),
        "fault_triggered_count": len(triggered_targets),
        "fault_untriggered_count": len(untriggered_targets), "fault_coverage": coverage_status,
        "fault_rule_status": [{
            "tool": rule.tool, "kind": rule.kind, "configured_calls": rule.calls,
            "triggered_calls": sorted(ordinal for ordinal in rule.calls if (rule.tool, ordinal) in triggered_targets),
            "status": ("all" if all((rule.tool, ordinal) in triggered_targets for ordinal in rule.calls)
                       else "partial" if any((rule.tool, ordinal) in triggered_targets for ordinal in rule.calls)
                       else "none")
        } for rule in scenario.faults.rules],
        "facts": len(facts), "supported_facts": sum(supported),
        "evidence_quote_matches": quote_matches,
        "semantic_diagnostic_support": {"value": None, "status": "not_evaluated",
            "reason": "自由文本 hypotheses、recommendations 和最终诊断语义没有客观真值标签"},
        "policy_violations": policy_violations, "latency_ms": latency,
        "usage": {"total_tokens": total_tokens, "known": total_tokens is not None},
        "api_cost": None, "cost_note": "未获取可核验的实际计费数据",
        "recovery_actions": [e.payload for e in events if e.event_type in ("tool_retry", "tool_recovered", "tool_blocked")],
        "injections": [e.payload for e in injected], "final_answer": run.final_answer,
        "error_sources": sorted({e.payload.get("error_source", "tool" if e.event_type == "tool_failed" else "model")
                                 for e in events if e.event_type in ("tool_failed", "model_failed", "run_failed")}),
        "trace_hash": hashlib.sha256(json.dumps([e.model_dump(mode="json") for e in events], sort_keys=True).encode()).hexdigest()}


def aggregate(results: list[dict]) -> dict:
    valid = [r for r in results if r["evaluation_status"] == "ok"]
    affected = [r for r in valid if r["fault_observed"]]
    latencies = [r["latency_ms"] for r in valid if r["latency_ms"] is not None]
    usages = [r["usage"]["total_tokens"] for r in valid if r["usage"]["total_tokens"] is not None]
    return {
        "evaluated": len(valid), "evaluation_errors": len(results) - len(valid),
        "task_completion_rate": rate(sum(r["task_completed"] for r in valid), len(valid)),
        "recovery_rate": rate(sum(r["outcome"] == "Recovered" for r in affected), len(affected)),
        "safe_termination_rate": rate(sum(r["outcome"] == "SafeTermination" for r in valid), len(valid)),
        "tool_error_rate": rate(sum(r["tool_errors"] for r in valid), sum(r["tool_attempts"] for r in valid)),
        "injected_tool_failure_count": sum(r.get("injected_tool_failure_count", 0) for r in valid),
        "unexpected_tool_failure_count": sum(r.get("unexpected_tool_failure_count", r.get("tool_errors", 0)) for r in valid),
        "configured_fault_target_count": sum(r.get("fault_target_count", 0) for r in valid),
        "triggered_fault_target_count": sum(r.get("fault_triggered_count", 0) for r in valid),
        "untriggered_fault_target_count": sum(r.get("fault_untriggered_count", 0) for r in valid),
        "tool_attempt_count": sum(r["tool_attempts"] for r in valid),
        "injected_failure_rate": rate(sum(r.get("injected_tool_failure_count", 0) for r in valid), sum(r["tool_attempts"] for r in valid)),
        "unexpected_failure_rate": rate(sum(r.get("unexpected_tool_failure_count", r.get("tool_errors", 0)) for r in valid), sum(r["tool_attempts"] for r in valid)),
        "evidence_quote_match_rate": rate(sum(r.get("evidence_quote_matches", r.get("supported_facts", 0)) for r in valid), sum(r["facts"] for r in valid)),
        "semantic_diagnostic_support_rate": {"value": None, "status": "not_evaluated",
            "reason": "自由文本诊断语义没有独立真值标签"},
        "policy_violation_rate": rate(sum(bool(r["policy_violations"]) for r in valid), len(valid)),
        "pass_rate": rate(sum(r["passed"] for r in valid), len(valid)),
        "execution_latency_ms": {"mean": statistics.mean(latencies) if latencies else None,
                                 "max": max(latencies) if latencies else None, "samples": len(latencies)},
        "usage": {"total_tokens": sum(usages) if len(usages) == len(valid) and valid else None,
                  "known_subtotal": sum(usages) if usages else None,
                  "known_runs": len(usages), "unknown_runs": len(valid) - len(usages)},
        "api_cost": None, "regression_count": None,
    }


def batch_report(store, batch_id):
    batch = store.get_batch(batch_id)
    if batch is None:
        raise KeyError("Unknown batch")
    batch["status"] = "completed" if len(batch["results"]) == len(batch["manifest"]["scenarios"]) else "partial"
    batch["metrics"] = aggregate(batch["results"])
    batch["note"] = "小样本探索性结果；离线策略不代表真实 LLM 能力。序列变化不等于因果证据。"
    return batch


async def run_failure_benchmark(store, *, config: AgentConfig | None = None,
                                scenario_ids: list[str] | None = None, provider=None):
    config = config or AgentConfig()
    available = {s.scenario_id: s for s in scenarios()}
    ids = scenario_ids if scenario_ids is not None else list(available)
    if not ids or len(ids) != len(set(ids)) or any(i not in available for i in ids):
        raise ValueError("Invalid or duplicate scenario IDs")
    if config.mode == "server" and provider is None:
        raise ValueError("Server provider must be supplied explicitly")
    selected = [available[i] for i in ids]
    dataset = manifest(selected)
    dataset["grader_hash"] = hashlib.sha256(
        Path(__file__).read_bytes()
        + Path(__file__).with_name("evidence.py").read_bytes()
        + Path(__file__).with_name("scenarios.py").read_bytes()
    ).hexdigest()
    effective = config.model_dump()
    effective.update(model=getattr(provider, "model", "scripted-evidence-policy-v1") if config.mode == "server" else "scripted-evidence-policy-v1",
        provider_mode=getattr(provider, "mode", "unknown") if config.mode == "server" else "offline",
        prompt_version=getattr(provider, "prompt_version", "legacy") if config.mode == "server" else "offline-policy-v1",
        prompt_hash=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() if config.mode == "server" else None)
    effective = redact(effective, secrets=(getattr(provider, "_api_key", ""),))
    batch_id = uuid4().hex
    store.create_batch(batch_id, dataset, effective)
    for scenario in selected:
        engine = provider if config.mode == "server" else OfflineEvidenceProvider(strategy=config.strategy)
        runtime = AgentRuntime(provider=engine, tools=build_incident_registry(
            faults=scenario.faults, fixtures=scenario.fixtures), store=store,
            max_steps=min(config.max_steps, scenario.step_cap), max_rounds=config.max_rounds,
            max_tool_retries=config.max_tool_retries)
        run = await runtime.run(scenario.task)
        events = store.get_events(run.run_id)
        try:
            result = evaluate_run(scenario, run, events)
        except Exception as exc:
            # Evaluation failures are not scored as model failures or silently passed.
            result = {"scenario_id": scenario.scenario_id, "run_id": run.run_id,
                "evaluation_status": "error", "outcome": None, "passed": False,
                "error_source": "evaluation", "error_type": type(exc).__name__,
                "trace_url": f"/runs/{run.run_id}/trace"}
        store.append_evaluation(batch_id, scenario.scenario_id, result)
    return batch_report(store, batch_id)


def compare_batches(store, baseline_id: str, candidate_id: str) -> dict:
    a, b = batch_report(store, baseline_id), batch_report(store, candidate_id)
    if a["manifest"] != b["manifest"] or a["status"] != "completed" or b["status"] != "completed":
        raise ValueError("Comparison requires complete batches with identical scenarios and grading rules")
    left = {r["scenario_id"]: r for r in a["results"]}
    rank = {"Incorrect": 0, "SafeTermination": 1, "Completed": 2, "Recovered": 2}
    rows, improvements, regressions = [], [], []
    for right in b["results"]:
        old = left[right["scenario_id"]]
        if old["evaluation_status"] != "ok" or right["evaluation_status"] != "ok":
            raise ValueError("Cannot compare batches with evaluation errors")
        old_score = (int(old["passed"]), rank[old["outcome"]])
        new_score = (int(right["passed"]), rank[right["outcome"]])
        change = "improved" if new_score > old_score else "regressed" if new_score < old_score else "unchanged"
        row = {"scenario_id": right["scenario_id"], "change": change, "baseline": old, "candidate": right}
        rows.append(row)
        if change == "improved":
            improvements.append(right["scenario_id"])
        if change == "regressed":
            regressions.append(right["scenario_id"])
    return {"baseline_id": baseline_id, "candidate_id": candidate_id,
        "baseline_config": a["config"], "candidate_config": b["config"],
        "baseline_metrics": a["metrics"], "candidate_metrics": b["metrics"],
        "regression_count": len(regressions), "regressions": regressions,
        "improvements": improvements, "results": rows,
        "note": "相同场景与规则的小样本探索性比较；未检验统计显著性。"}


def counterfactual_pairs(batch: dict) -> list[dict]:
    results = {r["scenario_id"]: r for r in batch["results"]}
    pairs = {}
    for s in batch["manifest"]["scenarios"]:
        if s["pair_id"]:
            pairs.setdefault(s["pair_id"], {})[s["condition"]] = s
    output = []
    for pair, conditions in pairs.items():
        if set(conditions) != {"A", "B"}:
            continue
        a, b = conditions["A"], conditions["B"]
        if a["task"] != b["task"] or a["expectations"] != b["expectations"] or a["step_cap"] != b["step_cap"]:
            raise ValueError("Counterfactual experimental conditions are not controlled")
        if a["scenario_id"] in results and b["scenario_id"] in results:
            output.append({"pair_id": pair, "task": a["task"], "A": results[a["scenario_id"]],
                           "B": results[b["scenario_id"]],
                           "note": "分别按行为规则评分；不要求工具序列不同，不据单次差异推断因果。"})
    return output
