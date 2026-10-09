"""Deterministic secret detection for generated source code.

Stricter than log redaction: in source code only *literal* values count, so
``password = request.form["password"]`` is fine while
``password = "hunter2hunter2"`` is a finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("sk- style API key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    (
        "hard-coded credential",
        re.compile(
            # Any identifier containing a credential word, e.g. ADMIN_PASSWORD = "…".
            r"(?i)\b\w*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"\w*\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"
        ),
    ),
]

SENSITIVE_FILE_NAMES = frozenset({".env", "id_rsa", "id_ed25519", "credentials.json"})
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})
_TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__", "spec", "fixtures"})
_PLACEHOLDER = re.compile(r"(?i)(example|changeme|placeholder|your[_-]|xxx|dummy|<.*>)")


@dataclass(frozen=True)
class SecretMatch:
    file: str
    line: int
    kind: str
    in_test_code: bool


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ""
    return (
        bool(set(parts[:-1]) & _TEST_DIR_NAMES)
        or name.startswith("test_")
        or (".test." in name or ".spec." in name)
    )


def is_sensitive_file(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.name in SENSITIVE_FILE_NAMES or pure.suffix in SENSITIVE_SUFFIXES


def scan_text(path: str, text: str) -> list[SecretMatch]:
    matches = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in _SECRET_PATTERNS:
            found = pattern.search(line)
            if found and not _PLACEHOLDER.search(found.group(0)):
                matches.append(SecretMatch(path, number, kind, is_test_path(path)))
    return matches
