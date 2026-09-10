"""选股内核 v2 因子族单测：打分范围、方向性、点-in-time 窗口独立性、别名一致性。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.family_v2 import (
    HighTightFlagStrategy,
    LowVolatilityStrategy,
    PullbackContinuationStrategy,
)


def _mk_df(
    closes: list[float],
    volumes: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    n = len(closes)
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float) if highs is not None else c * 1.005
    low = np.asarray(lows, dtype=float) if lows is not None else c * 0.995
    v = (
        np.asarray(volumes, dtype=float)
        if volumes is not None
        else np.full(n, 2_000_000.0)
    )
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n).strftime("%Y-%m-%d"),
            "open": c,
            "high": h,
            "low": low,
            "close": c,
            "volume": v,
        }
    )


def _flag_series() -> pd.DataFrame:
    """强势上涨(10→17) 后最后 10 日极窄缩量整理。"""
    rise = list(np.linspace(10.0, 17.0, 30))
    tight = list(np.linspace(16.8, 17.0, 10))
    closes = [10.0] * 20 + rise + tight
    n = len(closes)
    vols = [2_000_000.0] * n
    vols[-1] = 300_000.0  # 末日极度缩量
    highs = [x * 1.0 for x in closes]
    lows = [x * 1.0 for x in closes]
    return _mk_df(closes, volumes=vols, highs=highs, lows=lows)


@pytest.mark.parametrize(
    "cls",
    [HighTightFlagStrategy, LowVolatilityStrategy, PullbackContinuationStrategy],
)
def test_scores_within_unit_range(cls) -> None:
    strat = cls(StrategyConfig(name=cls.name))
    data = {
        "600001": _flag_series(),
        "600002": _mk_df(list(np.linspace(20.0, 12.0, 60))),  # 下跌
        "600003": _mk_df([15.0] * 60),  # 横盘
    }
    scores = strat.calculate_score(data)
    assert set(scores) == set(data)
    for v in scores.values():
        if v == v:  # 非 NaN
            assert 0.0 <= v <= 1.0


def test_high_tight_flag_detects_flag_formation() -> None:
    strat = HighTightFlagStrategy(StrategyConfig(name="high_tight_flag"))
    flag = strat._calculate_single_score(_flag_series())
    choppy = strat._calculate_single_score(
        _mk_df(list(15.0 + np.random.default_rng(0).normal(0, 0.5, 60)))
    )
    falling = strat._calculate_single_score(_mk_df(list(np.linspace(20.0, 10.0, 60))))
    assert flag > 0.8
    assert flag > choppy
    assert flag > falling


def test_low_volatility_ranks_low_vol_highest() -> None:
    strat = LowVolatilityStrategy(StrategyConfig(name="low_volatility"))
    rng = np.random.default_rng(42)
    base = np.linspace(10.0, 12.0, 61)
    calm = base + rng.normal(0, 0.01, 61)
    mid = base + rng.normal(0, 0.05, 61)
    wild = base + rng.normal(0, 0.20, 61)
    data = {
        "600001": _mk_df(list(calm)),
        "600002": _mk_df(list(mid)),
        "600003": _mk_df(list(wild)),
    }
    s = strat.calculate_score(data)
    assert s["600001"] > s["600002"] > s["600003"]
    assert all(0.0 <= v <= 1.0 for v in s.values())


def test_pullback_continuation_prefers_shallow_intact_pullback() -> None:
    strat = PullbackContinuationStrategy(StrategyConfig(name="pullback_continuation"))
    # 强势 10→17，随后浅回调 5%(16.15)，结构完好、缩量
    closes = (
        [10.0] * 20
        + list(np.linspace(10.0, 17.0, 30))
        + list(np.linspace(17.0, 16.15, 10))
    )
    vols = [2_000_000.0] * len(closes)
    vols[-1] = 800_000.0
    good = _mk_df(closes, volumes=vols)
    # 同样的强势但深跌破坏结构
    bad_closes = (
        [10.0] * 20
        + list(np.linspace(10.0, 17.0, 30))
        + list(np.linspace(17.0, 12.0, 10))
    )
    bad = _mk_df(bad_closes)
    assert strat._calculate_single_score(good) > strat._calculate_single_score(bad)


def test_high_tight_flag_point_in_time_window_only() -> None:
    """打分只依赖窗口内数据：截断历史不影响同一截面日的分数。"""
    strat = HighTightFlagStrategy(StrategyConfig(name="high_tight_flag"))
    full = _flag_series()
    assert len(full) >= 41
    truncated = full.iloc[-41:].reset_index(drop=True)
    assert strat._calculate_single_score(full) == pytest.approx(
        strat._calculate_single_score(truncated)
    )


def test_low_volatility_point_in_time_window_only() -> None:
    strat = LowVolatilityStrategy(StrategyConfig(name="low_volatility"))
    rng = np.random.default_rng(7)
    full = _mk_df(list(10.0 + rng.normal(0, 0.02, 200)))
    truncated = full.iloc[-21:].reset_index(drop=True)
    data_full = {"600001": full, "600002": _mk_df(list(np.linspace(10, 11, 200)))}
    data_trunc = {"600001": truncated, "600002": _mk_df(list(np.linspace(10, 11, 200)))}
    s_full = strat.calculate_score(data_full)["600001"]
    s_trunc = strat.calculate_score(data_trunc)["600001"]
    assert s_full == pytest.approx(s_trunc)


def test_pullback_point_in_time_window_only() -> None:
    strat = PullbackContinuationStrategy(StrategyConfig(name="pullback_continuation"))
    closes = (
        [10.0] * 20
        + list(np.linspace(10.0, 17.0, 30))
        + list(np.linspace(17.0, 16.15, 10))
    )
    full = _mk_df(closes)
    truncated = full.iloc[-41:].reset_index(drop=True)
    assert strat._calculate_single_score(full) == pytest.approx(
        strat._calculate_single_score(truncated)
    )


def test_short_series_returns_zero() -> None:
    for cls in (
        HighTightFlagStrategy,
        PullbackContinuationStrategy,
    ):
        strat = cls(StrategyConfig(name=cls.name))
        assert strat._calculate_single_score(_mk_df([10.0, 10.1, 10.2])) == 0.0


def test_candidate_alias_is_single_source_of_truth() -> None:
    from aqsp.strategies.candidates import HighTightFlagCandidate

    assert HighTightFlagCandidate is HighTightFlagStrategy
