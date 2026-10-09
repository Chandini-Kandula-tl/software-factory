"""Ollama adapter against a mocked HTTP server (no real model needed)."""

from __future__ import annotations

import httpx
import pytest
import respx

from software_factory.config.settings import ProviderConfig
from software_factory.llm.errors import LLMError, LLMErrorKind
from software_factory.llm.models import LLMRequest, Message, MessageRole
from software_factory.llm.providers.ollama import OllamaProvider

BASE = "http://ollama.test"


def _provider(timeout: float = 5.0) -> OllamaProvider:
    return OllamaProvider(
        "ollama", ProviderConfig(type="ollama", base_url=BASE, timeout_seconds=timeout)
    )


def _request(**kwargs) -> LLMRequest:
    return LLMRequest(messages=[Message(role=MessageRole.USER, content="hi")], **kwargs)


@respx.mock
def test_generate_maps_request_and_response():
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": '{"a": 1}'},
                "prompt_eval_count": 12,
                "eval_count": 5,
            },
        )
    )
    response = _provider().generate(
        _request(temperature=0.2, structured_output_schema={"type": "object"}),
        "qwen-test",
        {"num_ctx": 8192, "think": False},
    )
    body = route.calls.last.request.read()
    import json

    sent = json.loads(body)
    assert sent["model"] == "qwen-test"
    assert sent["stream"] is False
    assert sent["format"] == {"type": "object"}
    assert sent["options"] == {"num_ctx": 8192, "temperature": 0.2}
    assert sent["think"] is False
    assert response.content == '{"a": 1}'
    assert response.usage.input_tokens == 12 and response.usage.output_tokens == 5
    assert response.provider == "ollama" and response.model == "qwen-test"


@respx.mock
def test_server_unreachable_is_provider_unavailable():
    respx.post(f"{BASE}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMError) as exc:
        _provider().generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.PROVIDER_UNAVAILABLE
    assert "ollama serve" in exc.value.hint


@respx.mock
def test_missing_model_is_model_unavailable():
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(404, json={"error": "model 'm' not found"})
    )
    with pytest.raises(LLMError) as exc:
        _provider().generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.MODEL_UNAVAILABLE
    assert "ollama pull m" in exc.value.hint


@respx.mock
def test_timeout():
    respx.post(f"{BASE}/api/chat").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError) as exc:
        _provider().generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.TIMEOUT


@respx.mock
@pytest.mark.parametrize(
    ("status", "payload", "kind"),
    [
        (500, {"error": "boom"}, LLMErrorKind.PROVIDER_UNAVAILABLE),
        (429, {"error": "slow down"}, LLMErrorKind.RATE_LIMIT),
        (401, {"error": "nope"}, LLMErrorKind.AUTHENTICATION),
        (400, {"error": "input exceeds context length"}, LLMErrorKind.CONTEXT_LENGTH_EXCEEDED),
    ],
)
def test_http_errors_are_normalized(status, payload, kind):
    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(status, json=payload))
    with pytest.raises(LLMError) as exc:
        _provider().generate(_request(), "m", {})
    assert exc.value.kind is kind


@respx.mock
def test_malformed_response():
    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(LLMError) as exc:
        _provider().generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.MALFORMED_RESPONSE

    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(200, json={"done": True}))
    with pytest.raises(LLMError) as exc:
        _provider().generate(_request(), "m", {})
    assert exc.value.kind is LLMErrorKind.MALFORMED_RESPONSE


@respx.mock
def test_health_check_available_and_unavailable():
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen2.5:14b"}]})
    )
    health = _provider().health_check()
    assert health.healthy and health.available_models == ["qwen2.5:14b"]

    respx.get(f"{BASE}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    health = _provider().health_check()
    assert not health.healthy and "ollama serve" in health.detail


@respx.mock
def test_model_metadata():
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen2.5:14b",
                        "size": 9_000_000_000,
                        "details": {
                            "parameter_size": "14.8B",
                            "quantization_level": "Q4_K_M",
                            "family": "qwen2",
                        },
                    }
                ]
            },
        )
    )
    respx.post(f"{BASE}/api/show").mock(
        return_value=httpx.Response(200, json={"model_info": {"qwen2.context_length": 32768}})
    )
    meta = _provider().get_model_metadata("qwen2.5:14b")
    assert meta.available and meta.parameter_size == "14.8B"
    assert meta.quantization == "Q4_K_M" and meta.context_length == 32768
    assert not _provider().get_model_metadata("llama-missing").available
