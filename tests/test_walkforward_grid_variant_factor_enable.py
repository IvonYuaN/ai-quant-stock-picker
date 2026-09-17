"""网格变体的「声明因子族」必须真的生效（防 WF-V01/WF-MR1 那类幽灵变体回归）。

背景（2026-09-17 实测）：``_apply_walkforward_grid_variant`` 此前只改权重、**不 enable** 因子，
而 ``thresholds.yaml`` 里 ``volume`` / ``mean_reversion`` 默认 ``enabled=False``，于是
``CompositeStrategy._has_volume()`` / ``_has_mr()`` 恒为 False：

- ``WF-V01``（声称 volume）实际只用 mom+tr，**选出名单与 WF-001 逐位相同**；
- ``WF-MR1``（声称 mean_reversion）实际只用 mom+tr，**与 WF-B03 逐位相同**。

后果：``stable_plus``（生产默认 N=8）声称的「含因子族多样性」不成立，
且 ``MIN_CSCV_VARIANTS=8`` 这道 fail-closed 守卫被一个**重复列**凑数通过。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aqsp.cli import (
    _WALKFORWARD_STABLE_GRID_VARIANTS,
    _WALKFORWARD_STABLE_PLUS_GRID_VARIANTS,
    _WALKFORWARD_VALIDATED_GRID_VARIANTS,
    WalkForwardGridVariant,
    _apply_walkforward_grid_variant,
)
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import load_thresholds

BARS = 220
SYMBOL_COUNT = 40


def _frame(seed: int, bars: int = BARS) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.02, 0.3, bars)
    close = np.maximum(10.0 + np.cumsum(steps), 1.0)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2023-01-02", periods=bars),
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, bars),
            "amount": close * 1_000_000,
        }
    )


def _data() -> dict[str, pd.DataFrame]:
    return {f"{i:06d}.SZ": _frame(i) for i in range(SYMBOL_COUNT)}


def _active_factors(variant: WalkForwardGridVariant) -> frozenset[str]:
    strat = CompositeStrategy(
        thresholds=_apply_walkforward_grid_variant(load_thresholds(), variant)
    )
    active = {"momentum"}
    if strat._has_tr():
        active.add("triple_rise")
    if strat._has_volume():
        active.add("volume")
    if strat._has_mr():
        active.add("mean_reversion")
    return frozenset(active)


def _declared_factor(variant: WalkForwardGridVariant) -> str:
    mix = variant.strategy_mix or "momentum"
    return {"volume": "volume", "mean_reversion": "mean_reversion"}.get(mix, "momentum")


def test_every_variant_activates_its_declared_factor() -> None:
    """声明的 strategy_mix 对应的因子必须真的在生效集合里。"""
    for variant in _WALKFORWARD_VALIDATED_GRID_VARIANTS:
        declared = _declared_factor(variant)
        active = _active_factors(variant)
        assert declared in active, (
            f"{variant.variant_id} 声称 {declared} 但实际生效因子 = {sorted(active)}"
        )


def test_wf_v01_uses_volume_so_it_differs_from_wf_001() -> None:
    """WF-V01 必须真的用上 volume（否则它与 WF-001 是同一个变体的复制品）。"""
    by_id = {v.variant_id: v for v in _WALKFORWARD_VALIDATED_GRID_VARIANTS}
    assert "volume" in _active_factors(by_id["WF-V01"])
    assert "volume" not in _active_factors(by_id["WF-001"])


def test_wf_mr1_uses_mean_reversion_so_it_differs_from_wf_b03() -> None:
    by_id = {v.variant_id: v for v in _WALKFORWARD_VALIDATED_GRID_VARIANTS}
    assert "mean_reversion" in _active_factors(by_id["WF-MR1"])
    assert "mean_reversion" not in _active_factors(by_id["WF-B03"])


def _score_vector(variant: WalkForwardGridVariant, data: dict[str, pd.DataFrame]):
    strat = CompositeStrategy(
        thresholds=_apply_walkforward_grid_variant(load_thresholds(), variant)
    )
    scores = strat.calculate_score(data, regime="unknown")
    return tuple(round(scores[s], 9) for s in sorted(scores))


def _profile_signature(
    variants, data: dict[str, pd.DataFrame]
) -> dict[str, tuple[tuple[float, ...], int]]:
    return {
        v.variant_id: (_score_vector(v, data), v.top_n)
        for v in variants
    }


def test_stable_plus_has_no_duplicate_variant_columns() -> None:
    """门禁网格里不得出现「打分 + top_n 完全相同」的重复列。

    这是 ``MIN_CSCV_VARIANTS`` / PBO 统计有效性的前提：CSCV 假设 N 个**互异**策略，
    重复列会让参数被幻觉地"凑够"，并使 PBO 失真。
    """
    data = _data()
    for variants in (
        _WALKFORWARD_STABLE_GRID_VARIANTS,
        _WALKFORWARD_STABLE_PLUS_GRID_VARIANTS,
    ):
        seen: dict[tuple[tuple[float, ...], int], str] = {}
        for vid, sig in _profile_signature(variants, data).items():
            assert sig not in seen, (
                f"{vid} 与 {seen[sig]} 打分+top_n 完全相同 → 网格存在重复列"
            )
            seen[sig] = vid
