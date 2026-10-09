"""Secret redaction applied to logs, tool output and persisted errors."""

from __future__ import annotations

import os
import re

REDACTED = "***REDACTED***"

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),  # OpenAI/DeepSeek style keys
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),  # GitHub tokens
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API keys
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)((?:api[_-]?key|secret|password|passwd|token)\s*[=:]\s*)['\"]?[^\s'\"]{6,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _secret_env_values() -> list[str]:
    return [
        value
        for name, value in os.environ.items()
        if any(hint in name.upper() for hint in SECRET_ENV_HINTS) and len(value) >= 8
    ]


def redact(text: str) -> str:
    """Remove known secret shapes and any secret-looking environment values."""
    if not text:
        return text
    for value in _secret_env_values():
        text = text.replace(value, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
        else:
            text = pattern.sub(REDACTED, text)
    return text
