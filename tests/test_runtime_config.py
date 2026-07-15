from __future__ import annotations

from aqsp.config import load_debate_runtime_config, load_runtime_config

RUNTIME_ROLE_NAMES = (
    "bull",
    "bear",
    "risk_control",
    "sector_leader",
    "cross_market",
    "policy_sensitive",
    "margin_trading",
    "northbound",
    "retail_mood",
)

INTRADAY_ROLE_NAMES = (
    "bull",
    "bear",
    "risk_control",
    "sector_leader",
    "cross_market",
    "policy_sensitive",
    "margin_trading",
    "northbound",
    "retail_mood",
)

BRIEFING_ROLE_NAMES = (
    "bull",
    "bear",
    "risk_control",
    "sector_leader",
    "cross_market",
    "policy_sensitive",
    "northbound",
)


def test_load_runtime_config_defaults_debate_to_enabled(monkeypatch) -> None:
    monkeypatch.delenv("AQSP_ENABLE_DEBATE", raising=False)

    config = load_runtime_config()

    assert config.enable_debate is True


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
    monkeypatch.setenv("AQSP_DEBATE_MAX_CANDIDATES", "5")
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
    assert config.max_candidates == 5
    assert config.language == "en-US"
    assert config.task_id == ""
    assert config.requested_roles == ("bull", "risk_control", "northbound")
    assert config.focus_roles == ()
    assert config.disabled_roles == ()
    assert config.roles == ("bull", "risk_control", "northbound")
    assert config.explicit_roles is True
    assert config.context_roles_locked is True
    assert config.role_runtime[0].role == "bull"
    assert config.role_runtime[0].enable_llm is True
    assert config.role_runtime[0].provider == "agnes"
    assert config.role_runtime[1].enable_llm is False
    assert config.role_runtime[2].model == "glm-4.7-flash"


