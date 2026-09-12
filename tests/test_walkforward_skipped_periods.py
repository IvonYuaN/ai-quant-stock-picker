"""C4：熊市过滤「整期跳过」与「真实 0 收益期」的口径分离。

``regime_is_bear_filter`` 命中时整期空仓，``trades=[]`` → 指标全零，与「策略
出手了但恰好 0 收益」在产物里**同形**，违反「skipped ≠ 健康」。旧口径把跳过期
当成一次正常的 0 收益投资期，导致 ``win_rate`` 被系统性低估（跳过期被计成
「非胜」），``|Sharpe|`` 被 0 收益期拉向 0（双向，非单向高估）。

本文件锁死两件事：主指标口径**逐位不变**（兼容性铁律），以及新增的跳期标注与
活跃期口径确实生效。实证读数见 ``outputs/大方向_口径自解释_2026-09-12.md``。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from aqsp.backtest.walk_forward import (
    BacktestResult,
    WalkForwardTester,
    _compute_aggregate_metrics,
    _compute_backtest_metrics,
)

PERIODS_PER_YEAR = 252.0 / 30.0


def _ohlcv(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [100000.0] * len(dates),
        }
    )


class _PriceRankStrategy:
    name = "price_rank"

    def select_stocks(self, data, n: int = 2, regime: str = "unknown") -> list[str]:
        del regime
        return sorted(data, key=lambda s: float(data[s].iloc[-1]["close"]), reverse=True)[
            :n
        ]


def _frames(days: int = 60) -> dict[str, pd.DataFrame]:
    dates = [(date(2024, 1, 1) + timedelta(days=d)).isoformat() for d in range(days)]
    return {
        f"{i:06d}": _ohlcv(dates, [10.0 + i + d * 0.01 for d in range(days)])
        for i in range(1, 5)
    }


def _tester(**overrides) -> WalkForwardTester:
    kwargs = dict(
        train_period_days=10,
        test_period_days=5,
        purge_days=2,
        horizon_days=2,
        top_n=2,
        n_variants=3,
        benchmark_symbol="000300",
    )
    kwargs.update(overrides)
    return WalkForwardTester(_PriceRankStrategy(), **kwargs)


def _aggregate(returns, skipped=None):
    return _compute_aggregate_metrics(
        list(returns),
        period="Overall",
        periods_per_year=PERIODS_PER_YEAR,
        trades=1,
        not_executable=0,
        period_skipped=skipped,
    )


def test_single_period_marks_skipped_zero_period() -> None:
    result = _compute_backtest_metrics([], "p", skipped=True)

    assert (result.skipped, result.skipped_periods, result.active_periods) == (
        True,
        1,
        0,
    )
    assert result.total_return == 0.0


def test_backtest_result_positional_defaults_keep_legacy_shape() -> None:
    """新字段全部带默认值：既有的 9 位置参数构造仍然合法。"""
    result = BacktestResult("p0", 0.05, 0.05, 0.02, 1.5, 0.6, 1.2, 10, 0)

    assert (result.skipped, result.skipped_periods, result.active_periods) == (
        False,
        0,
        0,
    )
    assert (result.sharpe_ratio_active, result.win_rate_active) == (0.0, 0.0)


def test_aggregate_main_metrics_identical_with_and_without_flags() -> None:
    """同一序列下带/不带跳期标记，主指标必须逐位相同（与历史读数可比）。"""
    series = [2.0, -1.0, 3.0, -0.5, 1.5, 0.0, 0.0]
    legacy = _aggregate(series)
    flagged = _aggregate(series, [False] * 5 + [True, True])

    for field in (
        "total_return",
        "annual_return",
        "max_drawdown",
        "sharpe_ratio",
        "win_rate",
        "profit_factor",
    ):
        assert getattr(flagged, field) == getattr(legacy, field), field
    # 差异只体现在新增的活跃期口径上。
    assert flagged.sharpe_ratio_active != legacy.sharpe_ratio_active
    assert flagged.win_rate_active >= flagged.win_rate


def test_aggregate_without_flags_reports_fully_active() -> None:
    result = _aggregate([1.0, -1.0, 2.0])

    assert (result.skipped, result.skipped_periods, result.active_periods) == (
        False,
        0,
        3,
    )
    assert result.sharpe_ratio_active == result.sharpe_ratio
    assert result.win_rate_active == result.win_rate


def test_aggregate_win_rate_active_excludes_skipped_periods() -> None:
    """实测方向：跳过期被旧口径计成「非胜」→ 旧 win_rate 被低估。"""
    result = _aggregate([2.0, 3.0, 0.0, 0.0], [False, False, True, True])

    assert result.win_rate == pytest.approx(0.5)
    assert result.win_rate_active == pytest.approx(1.0)
    assert (result.skipped_periods, result.active_periods, result.skipped) == (2, 2, True)


def test_aggregate_sharpe_active_attenuates_zero_periods() -> None:
    """0 收益期把 |Sharpe| 拉向 0（双向，不是单向高估）。"""
    active = list(np.random.default_rng(20260912).normal(0.015, 0.04, 24))
    result = _aggregate(active + [0.0] * 16, [False] * 24 + [True] * 16)

    assert abs(result.sharpe_ratio_active) >= abs(result.sharpe_ratio)
    assert result.active_periods == 24


def test_aggregate_all_periods_skipped_has_no_active_view() -> None:
    """全期跳过 = 策略根本没跑，活跃口径归零，不得回落主序列假装有读数。"""
    result = _aggregate([0.0, 0.0, 0.0], [True, True, True])

    assert (result.skipped_periods, result.active_periods) == (3, 0)
    assert result.sharpe_ratio_active == 0.0


def test_aggregate_rejects_misaligned_flags() -> None:
    with pytest.raises(ValueError, match="must align"):
        _aggregate([1.0, 2.0], [False])


def test_run_marks_bear_filter_periods_as_skipped(monkeypatch) -> None:
    tester = _tester()
    monkeypatch.setattr(
        tester, "_resolve_market_regime", lambda data, as_of=None: ("bear", True)
    )

    result = tester.run(_frames(), start_date="2024-01-01", end_date="2024-03-01")

    assert result.periods and all(p.skipped for p in result.periods)
    assert result.overall.skipped_periods == len(result.periods)
    assert result.overall.active_periods == 0


def test_run_does_not_mark_empty_selection_as_skipped(monkeypatch) -> None:
    """「选出 0 只」是真实 0 交易期，绝不能被标成熊市过滤跳过。"""
    tester = _tester()
    monkeypatch.setattr(
        tester,
        "_resolve_market_regime",
        lambda data, as_of=None: ("stable_bull", False),
    )
    monkeypatch.setattr(tester, "_select_stocks", lambda data, regime=None: [])

    result = tester.run(_frames(), start_date="2024-01-01", end_date="2024-03-01")

    assert result.periods and all(p.trades == 0 for p in result.periods)
    assert not any(p.skipped for p in result.periods)
    assert result.overall.skipped_periods == 0
