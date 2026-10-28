# Phase 2 交付与验收记录

日期：2026-10-26。代码已在本地完成审查和离线验证；未提交或推送 GitHub。

## 修改文件清单

新增：

- `.env.example`：服务端配置示例，不含真实密钥。
- `src/agenttrace/llm.py`：OpenAI 兼容适配器、结构化报告及证据校验、环境配置工厂。
- `tests/test_llm.py`：46 个离线测试用例（参数化后计数）。
- `docs/llm.zh-CN.md`：安装、配置、架构、安全、错误、回放及限制。
- `docs/phase2-delivery.zh-CN.md`：本验收记录。

修改：

- `pyproject.toml`：声明 OpenAI SDK 依赖。
- `src/agenttrace/models.py`：增量模式/模型、用量、引用及参数解析错误字段。
- `src/agenttrace/providers.py`：Mock 模式元数据。
- `src/agenttrace/runtime.py`：有界决策循环、协议历史、时限、调用预算、事件和输出截断。
- `src/agenttrace/security.py`：已知密钥及常见文本凭据脱敏，保留数值 Token 统计。
- `src/agenttrace/store.py`：旧库增量迁移与落盘脱敏。
- `src/agenttrace/api.py`：模式工厂、健康提示、验证错误不回显输入。
- `src/agenttrace/cli.py`：CLI demo 使用服务端配置的模式。
- `src/agenttrace/static/index.html`：中文模式、模型阶段、失败、超时和截断提示。
- `README.md`：更新 Phase 2 入口与能力边界。
- `docs/architecture.md`：更新模型和事件说明。
- `docs/user-guide.zh-CN.md`：补充 LLM 配置入口。

`.gitignore` 已有 `.env` / `.env.*` 忽略规则，不需要修改。当前目录无 `.git`，因此无法验证仓库索引、已跟踪文件或远端 CI 状态。没有初始化项目 Git 仓库。

## 已验证结果

- 完整 pytest：58 passed（原有 12 项均保留通过，新增 46 项），1 warning。
- CI 同款 `coverage run -m pytest -q` + `coverage report --include="src/agenttrace/*" --fail-under=85`：总计 97%，达到 85% 门槛。
- `ruff check .`：通过。
- `python -m compileall -q src tests`：通过。
- `python -m agenttrace eval`：3/3，pass_rate=1.0，模式 deterministic_mock。
- 浏览器实际检查：Mock 任务完成，中文诊断与回放显示；LLM 缺配置时明确失败，工具步骤为 0，模式仍为 LLM；所查浏览器 error 日志为空。
- 模型协议通过模拟客户端和真实 SDK + httpx MockTransport 检查，没有真实模型请求和 API 费用。
- 老数据库迁移、并发历史隔离、回放不重新执行、提示注入越权工具拒绝、秘密脱敏均有回归覆盖。

现有警告：StarletteDeprecationWarning，TestClient 提示 httpx 使用方式弃用并建议 httpx2。未通过屏蔽警告、跳过或删除测试解决。

实际运行环境为 Windows/Python 3.11。已执行与现有 CI 相同的检查命令，未运行 GitHub Actions 或 Linux/Python 3.12 矩阵。

## 手动步骤与未实现功能

按 [中文配置说明](llm.zh-CN.md) 在本机设置 OPENAI_API_KEY、AGENTTRACE_MODEL 和 AGENTTRACE_MODE=llm，必要时设置 OPENAI_BASE_URL，重启服务后验证自己的模型服务。不要向聊天发送密钥。

所有工具后端仍为合成数据；未做真实供应商连通性验收、生产工具接入、跨模型能力评测、实时流式轨迹、成本计费或身份认证。事实引用校验不等于证明模型推测正确。同步线程超时不能强杀底层函数。
