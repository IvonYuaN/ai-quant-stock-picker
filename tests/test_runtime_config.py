from __future__ import annotations

from aqsp.config import load_debate_runtime_config, load_runtime_config


def test_load_runtime_config_reads_debate_flag(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
    monkeypatch.setenv("AQSP_NOTIFY", "true")
    monkeypatch.setenv("AQSP_NOTIFY_MODE", "fanout")
    monkeypatch.setenv("AQSP_ENABLE_AUTO_EVOLUTION", "true")
    monkeypatch.setenv("AQSP_WALKFORWARD_SYMBOLS", "000915,000921")
    monkeypatch.setenv("AQSP_RESEARCH_ENGINE", "akquant")

    config = load_runtime_config()

    assert config.enable_debate is True
    assert config.notify is True
    assert config.notify_mode == "fanout"
    assert config.enable_auto_evolution is True
    assert config.walkforward_symbols == ("000915", "000921")
    assert config.research_engine == "akquant"


def test_load_debate_runtime_config_reads_language_roles_and_llm(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
    monkeypatch.setenv("AQSP_DEBATE_ENABLE_LLM", "true")
    monkeypatch.setenv("AQSP_DEBATE_MAX_ROUNDS", "3")
    monkeypatch.setenv("AQSP_DEBATE_LANGUAGE", "en-US")
    monkeypatch.setenv("AQSP_DEBATE_ROLES", "bull,risk_control,northbound")
    monkeypatch.setenv("AQSP_DEBATE_ROLE_LLM", "bull:true,risk_control:false")
    monkeypatch.setenv("AQSP_DEBATE_ROLE_PROVIDERS", "bull:agnes,northbound:glm")
    monkeypatch.setenv(
        "AQSP_DEBATE_ROLE_MODELS",
        "bull:agnes-2.0-flash,northbound:glm-4.7-flash",
    )

    config = load_debate_runtime_config()

    assert config.enabled is True
    assert config.enable_llm is True
    assert config.max_rounds == 3
    assert config.language == "en-US"
    assert config.roles == ("bull", "risk_control", "northbound")
    assert config.role_runtime[0].role == "bull"
    assert config.role_runtime[0].enable_llm is True
    assert config.role_runtime[0].provider == "agnes"
    assert config.role_runtime[1].enable_llm is False
    assert config.role_runtime[2].model == "glm-4.7-flash"
