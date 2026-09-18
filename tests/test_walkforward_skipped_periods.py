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


# ---------------------------------------------------------------------------
# 后续 PR：活跃期 DSR（``deflated_sharpe_active``）。
# 目的不是"修" DSR，而是**证明**跳过期对 DSR 近似不敏感，防止把「有跳过期」
# 误读成「门禁被放松」。
# ---------------------------------------------------------------------------


def test_deflated_sharpe_active_is_none_without_skipped_periods() -> None:
    """无跳过期 → ``None``（未计算），调用方能与合法的 0.0 区分。"""
    tester = _tester()
    view = _aggregate([1.0, -0.5, 2.0])

    assert view.skipped_periods == 0
    assert tester._deflated_sharpe_active(view, PERIODS_PER_YEAR) is None


def test_deflated_sharpe_active_computed_when_some_periods_skipped() -> None:
    """有跳过期且活跃期 >1 → 用活跃期数重算，返回浮点值。"""
    tester = _tester()
    view = _aggregate([1.0, -0.5, 2.0, 0.0, 0.0], [False, False, False, True, True])

    assert (view.skipped_periods, view.active_periods) == (2, 3)
    value = tester._deflated_sharpe_active(view, PERIODS_PER_YEAR)

    assert value is not None
    assert isinstance(value, float)


def test_deflated_sharpe_active_is_none_when_no_active_period() -> None:
    """全期跳过 → 没有活跃期，不得拿主序列假装算得出活跃 DSR。"""
    tester = _tester()
    view = _aggregate([0.0, 0.0], [True, True])

    assert view.active_periods == 0
    assert tester._deflated_sharpe_active(view, PERIODS_PER_YEAR) is None


def test_deflated_sharpe_active_tracks_active_view_not_main_series() -> None:
    """活跃口径 DSR 必须建立在 ``sharpe_ratio_active`` 上，而非被 0 收益期稀释的主序列。"""
    tester = _tester()
    # 主序列被 0 收益期稀释，活跃序列更极端 → 两者 DSR 必然不同。
    view = _aggregate(
        [0.03, -0.01, 0.04, -0.02, 0.0, 0.0, 0.0, 0.0],
        [False, False, False, False, True, True, True, True],
    )
    assert view.sharpe_ratio_active != view.sharpe_ratio

    active_dsr = tester._deflated_sharpe_active(view, PERIODS_PER_YEAR)
    main_dsr = tester._calculate_deflated_sharpe(
        view.sharpe_ratio,
        tester.n_variants,
        len([1, 1, 1, 1, 0, 0, 0, 0]),  # type: ignore[arg-type]
        sharpe_is_annualized=True,
        periods_per_year=PERIODS_PER_YEAR,
    )

    assert active_dsr is not None
    assert active_dsr != main_dsr


def test_assemble_walkforward_result_aligns_skipped_periods_and_dsr() -> None:
    """akquant 路径：跳过期必须落进 ``periods``（T 对齐），并产出活跃期 DSR。

    ⚠️ 已知残留差异（有意保留，不属本 PR）：akquant 的 ``overall`` 由**交易**聚合，
    builtin 的 ``overall`` 由**期收益**聚合 —— 故 ``result.overall.skipped_periods``
    在两引擎间仍不同（此处恒为 0）。跳期计数以 ``result.periods`` 为准；活跃期指标
    走独立的 ``active_view``。改动 ``overall`` 口径会动摇历史读数，需单独评估。
    """
    from aqsp.research_engine import _assemble_walkforward_result

    tester = _tester()
    periods = [
        _compute_backtest_metrics([0.01], "p0"),
        _compute_backtest_metrics([-0.02], "p1"),
        _compute_backtest_metrics([], "p2", skipped=True),
    ]

    result = _assemble_walkforward_result(
        tester, periods, [], 3, periods_per_year=PERIODS_PER_YEAR
    )

    # T 对齐：跳过期显式落进 periods，不再被静默丢弃。
    assert len(result.periods) == 3
    assert [p.skipped for p in result.periods] == [False, False, True]
    # 活跃期 DSR 落值。
    assert result.deflated_sharpe_active is not None
    # 残留差异被显式钉住：跳期计数不在 overall 上（akquant overall 由交易聚合）。
    assert result.overall.skipped_periods == 0


def test_assemble_walkforward_result_no_skip_has_no_active_dsr() -> None:
    from aqsp.research_engine import _assemble_walkforward_result

    tester = _tester()
    periods = [
        _compute_backtest_metrics([0.01], "p0"),
        _compute_backtest_metrics([-0.02], "p1"),
    ]

    result = _assemble_walkforward_result(
        tester, periods, [], 3, periods_per_year=PERIODS_PER_YEAR
    )

    assert result.overall.skipped_periods == 0
    assert result.deflated_sharpe_active is None
