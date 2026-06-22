from __future__ import annotations

from datetime import date

from aqsp.risk.unified_risk import (
    MarketSnapshot,
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
            max_position_pct=0.22,
            soft_stop_loss_pct=0.035,
            trailing_stop_activation_pct=0.045,
            max_holding_days=7,
            profit_take_threshold_pct=0.12,
            portfolio_daily_loss_pct=0.015,
            portfolio_weekly_loss_pct=0.045,
            portfolio_max_drawdown_pct=0.11,
            max_positions=6,
            max_single_position_pct=0.24,
            max_sector_concentration=0.33,
            max_correlation=0.62,
            min_cash_reserve=0.18,
            market_crash_threshold=-0.055,
            market_correction_threshold=-0.12,
            sector_panic_threshold=4,
            halt_trigger_count=2,
            auto_resume_days=3,
            avg_volume_ratio_min=0.8,
            north_flow_exit_threshold=-4_000_000_000,
        )
    )


def test_unified_stock_risk_config_from_thresholds() -> None:
    config = StockRiskConfig.from_thresholds(_thresholds())

    assert config.hard_stop_loss == 0.09
    assert config.soft_stop_loss == 0.035
    assert config.trailing_stop_activation == 0.045
    assert config.trailing_stop_distance == 0.025
    assert config.max_position_pct == 0.22
    assert config.max_holding_days == 7
    assert config.profit_take_threshold == 0.12


def test_unified_portfolio_risk_config_from_thresholds() -> None:
    config = PortfolioRiskConfig.from_thresholds(_thresholds())

    assert config.max_daily_loss_pct == 0.015
    assert config.max_weekly_loss_pct == 0.045
    assert config.max_drawdown_pct == 0.11
    assert config.max_positions == 6
    assert config.max_single_position_pct == 0.24
    assert config.max_sector_concentration == 0.33
    assert config.max_correlation == 0.62
    assert config.min_cash_reserve == 0.18


def test_unified_system_risk_config_from_thresholds() -> None:
    config = SystemRiskConfig.from_thresholds(_thresholds())

    assert config.market_crash_threshold == -0.055
    assert config.market_correction_threshold == -0.12
    assert config.panic_index_threshold == 0.33
    assert config.liquidity_min == 88_000_000
    assert config.sector_panic_threshold == 4
    assert config.halt_trigger_count == 2
    assert config.auto_resume_days == 3
    assert config.avg_volume_ratio_min == 0.8
    assert config.north_flow_exit_threshold == -4_000_000_000


def test_unified_risk_managers_load_threshold_defaults(monkeypatch) -> None:
    monkeypatch.setattr("aqsp.risk.unified_risk.load_thresholds", _thresholds)

    assert StockRiskManager().config.hard_stop_loss == 0.09
    assert PortfolioRiskManager().config.max_drawdown_pct == 0.11
    assert SystemRiskManager().config.panic_index_threshold == 0.33


def test_system_risk_corrupt_state_fails_closed(tmp_path) -> None:
    config = SystemRiskConfig.from_thresholds(_thresholds())
    manager = SystemRiskManager(config)
    manager.state_path = tmp_path / "system_risk.json"
    manager.state_path.write_text("{broken", encoding="utf-8")

    assert manager.is_halt_active()
    check = manager.check_market(
        MarketSnapshot(
            date=date(2026, 6, 22),
            hs300_change=0.0,
            hs300_change_5d=0.0,
            limit_up_count=0,
            limit_down_count=0,
            market_volatility=0.0,
            avg_volume_ratio=1.0,
            north_flow=0.0,
        )
    )

    assert check.halt_all_strategies
    assert check.duration_days >= config.halt_trigger_count
