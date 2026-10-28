# Architecture

## Components

- `AgentRuntime` owns the bounded decision loop. It asks a provider for a decision, dispatches only registered tool calls, appends observations to the provider history, and records lifecycle events.
- `ModelProvider` is a protocol. `MockModelProvider` is a deterministic scripted implementation. `OpenAIModelProvider` implements stateless Chat Completions tool calling; the runtime owns each run’s assistant/tool history. See [Phase 2 details](llm.zh-CN.md).
- `ToolRegistry` maps explicitly registered names to argument models and handlers. It validates arguments before dispatch and returns structured errors for unknown tools, invalid arguments, timeouts, and handler exceptions.
- `SQLiteTraceStore` persists run metadata and ordered JSON events. The store is intended for local demos and small experiments, not high-concurrency production workloads.
- `build_replay` builds a read-only view of the recorded tool calls and outcomes. It does not rerun the model or tool handlers.
- `run_smoke_evaluation` runs fixed synthetic scenarios and compares observed tool sequences and answer terms with declared expectations.

## Execution lifecycle

1. Create a run record and emit `run_started`.
2. Ask the configured provider for a `ModelDecision`.
3. Record the decision, including requested tool names and arguments.
4. Validate the tool name and arguments against the allowlisted registry.
5. Execute the handler within its configured wait timeout; record success or a structured failure.
6. Retry a timeout at most `max_tool_retries` times when configured. Other errors are surfaced to the provider as observations.
7. Continue until a final answer is returned, the step budget is exhausted, or the runtime fails.
8. Persist the final run status and return the run record.

## Trace event types

`run_started`, `model_started`, `model_request_finished`, `model_failed`, `model_decision`, `tool_started`, `tool_completed`, `tool_failed`, `tool_retry`, `budget_exhausted`, `run_completed`, and `run_failed`.

Events have a monotonically increasing per-run sequence number and a UTC timestamp. Credential-like dictionary keys are redacted before persistence. Configured API keys and common credential assignments are also redacted in free text before persistence and model context. Arbitrary unknown secrets cannot be reliably detected; do not put credentials in tasks.

## Failure semantics

- Unknown tool: rejected; no handler is invoked.
- Invalid arguments: rejected before handler invocation.
- Timeout: returned as `tool_timeout` and marked retryable; runtime retries within its configured retry budget.
- Handler exception: returned as `tool_exception`; the stored error omits exception messages and stack traces to reduce accidental disclosure.
- Step budget: run terminates with a failed status and a `budget_exhausted` event.
- Provider exception: run terminates with a `run_failed` event. The event records the exception type, not arbitrary exception text.

## Important limitations

- The default provider is scripted and deterministic. Optional llm mode calls the configured model, but all incident tools still return synthetic data.
- Replay reconstructs recorded tool outcomes; it does not reproduce nondeterministic model generation.
- SQLite is a local persistence choice, not a production observability backend.
- `asyncio.wait_for` bounds the runtime wait. A synchronous handler running in a worker thread may continue after timeout. Production execution of untrusted tools should use process/container isolation.
- The API has no authentication or multi-tenant access controls and should remain local during this milestone.
