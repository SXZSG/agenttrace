# Roadmap

已实现项目对应当前代码与离线验证结果；路线图中的生产能力仍保持待办状态。

## P0 — Local runtime and observability

- [x] Bounded tool-calling runtime
- [x] Allowlisted tool registry and typed argument validation
- [x] Tool timeouts and bounded retry for retryable timeouts
- [x] SQLite run and event persistence
- [x] Read-only replay view
- [x] Deterministic incident-triage scenario
- [x] CLI and FastAPI endpoints
- [x] Deterministic smoke benchmark
- [x] Automated tests for core success and failure paths

## P1 — Real model integration — 已完成

- [x] Add a provider adapter for a supported tool-calling API
- [x] Add configuration via environment variables and a documented `.env.example`
- [x] Add provider-level timeout, retry, and error classification
- [x] Add tests using a mocked HTTP transport; no live API calls in CI
- [x] Keep provider credentials out of logs and persisted traces

## P2 — Evaluation and reliability — 已完成（离线原型范围）

- [x] Add configurable fault injection and recovery-policy tests
- [x] Version benchmark cases and expected outcomes
- [x] Add model/configuration comparison reports
- [x] Add latency and token/cost metrics where available
- [x] Add regression thresholds and failure-case export

## P3 — Productization — 部分完成

- [x] Add a trace and replay web interface
- [x] Add Docker packaging and deployment documentation
- [ ] Add structured log export and OpenTelemetry integration
- [ ] Add API authentication and access controls before any shared deployment

## Non-goals for the initial release

- Executing arbitrary model-generated code or shell commands
- Connecting to real production infrastructure
- Claiming production readiness or generalized model-quality gains
- Replacing a full observability platform
