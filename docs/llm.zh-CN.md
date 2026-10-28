# Phase 2：真实模型工具调用配置

## 安装和启动

需要 Python 3.11+。在项目目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:AGENTTRACE_MODE = "mock"
.\scripts\run_api_windows.ps1
```

打开 http://127.0.0.1:8000/。默认 Mock 不需要密钥且不请求模型服务。

真实模型模式在**启动服务的同一个 PowerShell 窗口**配置：

```powershell
$env:AGENTTRACE_MODE = "llm"
$env:AGENTTRACE_MODEL = "填写你的服务支持的模型标识"
# 可选：OpenAI 兼容服务的根地址，通常以 /v1 结尾
# $env:OPENAI_BASE_URL = "https://your-provider.example/v1"
# 通过隐藏输入设置密钥，避免将明文写入命令历史
$credential = Read-Host "输入服务端 API Key（请勿发到聊天中）" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $credential).Password
.\scripts\run_api_windows.ps1
```

切换模式或修改配置后重启服务。CLI 的 `python -m agenttrace demo` 同样遵循环境配置。`python -m agenttrace eval` 始终使用 Mock。

| 变量 | 用途 |
|---|---|
| `AGENTTRACE_MODE` | `mock`（默认）或 `llm`；其他值拒绝启动 |
| `OPENAI_API_KEY` | LLM 模式必需，仅服务端读取 |
| `AGENTTRACE_MODEL` | LLM 模式必需，服务支持的 Chat Completions 工具调用模型 |
| `OPENAI_BASE_URL` | 可选，不设置则使用 SDK 默认地址；由管理员配置，模型不能改变 |
| `AGENTTRACE_DB_PATH` | SQLite 路径，默认 `agenttrace.sqlite3` |

`.env.example` 是变量清单。Windows 启动脚本会加载项目根目录 `.env`，已有进程环境变量优先；直接运行 Python/CLI 不自动加载，可通过进程环境注入。`.gitignore` 已忽略 `.env` / `.env.*` 并仅放行 `.env.example`。不要在示例、任务文本或浏览器中填写真实密钥。

## 决策与执行架构

保留原有 `ModelProvider → AgentRuntime → ToolRegistry → SQLiteTraceStore` 链路。

1. 运行时创建独立的任务历史，记录运行 ID、模式、模型及预算。
2. LLM 适配器发送系统约束、用户任务、Pydantic 生成的工具定义和历史消息，使用 `tool_choice=auto`。
3. 模型自由选择工具、参数和调用顺序。注册表拒绝未知工具；Pydantic 拒绝非法参数。JSON 损坏作为失败观察返回模型。
4. 运行时执行工具，记录结果，将 assistant tool_calls 和对应 tool_call_id 结果一起交回模型。每轮不共享其他任务的历史。
5. 模型返回包含 facts、hypotheses、unknowns、recommendations 的 JSON 报告。事实引用必须是成功工具调用的 ID，quote 必须匹配实际返回的原文。伪造引用或格式错误使任务失败。
6. 中文报告分别呈现事实原文、未证实推测、未知信息和需人工核查的建议。没有事实时不得输出推测，必须明确证据不足。

协议参考：[OpenAI Function calling 官方文档](https://developers.openai.com/api/docs/guides/function-calling)。兼容服务必须支持 Chat Completions `tools`、`tool_choice`、`max_completion_tokens` 及标准工具结果消息；不兼容时会明确失败。

## 安全边界与预算

API 默认预算（在 `AgentRuntime` 构造参数中配置）：

| 限制 | 默认 |
|---|---|
| Agent 模型决策轮数 | 10 |
| 工具调用总尝试次数（包含重试） | 8 |
| 单工具等待 | 2 秒，由注册表配置 |
| 单工具超时重试 | 最多 1 次，仍受总调用预算约束 |
| 单次模型请求等待 | 30 秒 |
| 整体执行等待 | 120 秒 |
| 工具输出预览 | 最多 6000 字符，另带截断标识 |
| 模型输出预算 | 2048 completion tokens |

API 仅允许四个预先注册的模拟查询工具：健康指标、错误日志、部署、操作手册。模型不能注册工具，也不能执行 Python、Shell、任意文件读写或自选网络请求。系统消息规定工具结果中的指令属于不可信数据；硬性执行边界由注册表和参数模型实施，不能靠提示词扩大权限。

工具空输出、超时、未知名称和错误参数均有结构化错误。模型拒绝、限流、连接错误、API 错误、空响应、协议错误、配置缺失均终止并保存失败原因，**绝不回退 Mock**。模型 API 不自动重试（SDK `max_retries=0`），避免隐藏费用和调用；用户可排障后重新提交。

轨迹不保存私有推理过程、原始 API 异常正文或请求头。保存决策轮数、工具调用序号、参数、状态、耗时、重试、终止原因及可取得的 Token 用量。脱敏覆盖敏感键名、当前配置密钥、常见凭据赋值及 `sk-` 格式；任意未知秘密无法完整识别，仍不要在任务中放入机密信息。

## 回放与历史兼容

SQLite 对旧 runs 表仅增补 mode/model 字段，旧记录默认 Mock，不删除原有事件。Trace/Replay 结构保留，新字段为增量扩展。

所有 GET 运行、轨迹、列表和回放接口只读取存储。回放不调用 Provider 或工具，即使当前配置缺少密钥也能阅读历史。自动化测试将模型和工具替换为“一调用就失败”的桩验证这一点。

## 工作台与自动评测

中文工作台显示当前模式、模型和“真实模型 + 模拟工具后端”，历史详情显示该次运行自身模式。运行期间有加载提示，完成后显示完整事件；当前采用同步请求，不提供流式实时轨迹。失败记录展示明确中文原因，空日志和过长工具结果有单独提示。

“自动评测”及 `/evaluations/demo` 保留三个确定性 Mock 场景，不受 LLM 模式影响，不消耗真实 API 费用，不代表真实模型准确率。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m coverage run -m pytest -q
.\.venv\Scripts\python.exe -m coverage report --include="src/agenttrace/*" --fail-under=85
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m agenttrace eval
```

LLM 测试使用模拟模型响应与 httpx MockTransport，也验证真实 SDK 的请求序列化；无需密钥、无外部模型请求和实际 API 费用。

## 尚有限制

- 所有工具仍为本地合成后端，未接入真实监控、日志平台或生产操作。
- 引用校验保证事实片段来自工具，不证明推测的因果关系或建议正确；模型质量仍需人工审核和独立评测。
- 未实施跨模型基准、成本计费估算、实时流式事件和生产身份认证。
- 同步工具在工作线程中执行，超时停止等待，不能强杀线程；当前工具只执行短小本地查询。生产隔离需要子进程或容器。
- 只在本机监听使用；SQLite 适合本地实验，不提供多租户隔离。
- 实际模型连通性、账号权限和服务方兼容性需用户在本机配置后验证；本次离线验收不替代真实服务验收。

## DeepSeek 本机配置

在 `.env` 中使用 `AGENTTRACE_MODE=llm`、`OPENAI_BASE_URL=https://api.deepseek.com`、`AGENTTRACE_MODEL=deepseek-flash`、`AGENTTRACE_DISABLE_THINKING=1` 和服务端密钥。启动脚本自动加载。当前历史协议使用非思考工具调用，不记录或回传 reasoning_content；DeepSeek 官方接口使用 max_tokens 限制输出。
