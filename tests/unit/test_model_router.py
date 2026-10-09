"""Model router: profile resolution, capability validation, retry, fallback, repair."""

from __future__ import annotations

from collections import deque

import pytest
from pydantic import BaseModel

from software_factory.config.settings import LimitsConfig, config_from_dict
from software_factory.llm.errors import LLMError, LLMErrorKind
from software_factory.llm.models import LLMRequest, LLMResponse, Message, MessageRole
from software_factory.llm.registry import ModelRegistry
from software_factory.llm.router import ModelRouter


class FakeProvider:
    def __init__(self, name: str, outputs: list) -> None:
        self.name = name
        self.outputs = deque(outputs)
        self.models_called: list[str] = []

    def generate(self, request, model, options):
        self.models_called.append(model)
        item = self.outputs.popleft()
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            content=item, provider=self.name, model=model, model_profile=request.model_profile
        )

    def health_check(self): ...
    def get_model_metadata(self, model): ...


class Answer(BaseModel):
    value: int


def _router(outputs: list, fallback_caps=("reasoning", "structured_output"), **limits):
    config = config_from_dict(
        {
            "providers": {"p": {"type": "fake"}},
            "models": {
                "big": {
                    "provider": "p",
                    "model": "big-model",
                    "capabilities": ["reasoning", "structured_output"],
                },
                "small": {
                    "provider": "p",
                    "model": "small-model",
                    "capabilities": list(fallback_caps),
                },
                "coder": {"provider": "p", "model": "coder-model", "capabilities": ["coding"]},
            },
            "profiles": {
                "reasoning": {
                    "preferred_model": "big",
                    "fallback_models": ["small"],
                    "required_capabilities": ["reasoning"],
                },
                "coding": {"preferred_model": "coder", "required_capabilities": ["coding"]},
            },
        }
    )
    provider = FakeProvider("p", outputs)
    registry = ModelRegistry(config, providers={"p": provider})
    router = ModelRouter(
        registry, LimitsConfig(retry_backoff_seconds=0, **limits), sleep=lambda _: None
    )
    return router, provider


def _req(profile="reasoning"):
    return LLMRequest(model_profile=profile, messages=[Message(role=MessageRole.USER, content="q")])


def test_retry_then_success():
    router, provider = _router([LLMError(LLMErrorKind.TIMEOUT, "slow"), "ok"])
    response = router.generate(_req())
    assert response.content == "ok"
    assert [c.status for c in response.calls] == ["failed", "success"]
    assert provider.models_called == ["big-model", "big-model"]


def test_retry_limit_then_capability_compatible_fallback():
    errors = [LLMError(LLMErrorKind.TIMEOUT, "slow")] * 3
    router, provider = _router([*errors, "from small"], max_agent_retries=2)
    response = router.generate(_req())
    assert response.model == "small-model"
    assert provider.models_called == ["big-model"] * 3 + ["small-model"]


def test_unreachable_provider_is_not_retried_through_its_other_models():
    errors = [LLMError(LLMErrorKind.PROVIDER_UNAVAILABLE, "down")] * 3
    router, provider = _router(errors, max_agent_retries=2)
    with pytest.raises(LLMError) as exc:
        router.generate(_req())
    assert exc.value.kind is LLMErrorKind.PROVIDER_UNAVAILABLE
    assert provider.models_called == ["big-model"] * 3  # small-model shares the provider


def test_non_retryable_error_is_not_retried_but_falls_back():
    router, provider = _router([LLMError(LLMErrorKind.MODEL_UNAVAILABLE, "missing"), "ok"])
    assert router.generate(_req()).model == "small-model"
    assert provider.models_called == ["big-model", "small-model"]


def test_fallback_skips_models_missing_capabilities():
    router, provider = _router(
        [LLMError(LLMErrorKind.MODEL_UNAVAILABLE, "missing")], fallback_caps=("fast",)
    )
    with pytest.raises(LLMError) as exc:
        router.generate(_req())
    assert exc.value.kind is LLMErrorKind.MODEL_UNAVAILABLE
    assert provider.models_called == ["big-model"]
    assert len(exc.value.calls) == 1


def test_unsupported_capability():
    router, provider = _router([])
    with pytest.raises(LLMError) as exc:
        router.generate_structured(_req("coding"), Answer)
    assert exc.value.kind is LLMErrorKind.UNSUPPORTED_CAPABILITY
    assert "structured_output" in str(exc.value)
    assert provider.models_called == []
    assert router.supports("coding") and not router.supports("vision")


def test_structured_output_parsed_and_fenced_json_accepted():
    router, _ = _router(['```json\n{"value": 7}\n```'])
    response = router.generate_structured(_req(), Answer)
    assert response.parsed == Answer(value=7)


def test_structured_output_repair_sends_validation_error_back():
    router, provider = _router(['{"value": "seven"}', '{"value": 7}'])
    response = router.generate_structured(_req(), Answer)
    assert response.parsed.value == 7
    assert [c.status for c in response.calls] == ["invalid_output", "success"]


def test_structured_output_gives_up_after_repairs_then_falls_back():
    router, provider = _router(
        ["nope", "nope", "nope", '{"value": 1}'], max_structured_output_repairs=2
    )
    response = router.generate_structured(_req(), Answer)
    assert response.model == "small-model"
    assert provider.models_called == ["big-model"] * 3 + ["small-model"]


def test_unknown_profile_is_configuration_error():
    from software_factory.domain.errors import ConfigurationError

    router, _ = _router([])
    with pytest.raises(ConfigurationError):
        router.generate(_req("vision"))
