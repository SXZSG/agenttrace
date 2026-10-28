# AgentTrace

AgentTrace 是一个面向 tool-calling Agent 的本地可靠性与可观测工作台：追踪执行过程、定位工具失败、回放历史结果，并用版本化故障场景验证改动。

## 快速开始

默认 Mock 模式不需要 API Key：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\Start_AgentTrace.cmd
```

浏览器打开 `http://127.0.0.1:8001/`。也可以运行 `.\scripts\run_api_windows.ps1`，或在 Linux/macOS 使用 `sh scripts/run_api.sh`。

工作台提供总览、任务工作台、执行 Trace、可靠性评测、批次对比和使用说明。工具后端是合成 incident 数据；回放只读取历史事件，不会重新调用模型或工具。

## Mock 与真实模型

- Mock：确定性、本地、无需密钥，适合开发和 CI。
- LLM：设置 `AGENTTRACE_MODE=llm`、`OPENAI_API_KEY`、`AGENTTRACE_MODEL`，可选 `OPENAI_BASE_URL`。密钥只保存在服务端环境变量中。
- 两种模式都使用受约束的注册工具；不会执行模型生成的 Python、Shell 或未授权网络操作。

真实模型和工具服务可能产生费用。CI 和默认 Docker 配置永远使用 Mock；本项目的离线 Benchmark 不代表真实模型准确率。

## 可靠性评测

```powershell
.\.venv\Scripts\python.exe -m agenttrace failure-benchmark
.\.venv\Scripts\python.exe -m agenttrace failure-benchmark --scenario CF-04-B --strict
```

`--strict` 是 CI 验收模式；场景未完成、评测器报错或规则未通过时返回非零退出码。

评测包含 20 个版本化场景、故障注入、证据缺失、冲突证据和 CF-01 至 CF-04 反事实对照。`evidence_quote_match_rate` 只验证引用原文是否来自实际工具结果；自由文本诊断语义没有独立真值时显示 `not_evaluated`。完成、安全终止、恢复和错误严格分开。

## Docker

```bash
docker compose up --build
```

默认只绑定本机 `127.0.0.1:8001`，运行 Mock，SQLite 数据写入 Docker volume。镜像以非 root 用户运行，不包含 `.env` 或本地数据库。

## 开发与限制

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q src tests
```

当前定位是本地 Agent 工程与可靠性评测原型，不提供认证、多租户或生产级观测后端。Mock benchmark 只验证预定义流程、故障处理和规则约束，不能据此宣称真实大模型准确率、语义诊断正确率或生产可靠性。SQLite 适合本地运行和小规模实验。请阅读 [英文 README](README.md)、[架构说明](docs/architecture.md)、[评测协议](docs/evaluation.md) 和 [安全策略](SECURITY.md)。
