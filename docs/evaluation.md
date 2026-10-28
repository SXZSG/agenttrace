# Evaluation protocol

## Current benchmark: `agenttrace-incident-smoke-v1`

The current benchmark contains three synthetic incident tasks: checkout connection-pool saturation, payments health inspection, and inventory catalog timeouts. Each case declares expected tool order and required evidence terms.

### Reported metrics

- **Pass rate:** cases that completed and satisfied all declared checks divided by total cases.
- **Tool sequence match rate:** cases whose successful tool sequence exactly matches the declared sequence.
- **Required-term match rate:** cases whose final answer contains all case-specific required terms.

### What the metrics mean

These checks validate that the runtime, registered tools, trace collection, and synthetic scenario are wired together correctly. Since the included provider is scripted, a 100% pass rate is expected for a healthy P0 build and **must not be described as real-model accuracy, agent reliability, or a benchmark result against other systems**.

### Reproducibility

Run:

```bash
agenttrace eval
```

The CLI emits a JSON report and writes each run's events to the configured SQLite store. Set `AGENTTRACE_DB_PATH` to a fresh path when you want an isolated run.

## Planned evaluation work

The next milestones should add:

1. A versioned task set with explicit expected tool calls, outcomes, and evidence sources.
2. Fault-injection cases for invalid tool arguments, transient errors, timeouts, empty results, and step-budget exhaustion.
3. Repeated runs for each real model configuration, with confidence intervals where sample sizes justify them.
4. Separate measurements for task completion, tool-selection correctness, argument validity, recovery success, latency, and token/cost usage where provider telemetry is available.
5. Regression comparisons that flag statistically or operationally meaningful degradations without tuning on the held-out evaluation set.

Do not publish improvement claims until they are supported by recorded experiments and a documented evaluation protocol.

## Phase 3-R1 failure benchmark

`agenttrace failure-benchmark` runs the versioned `failure-benchmark-v1` suite offline. The suite has 20 scenarios, including CF-01 through CF-04. R1.1 uses `evidence-rules-v4`; its manifest includes scenario definitions, rule version, and a grader hash. R0 and R1 records are frozen and are not overwritten.

`evidence_quote_match_rate` is the fraction of report facts whose `evidence_ref` exists in the actual delivered successful tool observations and whose quote is a substring of that result. It is a mechanical citation check only. It does not measure whether hypotheses, recommendations, causal claims, or the final diagnosis are correct. `semantic_diagnostic_support_rate` is therefore `not_evaluated`/null unless independent structured truth labels are added.

Each scenario declares `allowed_outcomes`. Completion and recovery require verified evidence and a valid report; safe termination is separate and never increases task completion. A configured fault is tracked per tool rule and call ordinal as all, partial, or none. Unreached rules are not recovery evidence. Tool statistics split injected failures from unexpected failures.

The full benchmark report records numerators, denominators, raw trace references, fault rule coverage, and the exact report/trace hashes. Metrics are exploratory for this small synthetic suite and do not represent real-model accuracy.

R1.1 additionally permits safe termination when all required evidence is present but an independently detected health/log conflict is explicitly acknowledged with both real call IDs and no hypotheses. A conflict claim without a real conflict, with fake references, or with a deterministic hypothesis remains incorrect.
