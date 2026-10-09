"""Provider type registry.

Config names a provider ``type``; this module maps the type to an adapter
class. Built in: ``ollama`` (native Ollama API) and ``openai_compatible``
(any server speaking the Chat Completions format: LM Studio, llama.cpp,
vLLM, DeepSeek, OpenAI, ...). Adding another provider means registering one
more type here — no agent, workflow, or state code changes.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from software_factory.config.settings import ProviderConfig
from software_factory.domain.errors import ConfigurationError
from software_factory.llm.interfaces import ModelProvider

ProviderFactory = Callable[[str, ProviderConfig], ModelProvider]

_PROVIDER_TYPES: dict[str, ProviderFactory] = {}


def register_provider_type(type_name: str, factory: ProviderFactory) -> None:
    _PROVIDER_TYPES[type_name] = factory


def registered_provider_types() -> list[str]:
    _ensure_builtin_providers()
    return sorted(_PROVIDER_TYPES)


def create_provider(name: str, config: ProviderConfig) -> ModelProvider:
    _ensure_builtin_providers()
    factory = _PROVIDER_TYPES.get(config.type)
    if factory is None:
        raise ConfigurationError(
            f"Provider '{name}' has type '{config.type}', which is not implemented",
            hint=f"Implemented provider types: {', '.join(registered_provider_types())}. "
            "Cloud adapters are added by registering a new provider type.",
        )
    return factory(name, config)


def read_api_key(provider_name: str, config: ProviderConfig) -> str | None:
    """Read a provider's API key from the environment at call time.

    Keys are never stored in config, state, artifacts, history or logs.
    """
    if not config.api_key_env:
        return None
    value = os.environ.get(config.api_key_env)
    if not value:
        raise ConfigurationError(
            f"Provider '{provider_name}' requires environment variable {config.api_key_env}",
            hint=f"Export {config.api_key_env} (or add it to .env) before running the factory.",
        )
    return value


def _ensure_builtin_providers() -> None:
    if "ollama" not in _PROVIDER_TYPES:
        from software_factory.llm.providers.ollama import OllamaProvider

        register_provider_type("ollama", OllamaProvider)
    if "openai_compatible" not in _PROVIDER_TYPES:
        from software_factory.llm.providers.openai_compatible import OpenAICompatibleProvider

        register_provider_type("openai_compatible", OpenAICompatibleProvider)
