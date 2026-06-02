from __future__ import annotations

import pytest

from aqsp.utils.llm_safe import (
    choose_siliconflow_model,
    get_siliconflow_free_models,
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
