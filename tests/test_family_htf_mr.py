"""T3 方案 A（htf+mr 换 mom+tr）因子族替换的单元契约测试。

不改动默认 stable_plus；本文件只锁定方案 A 的接入契约：
1. _has_htf() 门控（weight=0 → False；weight>0 & enabled → True）
2. CompositeStrategy.calculate_score 正确聚合 htf（mom/tr 权重清零时仅 htf+mr 贡献）
3. cli._apply_walkforward_grid_variant(strategy_mix="htf_mr") 应用正确，
   且修 WF-MR1 潜伏 bug（mr/htf 因子被一并 enable，否则 _has_* 永不放行）
4. htf 接入不引入新的并列源：select_stocks 并列仍按 symbol 升序（rank 契约不变）

验证（重计算、双窗口）留到 runner，见 T3 设计稿 §4。
"""

from __future__ import annotations

from unittest import mock

import pandas as pd

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import Thresholds, load_thresholds


def _htf_only_composite(base: Thresholds) -> Thresholds:
    """composite 仅开 htf+mr（其余权重清零 + min_total_score=0），隔离聚合测试。"""
    t = base.with_overrides(
        "composite",
        {
            "momentum_weight": 0.0,
            "quality_weight": 0.0,
            "value_weight": 0.0,
            "volume_weight": 0.0,
            "mean_reversion_weight": 0.3,
            "triple_rise_weight": 0.0,
            "high_tight_flag_weight": 0.3,
            "min_total_score": 0.0,
        },
    )
    t = t.with_overrides("mean_reversion", {"enabled": True})
    t = t.with_overrides("high_tight_flag", {"enabled": True})
    return t


def test_has_htf_gating_default_off():
    """默认 composite.high_tight_flag_weight=0 → _has_htf() 为 False。"""
    strat = CompositeStrategy(StrategyConfig(name="composite"))
    assert strat._has_htf() is False


def test_has_htf_gating_enabled():
    """weight>0 且 enabled → _has_htf() 为 True。"""
    t = _htf_only_composite(Thresholds())
    strat = CompositeStrategy(StrategyConfig(name="composite"), thresholds=t)
    assert strat._has_htf() is True


def test_has_htf_gating_weight_positive_but_disabled():
    """weight>0 但 enabled=False → 仍 False（门控两条件缺一不放行）。"""
    t = _htf_only_composite(Thresholds()).with_overrides(
        "high_tight_flag", {"enabled": False}
    )
    strat = CompositeStrategy(StrategyConfig(name="composite"), thresholds=t)
    assert strat._has_htf() is False


def test_calculate_score_aggregates_htf_and_mr_only():
    """mom/tr 权重清零时，final 仅由 htf(1.0) 与 mr(0.5) 按 0.3/0.3 加权 → 0.75。"""
    t = _htf_only_composite(Thresholds())
    strat = CompositeStrategy(StrategyConfig(name="composite"), thresholds=t)
    # 隔离各因子打分（其它因子权重已清零，这里直接置空避免真实计算噪声）
    strat.momentum_strategy.calculate_score = lambda data: {}
    strat.volume_strategy.calculate_score = lambda data: {}
    strat.mean_reversion_strategy.calculate_score = lambda data: {"AAA": 0.5}
    strat.htf_strategy.calculate_score = lambda data: {"AAA": 1.0}

    data = {"AAA": pd.DataFrame({"close": [1.0]})}
    scores = strat.calculate_score(data, regime="unknown")
    assert "AAA" in scores
    assert abs(scores["AAA"] - 0.75) < 1e-9


def test_htf_disabled_does_not_call_candidate():
    """htf 门控关闭时，candidate.calculate_score 不应被调用（零开销且零污染）。"""
    strat = CompositeStrategy(StrategyConfig(name="composite"))
    assert strat._has_htf() is False
    # 其它因子恒被调用或按默认门控调用，置空避免真实计算对列式数据的要求
    for factor in (
        "momentum_strategy",
        "volume_strategy",
        "quality_strategy",
        "value_strategy",
        "mean_reversion_strategy",
        "triple_rise_strategy",
    ):
        setattr(getattr(strat, factor), "calculate_score", lambda data: {})
    with mock.patch.object(
        strat.htf_strategy, "calculate_score", return_value={}
    ) as spy:
        data = {"AAA": pd.DataFrame({"close": [1.0]})}
        strat.calculate_score(data)
        spy.assert_not_called()


def test_apply_htf_mr_variant_enables_factors_and_remaps_weights():
    """cli 变体应用：htf_mr 写入 0.3/0.3、清零 mom/tr、min_total_score=0.1，
    并修 WF-MR1 潜伏 bug —— 同步 enable mean_reversion 与 high_tight_flag 因子。
    """
    from aqsp.cli import (
        _WALKFORWARD_HTF_MR_GRID_VARIANTS,
        _apply_walkforward_grid_variant,
    )

    base = load_thresholds()  # mean_reversion.enabled 由 yaml 覆盖为 False
    assert base.mean_reversion.enabled is False  # 前置：确认触发 bug 的基线

    variant = _WALKFORWARD_HTF_MR_GRID_VARIANTS[0]  # WF-H01
    applied = _apply_walkforward_grid_variant(base, variant)
    c = applied.composite

    assert c.high_tight_flag_weight == 0.3
    assert c.mean_reversion_weight == 0.3
    assert c.momentum_weight == 0.0
    assert c.triple_rise_weight == 0.0
    assert c.min_total_score == 0.1
    # 潜伏 bug 修复：因子被 enable，否则 _has_mr/_has_htf 永不放行
    assert applied.mean_reversion.enabled is True
    assert applied.high_tight_flag.enabled is True


def test_htf_mr_grid_has_eight_variants():
    """htf_mr profile 含 8 个变体（满足 MIN_CSCV_VARIANTS=8，CSCV 可靠）。"""
    from aqsp.cli import _WALKFORWARD_HTF_MR_GRID_VARIANTS

    assert len(_WALKFORWARD_HTF_MR_GRID_VARIANTS) == 8
    for v in _WALKFORWARD_HTF_MR_GRID_VARIANTS:
        assert v.strategy_mix == "htf_mr"


def test_htf_selection_respects_symbol_tie_break():
    """htf 接入不引入新并列源：所有因子打分为并列时，top-n 仍按 symbol 升序取序。"""
    t = _htf_only_composite(Thresholds())
    strat = CompositeStrategy(StrategyConfig(name="composite"), thresholds=t)
    symbols = [f"sh60{i:04d}" for i in range(12)]

    strat.mean_reversion_strategy.calculate_score = lambda data: {s: 0.5 for s in symbols}
    strat.htf_strategy.calculate_score = lambda data: {s: 1.0 for s in symbols}
    strat.momentum_strategy.calculate_score = lambda data: {}

    data = {s: pd.DataFrame({"close": [1.0]}) for s in symbols}
    selected = strat.select_stocks(data, n=5)

    # 全部同分 → 并列按 symbol 升序（rank 契约不变）
    assert selected == symbols[:5]
