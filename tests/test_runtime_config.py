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


def test_load_runtime_config_falls_back_when_numeric_env_invalid(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_LIMIT", "oops")
    monkeypatch.setenv("AQSP_MAX_UNIVERSE", "-1")
    monkeypatch.setenv("AQSP_MIN_AVG_AMOUNT", "nan-text")
    monkeypatch.setenv("AQSP_MAX_DATA_LAG_DAYS", "-5")

    config = load_runtime_config()

    assert config.limit == 10
    assert config.max_universe == 0
    assert config.min_avg_amount == 50000000
    assert config.max_data_lag_days == 3


def test_load_debate_runtime_config_falls_back_when_rounds_invalid(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_DEBATE_MAX_ROUNDS", "bad-value")

    config = load_debate_runtime_config()

    assert config.max_rounds == 2


def test_thresholds_load_mean_reversion_section() -> None:
    from aqsp.strategies.thresholds import load_thresholds

    thresholds = load_thresholds()

    assert thresholds.mean_reversion.enabled is True
    assert thresholds.mean_reversion.lookback_days == 20
    assert thresholds.mean_reversion.rsi_period == 14
    assert thresholds.mean_reversion.oversold_threshold == 30
    assert thresholds.mean_reversion.deviation_threshold == -0.05


def test_thresholds_load_triple_rise_section() -> None:
    from aqsp.strategies.thresholds import load_thresholds

    thresholds = load_thresholds()

    assert thresholds.triple_rise.enabled is True
    assert thresholds.triple_rise.lookback_days == 25
    assert thresholds.triple_rise.min_data_points == 20
    assert thresholds.triple_rise.volume_avg_window == 20
    assert thresholds.triple_rise.weights.triple_rise == 0.4
