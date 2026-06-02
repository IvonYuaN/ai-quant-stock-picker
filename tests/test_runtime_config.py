from __future__ import annotations

from aqsp.config import load_debate_runtime_config, load_runtime_config


def test_load_runtime_config_reads_debate_flag(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")

    config = load_runtime_config()

    assert config.enable_debate is True


def test_load_debate_runtime_config_reads_language_roles_and_llm(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
    monkeypatch.setenv("AQSP_DEBATE_ENABLE_LLM", "true")
    monkeypatch.setenv("AQSP_DEBATE_MAX_ROUNDS", "3")
    monkeypatch.setenv("AQSP_DEBATE_LANGUAGE", "en-US")
    monkeypatch.setenv("AQSP_DEBATE_ROLES", "bull,risk_control,northbound")

    config = load_debate_runtime_config()

    assert config.enabled is True
    assert config.enable_llm is True
    assert config.max_rounds == 3
    assert config.language == "en-US"
    assert config.roles == ("bull", "risk_control", "northbound")
