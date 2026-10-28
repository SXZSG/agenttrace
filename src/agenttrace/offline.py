"""Explicit deterministic baseline, NOT a model-quality benchmark or LLM."""

import json
from agenttrace.models import ModelDecision, ToolCall
from agenttrace.llm import render_diagnosis


class OfflineEvidenceProvider:
    mode = "offline"
    model = "scripted-evidence-policy-v1"
    prompt_version = "offline-policy-v1"

    def __init__(self, *, strategy="retry_once"):
        self.strategy = strategy

    async def decide(self, *, task, history, available_tools):
        observations = [h for h in history if h.get("role") == "tool"]
        service = next((s for s in ("payments", "inventory") if s in task), "checkout")
        names = {t["name"] for t in available_tools}

        def request(name):
            if name not in names:
                return finish("工具不可用，证据不足，未知。")
            return ModelDecision(tool_calls=[ToolCall(name=name, arguments={"service": service})])

        def finish(unknown):
            facts = [{"evidence_ref": h["call_id"], "quote": json.dumps(h["content"], ensure_ascii=False)}
                     for h in observations if h["success"]]
            report = {"facts": facts, "hypotheses": [], "unknowns": [unknown] if unknown else [],
                      "recommendations": ["人工核查时间窗口与真实监控。"]}
            text, refs = render_diagnosis(json.dumps(report, ensure_ascii=False), observations)
            return ModelDecision(final_answer=text, evidence_refs=refs, report=report)

        if not observations:
            return request("get_service_health")
        last = observations[-1]
        if not last["success"]:
            failures = sum(h["name"] == last["name"] and not h["success"] for h in observations)
            if (self.strategy == "retry_once" and failures < 2 and
                    last["content"].get("error_code") in ("transient_error", "tool_timeout")):
                return request(last["name"])
            return finish("工具失败，证据不足，任务无法完成，原因未知。")
        successful = {h["name"]: h for h in observations if h["success"]}
        health = successful.get("get_service_health", {}).get("content", {})
        if health.get("status") == "healthy" and "核对" not in task:
            return finish("仅确认当前健康指标，其他来源未核查。")
        for name in ("search_error_logs", "get_recent_deployments", "get_runbook"):
            if name not in successful:
                return request(name)
        # Baseline detects its own narrow conflict; evaluator checks independently.
        entries = successful["search_error_logs"]["content"].get("entries", [])
        if health.get("status") == "healthy" and any(e["severity"] == "error" and e["count"] >= 100 for e in entries):
            refs = [successful[n]["call_id"] for n in ("get_service_health", "search_error_logs")]
            return finish("证据冲突 " + " ".join(refs) + "，不能确定原因，任务无法完成。")
        return finish("根因仍需对照真实环境确认；此处仅核验模拟证据。")