def test_load_debate_runtime_config_global_llm_off_overrides_role_enable(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
    monkeypatch.setenv("AQSP_DEBATE_ENABLE_LLM", "false")
    monkeypatch.setenv("AQSP_DEBATE_ROLES", "bull,risk_control")
    monkeypatch.setenv("AQSP_DEBATE_ROLE_LLM", "bull:true")

    config = load_debate_runtime_config()

    assert config.enable_llm is False
    assert all(item.enable_llm is False for item in config.role_runtime)


def test_load_debate_runtime_config_defaults_to_enabled_when_env_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AQSP_ENABLE_DEBATE", raising=False)

    config = load_debate_runtime_config(task_id="intraday")

    assert config.enabled is True


def test_load_debate_runtime_config_defaults_to_task_preset_committee(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
    monkeypatch.delenv("AQSP_RUN_TASK_ID", raising=False)

    config = load_debate_runtime_config(task_id="intraday")

    assert config.task_id == "intraday"
    assert config.requested_roles == INTRADAY_ROLE_NAMES
    assert config.roles == INTRADAY_ROLE_NAMES
    assert config.context_roles_locked is False


def test_load_debate_runtime_config_uses_run_task_alias_when_task_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    config = load_debate_runtime_config()

    assert config.task_id == "closing_review"
    assert config.roles == BRIEFING_ROLE_NAMES
    assert config.context_roles_locked is False


def test_load_debate_runtime_config_focus_roles_only_reorder_full_committee(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
    monkeypatch.setenv("AQSP_DEBATE_FOCUS_ROLES", "cross_market,risk_control")

    config = load_debate_runtime_config(task_id="briefing")

    assert config.requested_roles == BRIEFING_ROLE_NAMES
    assert config.roles == (
        "cross_market",
        "risk_control",
        "bull",
        "bear",
        "sector_leader",
        "policy_sensitive",
        "northbound",
    )
    assert config.context_roles_locked is False


def test_load_debate_runtime_config_disabled_roles_are_the_only_default_cut(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AQSP_DEBATE_ROLES", raising=False)
    monkeypatch.setenv("AQSP_DEBATE_DISABLED_ROLES", "northbound")

    config = load_debate_runtime_config(task_id="intraday")

    assert config.roles == (
        "bull",
        "bear",
        "risk_control",
        "sector_leader",
        "cross_market",
        "policy_sensitive",
        "margin_trading",
        "retail_mood",
    )
    assert config.disabled_roles == ("northbound",)
    assert config.context_roles_locked is False


def test_load_debate_runtime_config_applies_focus_and_disabled_role_switches(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AQSP_DEBATE_ROLES",
        "bull,bear,cross_market,northbound",
    )
    monkeypatch.setenv(
        "AQSP_DEBATE_FOCUS_ROLES",
        "cross_market,bull,unknown",
    )
    monkeypatch.setenv("AQSP_DEBATE_DISABLED_ROLES", "bull")
    monkeypatch.setenv("AQSP_DEBATE_ROLE_PROVIDERS", "cross_market:agnes")

    config = load_debate_runtime_config()

    assert config.requested_roles == ("bull", "bear", "cross_market", "northbound")
    assert config.focus_roles == ("cross_market", "bull", "unknown")
    assert config.disabled_roles == ("bull",)
    assert config.roles == ("cross_market", "bear", "northbound")
    assert config.context_roles_locked is True
    assert len(config.role_runtime) == 3
    assert config.role_runtime[0].role == "cross_market"
    assert config.role_runtime[0].provider == "agnes"


def test_goal_switches_can_disable_debate_and_auto_evolution_at_runtime(
    monkeypatch, tmp_path
) -> None:
    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  multi_agent_advisory_layer:
    enabled: false
    purpose: disable debate
  auto_optimization_proposals:
    enabled: false
    purpose: disable auto evolution
  realtime_fallback_chain:
    enabled: false
    purpose: disable fallback
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
    monkeypatch.setenv("AQSP_ENABLE_AUTO_EVOLUTION", "true")
    monkeypatch.setenv("AQSP_DEBATE_ENABLE_LLM", "true")

    runtime_config = load_runtime_config()
    debate_config = load_debate_runtime_config(task_id="briefing")

    assert runtime_config.enable_debate is False
    assert runtime_config.enable_auto_evolution is False
    assert debate_config.enabled is False
    assert debate_config.enable_llm is False
    assert debate_config.roles == BRIEFING_ROLE_NAMES


def test_load_debate_runtime_config_keeps_base_roles_when_disabled_would_empty(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_DEBATE_ROLES", "bull,cross_market")
    monkeypatch.setenv("AQSP_DEBATE_DISABLED_ROLES", "bull,cross_market")

    config = load_debate_runtime_config()

    assert config.roles == ("bull", "cross_market")
    assert config.disabled_roles == ("bull", "cross_market")
    assert tuple(item.role for item in config.role_runtime) == ("bull", "cross_market")


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


def test_load_debate_runtime_config_falls_back_when_max_candidates_invalid(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_DEBATE_MAX_CANDIDATES", "0")

    config = load_debate_runtime_config()

    assert config.max_candidates == 5


def test_thresholds_load_mean_reversion_section() -> None:
    from aqsp.strategies.thresholds import load_thresholds

    thresholds = load_thresholds()

    assert thresholds.mean_reversion.enabled is False
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


def test_thresholds_ignore_unknown_yaml_fields(tmp_path) -> None:
    from aqsp.strategies.thresholds import load_thresholds

    path = tmp_path / "thresholds.yaml"
    path.write_text(
        """
version: "test"
momentum:
  lookback_days: 33
  extra_field: 1
  weights:
    momentum: 0.55
    nested_extra: 99
composite:
  momentum_weight: 0.66
  extra_field: 2
internet_strategy:
  rps_score: 21.0
  extra_field: 3
regime:
  strategy_weights:
    stable_bull:
      momentum: 1.25
      extra_field: 4
""".strip(),
        encoding="utf-8",
    )

    thresholds = load_thresholds(str(path))

    assert thresholds.version == "test"
    assert thresholds.momentum.lookback_days == 33
    assert thresholds.momentum.weights.momentum == 0.55
    assert thresholds.composite.momentum_weight == 0.66
    assert thresholds.internet_strategy.rps_score == 21.0
    assert thresholds.regime.strategy_weights["stable_bull"].momentum == 1.25
