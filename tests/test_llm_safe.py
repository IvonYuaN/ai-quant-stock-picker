from __future__ import annotations

import os

import pytest

from aqsp.utils.llm_safe import (
    LlmResult,
    choose_siliconflow_model,
    get_siliconflow_free_models,
    llm_call_or_fallback,
    _model_env_for_provider,
)


def test_choose_siliconflow_model_defaults_to_free_whitelist(monkeypatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("SILICONFLOW_FREE_ONLY", "true")

    choice = choose_siliconflow_model()

    assert choice.free_only is True
    assert choice.model in get_siliconflow_free_models()


def test_choose_siliconflow_model_rejects_pro_models_in_free_only(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "Pro/deepseek-ai/DeepSeek-V3")
    monkeypatch.setenv("SILICONFLOW_FREE_ONLY", "true")

    with pytest.raises(ValueError):
        choose_siliconflow_model()


def test_choose_siliconflow_model_accepts_known_free_model_from_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "THUDM/glm-4-9b-chat")
    monkeypatch.setenv("SILICONFLOW_FREE_ONLY", "true")

    choice = choose_siliconflow_model()

    assert choice.model == "THUDM/glm-4-9b-chat"
    assert choice.source == "env"


def test_choose_siliconflow_model_prefers_provider_specific_env_when_present(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "Qwen/Qwen2-1.5B-Instruct")
    monkeypatch.setenv("SILICONFLOW_MODEL", "THUDM/glm-4-9b-chat")
    monkeypatch.setenv("SILICONFLOW_FREE_ONLY", "true")

    choice = choose_siliconflow_model()

    assert choice.model == "THUDM/glm-4-9b-chat"
    assert choice.source == "env"


def test_model_env_for_provider_prefers_agnes_model_over_global(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "global-fallback-model")
    monkeypatch.setenv("AGNES_MODEL", "agnes-2.0-flash")

    assert _model_env_for_provider("agnes") == "agnes-2.0-flash"


def test_llm_call_or_fallback_uses_provider_fallback_on_rate_limit(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_client_factory() -> object:
        return object()

    def fake_invoke(client, prompt, model, timeout_s):
        calls.append((os.getenv("LLM_PROVIDER", ""), model))
        if len(calls) == 1:
            raise RuntimeError("429 rate limit")
        return "agnes ok", 0.0

    monkeypatch.setenv("ENABLE_LLM_BRIEFING", "true")
    monkeypatch.setenv("LLM_PROVIDER", "glm")
    monkeypatch.setenv("AGNES_MODEL", "agnes-2.0-flash")
    monkeypatch.setattr("aqsp.utils.llm_safe._invoke", fake_invoke)

    result = llm_call_or_fallback(
        "prompt",
        "fallback",
        enable_llm=True,
        _client_factory=fake_client_factory,
    )

    assert isinstance(result, LlmResult)
    assert result.degraded is False
    assert result.text == "agnes ok"
    assert result.model == "agnes-2.0-flash"
    assert calls == [("glm", None), ("agnes", "agnes-2.0-flash")]
    assert os.getenv("LLM_PROVIDER") == "glm"


def test_llm_call_or_fallback_degrades_when_provider_fallback_fails(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_client_factory() -> object:
        return object()

    def fake_invoke(client, prompt, model, timeout_s):
        provider = os.getenv("LLM_PROVIDER", "")
        calls.append(provider)
        if provider == "glm":
            raise RuntimeError("rate limit exceeded")
        raise RuntimeError("agnes unavailable")

    monkeypatch.setenv("ENABLE_LLM_BRIEFING", "true")
    monkeypatch.setenv("LLM_PROVIDER", "glm")
    monkeypatch.setattr("aqsp.utils.llm_safe._invoke", fake_invoke)

    result = llm_call_or_fallback(
        "prompt",
        "fallback",
        enable_llm=True,
        _client_factory=fake_client_factory,
    )

    assert result.degraded is True
    assert result.text == "fallback"
    assert "glm: RuntimeError: rate limit exceeded" in result.reason
    assert "agnes: RuntimeError: agnes unavailable" in result.reason
    assert calls == ["glm", "agnes"]
    assert os.getenv("LLM_PROVIDER") == "glm"
