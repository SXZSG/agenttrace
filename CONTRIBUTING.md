# Contributing

Thanks for considering a contribution to AgentTrace.

## Development setup

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest -q
ruff check .
```

## Pull request expectations

- Keep changes focused and explain the behavior being changed.
- Add or update tests for runtime behavior, failure handling, trace events, or evaluation logic.
- Do not add real API calls to CI.
- Do not include secrets, private datasets, or production logs.
- Keep benchmark claims tied to documented, reproducible runs.
- Update docs when public API behavior or event schemas change.

## Commit style

Prefer concise imperative messages, for example `Add replay event pairing` or `Handle provider timeout`.
