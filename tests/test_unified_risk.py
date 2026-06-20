from __future__ import annotations

from aqsp.risk.unified_risk import (
    PortfolioRiskConfig,
    PortfolioRiskManager,
    StockRiskConfig,
    StockRiskManager,
    SystemRiskConfig,
    SystemRiskManager,
)
from aqsp.strategies.thresholds import RiskThresholds, Thresholds


def _thresholds() -> Thresholds:
    return Thresholds(
        risk=RiskThresholds(
            max_drawdown=0.18,
            volatility_limit=0.33,
            liquidity_threshold=88_000_000,
            single_stock_stop_pct=0.09,
            portfolio_stop_pct=0.16,
            warning_threshold_pct=0.04,
            trailing_stop_pct=0.025,
            enable_trailing_stop=True,
        )
    )


def test_unified_stock_risk_config_from_thresholds() -> None:
    config = StockRiskConfig.from_thresholds(_thresholds())

    assert config.hard_stop_loss == 0.09
    assert config.soft_stop_loss == 0.04
    assert config.trailing_stop_activation == 0.04
    assert config.trailing_stop_distance == 0.025


def test_unified_portfolio_risk_config_from_thresholds() -> None:
    config = PortfolioRiskConfig.from_thresholds(_thresholds())

    assert config.max_weekly_loss_pct == 0.04
    assert config.max_drawdown_pct == 0.16


def test_unified_system_risk_config_from_thresholds() -> None:
    config = SystemRiskConfig.from_thresholds(_thresholds())

    assert config.market_crash_threshold == -0.04
    assert config.panic_index_threshold == 0.33
    assert config.liquidity_min == SystemRiskConfig().liquidity_min


def test_unified_risk_managers_load_threshold_defaults(monkeypatch) -> None:
    monkeypatch.setattr("aqsp.risk.unified_risk.load_thresholds", _thresholds)

    assert StockRiskManager().config.hard_stop_loss == 0.09
    assert PortfolioRiskManager().config.max_drawdown_pct == 0.16
    assert SystemRiskManager().config.panic_index_threshold == 0.33
