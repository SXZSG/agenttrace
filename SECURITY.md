# Security Policy

AgentTrace is an alpha local-development project. The current API does not provide authentication and should not be exposed to untrusted networks.

## Reporting a vulnerability

Please do not publish secrets, production logs, or exploit details in a public issue. Contact the repository owner privately through GitHub to coordinate a responsible disclosure.

## Current security boundaries

- Only registered tools can be called by the runtime.
- The runtime never executes arbitrary model-generated Python or shell commands.
- Tool arguments are validated against Pydantic schemas.
- Run traces redact common credential-like dictionary keys, but cannot reliably detect secrets embedded in arbitrary free text.
- The runtime's async timeout does not forcibly terminate a synchronous function that has already started in a worker thread.
- Authentication, multi-user isolation, and production-grade process isolation are not included in P0.

Do not use the current version to execute untrusted tools or operate real production infrastructure.
