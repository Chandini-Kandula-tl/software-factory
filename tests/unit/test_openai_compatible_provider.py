"""OpenAI-compatible adapter against a mocked server (no network, no real key)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from software_factory.config.settings import ProviderConfig
from software_factory.domain.errors import ConfigurationError
from software_factory.llm.errors import LLMError, LLMErrorKind
from software_factory.llm.models import LLMRequest, Message, MessageRole
from software_factory.llm.providers.base import create_provider

BASE = "http://llm.test/v1"
KEY = "sk-test-0123456789abcdefghij"


def _provider(monkeypatch, **options):
    monkeypatch.setenv("TEST_LLM_KEY", KEY)
    return create_provider(
        "cloud",
        ProviderConfig(
            type="openai_compatible",
            base_url=BASE,
            api_key_env="TEST_LLM_KEY",
            timeout_seconds=5,
            options=options,
        ),
    )


def _request(**kwargs) -> LLMRequest:
    return LLMRequest(messages=[Message(role=MessageRole.USER, content="hi")], **kwargs)


def _ok(content: str = '{"a": 1}') -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4},
        },
    )


@respx.mock
def test_generate_sends_key_schema_and_maps_response(monkeypatch):
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=_ok())
    response = _provider(monkeypatch).generate(
        _request(temperature=0.1, structured_output_schema={"type": "object"}), "m-1", {}
    )
    sent = route.calls.last.request
    body = json.loads(sent.read())
    assert sent.headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "m-1" and body["temperature"] == 0.1
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert response.content == '{"a": 1}'
    assert response.usage.input_tokens == 11 and response.usage.output_tokens == 4


@respx.mock
@pytest.mark.parametrize(
    ("mode", "expected"), [("json_object", {"type": "json_object"}), ("prompt", None)]
)
def test_structured_output_modes(monkeypatch, mode, expected):
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=_ok())
    _provider(monkeypatch, structured_output=mode).generate(
        _request(structured_output_schema={"type": "object"}), "m", {}
    )
    assert json.loads(route.calls.last.request.read()).get("response_format") == expected


def test_configuration_errors(monkeypatch):
    with pytest.raises(ConfigurationError, match="base_url"):
        create_provider("x", ProviderConfig(type="openai_compatible"))
    with pytest.raises(ConfigurationError, match="structured_output"):
        _provider(monkeypatch, structured_output="xml")


@respx.mock
def test_missing_api_key_is_a_clear_error(monkeypatch):
    provider = _provider(monkeypatch)
    monkeypatch.delenv("TEST_LLM_KEY")
    with pytest.raises(ConfigurationError, match="TEST_LLM_KEY"):
        provider.generate(_request(), "m", {})


@respx.mock
@pytest.mark.parametrize(
    ("status", "payload", "kind"),
    [
        (401, {"error": {"message": "bad key"}}, LLMErrorKind.AUTHENTICATION),
        (
            404,
            {"error": {"message": "nope", "code": "model_not_found"}},
            LLMErrorKind.MODEL_UNAVAILABLE,
        ),
        (429, {"error": {"message": "slow down"}}, LLMErrorKind.RATE_LIMIT),
        (
            400,
            {"error": {"message": "maximum context length exceeded"}},
            LLMErrorKind.CONTEXT_LENGTH_EXCEEDED,
        ),
        (503, {"error": "overloaded"}, LLMErrorKind.PROVIDER_UNAVAILABLE),
    ],
)
def test_http_errors_are_normalized(monkeypatch, status, payload, kind):
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(status, json=payload))
    with pytest.raises(LLMError) as exc:
        _provider(monkeypatch).generate(_request(), "m", {})
    assert exc.value.kind is kind
    assert KEY not in str(exc.value)


@respx.mock
def test_transport_and_malformed_errors(monkeypatch):
    provider = _provider(monkeypatch)
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMError) as exc:
        provider.generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.PROVIDER_UNAVAILABLE
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError) as exc:
        provider.generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.TIMEOUT
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    with pytest.raises(LLMError) as exc:
        provider.generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.MALFORMED_RESPONSE


@respx.mock
def test_health_and_metadata(monkeypatch):
    provider = _provider(monkeypatch)
    respx.get(f"{BASE}/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "m-1", "owned_by": "lab", "context_length": 65536}]}
        )
    )
    health = provider.health_check()
    assert health.healthy and health.available_models == ["m-1"]
    meta = provider.get_model_metadata("m-1")
    assert meta.available and meta.context_length == 65536
    assert not provider.get_model_metadata("other").available

    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(401, json={}))
    assert "authentication" in provider.health_check().detail
