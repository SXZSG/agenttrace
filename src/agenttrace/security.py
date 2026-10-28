"""Small safety helpers for trace payloads."""

from __future__ import annotations

from typing import Any
import os
import re

_SENSITIVE_PARTS = ("api_key", "token", "secret", "password", "authorization", "credential")


def redact(value: Any, *, key: str = "", secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact values whose keys look credential-related."""

    if key not in {"prompt_tokens", "completion_tokens", "total_tokens"} and any(
        part in key.casefold() for part in _SENSITIVE_PARTS
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {redact(str(k), secrets=secrets): redact(v, key=str(k), secrets=secrets)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        for secret in (*secrets, os.getenv("OPENAI_API_KEY", "")):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        return re.sub(
            r"(?i)\b(api[_-]?key|password|secret|authorization|token)\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+",
            r"\1=[REDACTED]", value,
        )
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value), secrets=secrets)
