# Changelog

## Unreleased — Phase 4-R1 发布前质量修复

- 加固 Windows 启停脚本：记录 PID、启动时间、命令行和端口，并在停止或健康检查失败前校验进程身份。
- 修复历史评测批次 API 的只读展示摘要和空分母显示；比较成功后清除旧错误提示。
- 增加 `failure-benchmark --strict`，让离线 CI smoke benchmark 在评测失败时返回非零退出码。
- 更新发布说明、路线图和本地原型范围声明。

## Phase 4 — 产品工作台和工程包装

- Added the Chinese workbench overview, trace/replay views, reliability and batch comparison pages.
- Added Windows startup helpers, Docker configuration, cross-platform CI, and local-first documentation.

## Phase 3-R1.1 — 规则修订

- Corrected conflict-aware safe termination and kept semantic diagnostic support explicitly unevaluated without independent truth labels.

## Phase 3 — 故障注入和可靠性评测

- Added reproducible fault injection, bounded recovery, versioned failure scenarios, counterfactual comparisons, and append-only evaluation batches.

## Phase 2 — 真实模型适配

- Added an OpenAI-compatible tool-calling provider with explicit configuration errors, server-side secrets, bounded execution, traces, and offline mocked-client tests.

## 0.2.0 - Chinese local workbench

- Added a Chinese browser workspace at `/` so users do not need to interact with Swagger or raw JSON.
- Added recent-run listing and history detail views.
- Added localized execution timeline, evidence summaries, replay table, and evaluation dashboard.
- Localized deterministic demo diagnosis output to Chinese.
- Added Chinese usage documentation and browser smoke tests.

## 0.1.0 — Initial alpha

- Add bounded agent runtime and allowlisted tool registry.
- Add structured SQLite traces and read-only replay views.
- Add deterministic synthetic incident-triage tools.
- Add CLI, FastAPI endpoints, and deterministic smoke evaluation.
- Add automated tests and GitHub Actions CI configuration.
