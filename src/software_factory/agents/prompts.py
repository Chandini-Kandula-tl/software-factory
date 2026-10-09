"""Versioned prompt artifacts.

Prompts live as Markdown files under ``prompts/<agent>/``. Each file starts
with front matter declaring its version::

    ---
    version: 1.0.0
    ---

The recorded prompt version is ``<version>+<content-hash>`` so an edited
prompt is traceable in execution history even if nobody bumps the version.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from string import Template

from software_factory.domain.errors import ConfigurationError


@dataclass(frozen=True)
class Prompt:
    key: str
    version: str
    template: str

    def render(self, **values: str) -> str:
        return Template(self.template).safe_substitute(**values)


class PromptLibrary:
    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir

    def load(self, agent_dir: str, name: str) -> Prompt:
        path = self.prompts_dir / agent_dir / name
        if not path.exists():
            raise ConfigurationError(
                f"Prompt file not found: {path}",
                hint="Check factory.prompts_dir in the configuration.",
            )
        raw = path.read_text(encoding="utf-8")
        version, body = _split_front_matter(raw)
        digest = hashlib.sha256(body.encode()).hexdigest()[:8]
        return Prompt(key=f"{agent_dir}/{name}", version=f"{version}+{digest}", template=body)


def _split_front_matter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---\n"):
        return "unversioned", raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return "unversioned", raw
    header, body = raw[4:end], raw[end + 5 :]
    version = "unversioned"
    for line in header.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "version":
            version = value.strip()
    return version, body.lstrip("\n")
