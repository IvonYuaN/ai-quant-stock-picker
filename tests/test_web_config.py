from __future__ import annotations

from aqsp.strategies.thresholds import load_thresholds
from aqsp.web.config import RISK_CONFIG


def test_web_risk_config_uses_thresholds_yaml() -> None:
    thresholds = load_thresholds()

    assert RISK_CONFIG["single_stock_stop"] == -float(
        thresholds.risk.single_stock_stop_pct
    )
    assert RISK_CONFIG["portfolio_stop"] == -float(
        thresholds.risk.portfolio_stop_pct
    )
    assert RISK_CONFIG["warning_threshold"] == -float(
        thresholds.risk.warning_threshold_pct
    )
    assert RISK_CONFIG["enable_trailing"] is thresholds.risk.enable_trailing_stop
    assert RISK_CONFIG["trailing_stop_pct"] == float(
        thresholds.risk.trailing_stop_pct
    )
