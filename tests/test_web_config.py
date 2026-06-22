from __future__ import annotations

from aqsp.strategies.thresholds import (
    RegimeStrategyWeights,
    RegimeThresholds,
    RiskThresholds,
    Thresholds,
    load_thresholds,
)
from aqsp.web.config import RISK_CONFIG, get_risk_config, get_strategy_display_config


def test_web_risk_config_uses_thresholds_yaml() -> None:
    thresholds = load_thresholds()

    config = get_risk_config(thresholds=thresholds)
    assert config["single_stock_stop"] == -float(thresholds.risk.single_stock_stop_pct)
    assert config["portfolio_stop"] == -float(thresholds.risk.portfolio_stop_pct)
    assert config["warning_threshold"] == -float(thresholds.risk.warning_threshold_pct)
    assert config["enable_trailing"] is thresholds.risk.enable_trailing_stop
    assert config["trailing_stop_pct"] == float(thresholds.risk.trailing_stop_pct)


def test_web_risk_config_can_be_reloaded_from_threshold_snapshot() -> None:
    config = get_risk_config(
        thresholds=Thresholds(risk=RiskThresholds(single_stock_stop_pct=0.11))
    )

    assert config["single_stock_stop"] == -0.11
    assert RISK_CONFIG["single_stock_stop"] != -0.11


def test_strategy_display_config_uses_regime_threshold_weights() -> None:
    thresholds = Thresholds(
        regime=RegimeThresholds(
            strategy_weights={
                "stable_bull": RegimeStrategyWeights(
                    momentum=2.0,
                    quality=1.0,
                    value=0.0,
                    volume=1.0,
                    mean_reversion=0.0,
                    triple_rise=0.0,
                )
            }
        )
    )

    config = get_strategy_display_config(thresholds=thresholds)

    assert set(config) == {"momentum", "quality", "volume"}
    assert config["momentum"]["weight"] == 0.5
    assert config["quality"]["weight"] == 0.25
