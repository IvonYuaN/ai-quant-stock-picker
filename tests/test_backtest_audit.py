from __future__ import annotations

from aqsp.backtest.audit import audit_backtest_assumptions


def test_audit_backtest_assumptions_passes_when_core_guards_present() -> None:
    result = audit_backtest_assumptions(
        {
            "uses_raw_prices": True,
            "uses_point_in_time_data": True,
            "train_test_separated": True,
            "has_purge_window": True,
            "includes_transaction_costs": True,
            "includes_slippage": True,
            "excludes_not_executable": True,
            "cost_model": "fee+slippage from thresholds",
            "data_cutoff": "2026-07-06",
            "signal_cutoff": "2026-07-07",
        }
    )

    assert result.ok is True
    assert result.blockers == ()


def test_audit_backtest_assumptions_fails_when_good_backtest_rules_missing() -> None:
    result = audit_backtest_assumptions(
        {
            "uses_raw_prices": False,
            "uses_point_in_time_data": True,
            "train_test_separated": True,
            "has_purge_window": False,
            "includes_transaction_costs": True,
            "includes_slippage": False,
            "excludes_not_executable": True,
        }
    )

    assert result.ok is False
    assert any("uses_raw_prices" in item for item in result.blockers)
    assert any("has_purge_window" in item for item in result.blockers)
    assert any("includes_slippage" in item for item in result.blockers)
    assert any("cost_model" in item for item in result.blockers)
