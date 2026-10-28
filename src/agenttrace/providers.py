"""Model-provider contracts and a deterministic provider for offline development."""

from __future__ import annotations

from typing import Any, Protocol

from agenttrace.models import ModelDecision, ToolCall


class ModelProvider(Protocol):
    """Provider contract. A real LLM adapter can implement this in a later phase."""

    async def decide(
        self,
        *,
        task: str,
        history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> ModelDecision: ...


class MockModelProvider:
    """A scripted provider for reproducible tests; it does not perform LLM reasoning."""

    mode = "mock"
    model = "deterministic-mock"

    def _service_from_task(self, task: str) -> str:
        lowered = task.casefold()
        if "payments" in lowered or "支付" in lowered:
            return "payments"
        if "inventory" in lowered or "库存" in lowered:
            return "inventory"
        if "checkout" in lowered or "结算" in lowered:
            return "checkout"
        return "checkout"

    async def decide(
        self,
        *,
        task: str,
        history: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> ModelDecision:
        del available_tools
        service = self._service_from_task(task)
        observations = [message for message in history if message.get("role") == "tool"]
        completed_tools = [message.get("name") for message in observations]
        if observations and not observations[-1].get("success", False):
            last_name = observations[-1].get("name", "unknown tool")
            failure = observations[-1].get("content", {})
            code = failure.get("error_code", "unknown_error") if isinstance(failure, dict) else "unknown_error"
            tool_names = {
                "get_service_health": "查询服务健康指标",
                "search_error_logs": "检索错误日志",
                "get_recent_deployments": "查询近期部署记录",
                "get_runbook": "读取故障处理手册",
            }
            return ModelDecision(
                final_answer=(
                    f"本次诊断已停止：工具“{tool_names.get(last_name, last_name)}”执行失败（{code}）。"
                    "当前证据不完整，请查看执行轨迹，排除工具故障后再重试。"
                )
            )
        if not completed_tools:
            return ModelDecision(tool_calls=[ToolCall(name="get_service_health", arguments={"service": service})])
        if completed_tools[-1] == "get_service_health":
            return ModelDecision(
                tool_calls=[ToolCall(name="search_error_logs", arguments={"service": service, "severity": "error"})]
            )
        if completed_tools[-1] == "search_error_logs":
            return ModelDecision(tool_calls=[ToolCall(name="get_recent_deployments", arguments={"service": service})])
        if completed_tools[-1] == "get_recent_deployments":
            return ModelDecision(tool_calls=[ToolCall(name="get_runbook", arguments={"service": service})])

        outputs = [message.get("content", {}) for message in observations]
        health = next((item for item in outputs if isinstance(item, dict) and "error_rate_pct" in item), {})
        logs = next((item for item in outputs if isinstance(item, dict) and "entries" in item), {})
        deployments = next((item for item in outputs if isinstance(item, dict) and "deployments" in item), {})
        log_entries = logs.get("entries", []) if isinstance(logs, dict) else []
        deploy_entries = deployments.get("deployments", []) if isinstance(deployments, dict) else []
        top_log = log_entries[0].get("message", "no matching error log") if log_entries else "no matching error log"
        latest_deploy = deploy_entries[0] if deploy_entries else {}
        status = health.get("status", "unknown") if isinstance(health, dict) else "unknown"
        error_rate = health.get("error_rate_pct", "unknown") if isinstance(health, dict) else "unknown"
        deploy_version = latest_deploy.get("version", "未知版本")
        service_names = {"checkout": "结算服务", "payments": "支付服务", "inventory": "库存服务"}
        status_names = {"degraded": "异常", "healthy": "正常", "unknown": "未知"}
        log_names = {
            "connection pool exhausted": "数据库连接池耗尽",
            "request timed out waiting for database connection": "请求等待数据库连接超时",
            "retry budget elevated but within threshold": "重试次数有所上升，但仍在阈值内",
            "upstream catalog request timed out": "上游商品目录服务请求超时",
            "retry budget exhausted for catalog dependency": "商品目录依赖的重试预算已耗尽",
            "no matching error log": "未发现匹配的错误日志",
        }
        deploy_summaries = {
            "connection pool limit changed from 20 to 5": "连接池上限从 20 调整为 5",
            "dependency patch; no configuration changes": "依赖补丁更新，未修改配置",
            "catalog client timeout changed from 800ms to 250ms": "商品目录客户端超时从 800ms 调整为 250ms",
        }
        runbook_summaries = {
            "checkout": "检查数据库连接池是否饱和，并核对最近的连接池配置变更。如果问题在部署后立即出现，应先验证回滚安全性，并遵循事故负责人审批流程。",
            "payments": "检查支付服务提供方的延迟和重试预算。只有当错误率或 P95 延迟连续五分钟超过服务目标时，才应升级处理。",
            "inventory": "检查商品目录依赖的健康状态和客户端超时配置。调整重试策略前，先将超时设置与依赖服务目标进行比较。",
        }
        service_cn = service_names.get(service, service)
        status_cn = status_names.get(str(status), str(status))
        log_cn = log_names.get(str(top_log), str(top_log))
        deploy_summary_cn = deploy_summaries.get(str(latest_deploy.get("summary", "")), "没有近期部署摘要")
        guidance_cn = runbook_summaries.get(service, "未找到操作手册建议")
        answer = (
            f"【诊断对象】{service_cn}（{service}）\n"
            f"【当前状态】{status_cn}；错误率 {error_rate}%；P95 延迟 {health.get('p95_latency_ms', '未知')} ms。\n"
            f"【主要证据】最相关的日志为“{log_cn}”。\n"
            f"【近期变更】最新部署版本为 {deploy_version}：{deploy_summary_cn}。\n"
            f"【可能原因】现有证据提示近期变更可能与异常有关，但时间相关性不能直接证明因果关系；需要进一步对照异常开始时间和监控指标。\n"
            f"【建议】{guidance_cn}\n"
            "【注意】这是基于本地模拟数据生成的演示结论，不应直接用于真实生产环境决策。"
        )
        return ModelDecision(final_answer=answer)
