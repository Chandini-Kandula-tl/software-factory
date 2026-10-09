"""Model registry: configured providers, models, capabilities and profiles."""

from __future__ import annotations

from software_factory.config.settings import FactoryConfig, ModelConfig, ProfileConfig
from software_factory.domain.errors import ConfigurationError
from software_factory.llm.interfaces import ModelProvider
from software_factory.llm.models import Capability
from software_factory.llm.providers.base import create_provider


class ModelRegistry:
    def __init__(
        self, config: FactoryConfig, providers: dict[str, ModelProvider] | None = None
    ) -> None:
        self._config = config
        # Pre-built providers (used by tests); others are created lazily so a
        # disabled or unused cloud provider never needs its API key.
        self._providers: dict[str, ModelProvider] = dict(providers or {})

    @property
    def model_keys(self) -> list[str]:
        return list(self._config.models)

    @property
    def profile_names(self) -> list[str]:
        return list(self._config.profiles)

    def model(self, key: str) -> ModelConfig:
        try:
            return self._config.models[key]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown model '{key}'") from exc

    def profile(self, name: str) -> ProfileConfig:
        try:
            return self._config.profiles[name]
        except KeyError as exc:
            raise ConfigurationError(
                f"Unknown model profile '{name}'",
                hint=f"Configured profiles: {', '.join(self._config.profiles) or 'none'}",
            ) from exc

    def is_enabled(self, model_key: str) -> bool:
        provider_name = self.model(model_key).provider
        return self._config.providers[provider_name].enabled

    def provider_for(self, model_key: str) -> ModelProvider:
        provider_name = self.model(model_key).provider
        if provider_name not in self._providers:
            provider_config = self._config.providers[provider_name]
            if not provider_config.enabled:
                raise ConfigurationError(f"Provider '{provider_name}' is disabled")
            self._providers[provider_name] = create_provider(provider_name, provider_config)
        return self._providers[provider_name]

    def provider_names(self) -> list[str]:
        return [name for name, cfg in self._config.providers.items() if cfg.enabled]

    def provider(self, name: str) -> ModelProvider:
        if name not in self._providers:
            self._providers[name] = create_provider(name, self._config.providers[name])
        return self._providers[name]

    def capabilities(self, model_key: str) -> set[str]:
        return set(self.model(model_key).capabilities)

    def candidates(
        self, profile_name: str, required: set[Capability | str] | None = None
    ) -> list[str]:
        """Models for a profile in preference order, filtered by capability.

        Fallbacks are only ever capability-compatible: a model that lacks any
        required capability is never selected.
        """
        profile = self.profile(profile_name)
        needed = {str(c) for c in (required or set())} | set(profile.required_capabilities)
        ordered = [profile.preferred_model, *profile.fallback_models]
        return [
            key
            for key in dict.fromkeys(ordered)
            if self.is_enabled(key) and needed <= self.capabilities(key)
        ]

    def missing_capabilities(self, profile_name: str, required: set[Capability | str]) -> set[str]:
        profile = self.profile(profile_name)
        needed = {str(c) for c in required} | set(profile.required_capabilities)
        return needed - self.capabilities(profile.preferred_model)
