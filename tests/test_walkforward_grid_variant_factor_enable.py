"""网格变体的「声明因子族」必须真的生效（防 WF-V01/WF-MR1 那类幽灵变体回归）。

背景（2026-09-17 实测）：``_apply_walkforward_grid_variant`` 此前只改权重、**不 enable** 因子，
而 ``thresholds.yaml`` 里 ``volume`` / ``mean_reversion`` 默认 ``enabled=False``，于是
``CompositeStrategy._has_volume()`` / ``_has_mr()`` 恒为 False：

- ``WF-V01``（声称 volume）实际只用 mom+tr，**选出名单与 WF-001 逐位相同**；
- ``WF-MR1``（声称 mean_reversion）实际只用 mom+tr，**与 WF-B03 逐位相同**。

后果：``stable_plus``（生产默认 N=8）声称的「含因子族多样性」不成立，
且 ``MIN_CSCV_VARIANTS=8`` 这道 fail-closed 守卫被一个**重复列**凑数通过。

同一型缺陷在 ``htf_mr`` 档位（T3 方案 A，由 #163 加入 main）**复发**：
``lookback_days`` 被写进 momentum 而该档位把 momentum 权重清零 ⇒ ``lb`` 是死旋钮，
``WF-H04`` / ``WF-H05`` 与 ``WF-H01`` 完全同列（见 #175）。故本文件的档位清单已
扩到 ``htf_mr``，并把「互异列数 >= MIN_CSCV_VARIANTS」按档位参数化。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import pytest

from aqsp.cli import (
    _PLANB_VARIANTS,
    _WALKFORWARD_VALIDATED_GRID_VARIANTS,
    WalkForwardGridVariant,
    _apply_walkforward_grid_variant,
    _walkforward_grid_variants,
)
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import load_thresholds
from aqsp.walkforward_gate import MIN_CSCV_VARIANTS

PROFILES = ("stable", "stable_plus", "exploratory", "htf_mr", "planb_v1", "planb_v2", "planb_v3")

#: 变体数 >= MIN_CSCV_VARIANTS 的档位 —— 只有这些档位才受「互异列数必须真正够数」
#: 这条守卫约束（``stable`` N=5 天然不足，不在此列）。
PROFILES_MEETING_MIN_CSCV = ("stable_plus", "exploratory", "htf_mr", "planb_v1", "planb_v2", "planb_v3")

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


def _variant_signature(
    variant: WalkForwardGridVariant, data: dict[str, pd.DataFrame]
) -> tuple[tuple[float, ...], int, int]:
    """一个变体的「行为签名」= 打分向量 + top_n + horizon_days。

    判「重复变体」必须用**行为**而非参数表：``_apply_walkforward_grid_variant`` 产出的
    有效阈值才是真正决定打分的对象（幽灵变体问题即源于「参数不同、行为相同」）。

    为什么含 ``horizon_days``：它决定持有期（``cli.py`` 把它传给
    ``WalkForwardEngineConfig(horizon_days=...)``），因而**改变各变体的期收益序列** ——
    即 CSCV 收益矩阵里的列不同。故「同打分同 top_n、仅 horizon 不同」的变体
    （如 ``WF-B05`` 1 天 vs ``WF-001`` 3 天）是**合法互异列**，不算重复。

    反之，若打分 + top_n + horizon **三者全同**，则该变体在网格里是一根
    **重复列**：既产出同样的选股、也产出同样的收益序列 ⇒ 污染 CSCV/PBO，
    并使 ``MIN_CSCV_VARIANTS`` 被技术性凑数满足。
    """
    return (_score_vector(variant, data), variant.top_n, variant.horizon_days)


def _profile_signature(
    variants, data: dict[str, pd.DataFrame]
) -> dict[str, tuple[tuple[float, ...], int, int]]:
    return {v.variant_id: _variant_signature(v, data) for v in variants}


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_has_no_duplicate_variant_columns(profile: str) -> None:
    """门禁网格里不得出现「打分 + top_n 完全相同」的重复列（全部档位覆盖）。

    这是 ``MIN_CSCV_VARIANTS`` / PBO 统计有效性的前提：CSCV 假设 N 个**互异**策略，
    重复列会让参数被幻觉地"凑够"，并使 PBO 失真。

    ``exploratory`` = 全部已验证变体（含 WF-V01/WF-MR1），此前未被覆盖；
    ``htf_mr`` = T3 方案 A 档位（#175 复发点），此前未被覆盖；
    ``_apply_walkforward_grid_variant`` 的因子 enable 逻辑对所有档位是同一份代码，
    故必须全档同验。
    """
    data = _data()
    seen: dict[tuple[tuple[float, ...], int, int], str] = {}
    for vid, sig in _profile_signature(_walkforward_grid_variants(profile), data).items():
        assert sig not in seen, (
            f"[{profile}] {vid} 与 {seen[sig]} 打分+top_n+horizon 完全相同 "
            "→ 网格存在重复列"
        )
        seen[sig] = vid


def test_stable_plus_meets_min_cscv_variants_with_distinct_columns() -> None:
    """默认档位 ``stable_plus`` 的**互异**列数必须真正 >= ``MIN_CSCV_VARIANTS``。

    幽灵变体（重复列）会让这道 fail-closed 守卫被**技术性满足** —— 守卫数的是
    列数，而 CSCV/PBO 前提数的是**互异策略数**。此断言把两者钉在一起：仅当所有
    变体互异时，「N=8」才等价于「8 个互异策略」。
    """
    data = _data()
    variants = _walkforward_grid_variants("stable_plus")
    distinct = set(_profile_signature(variants, data).values())
    assert len(distinct) >= MIN_CSCV_VARIANTS, (
        f"stable_plus 有 {len(variants)} 列但仅 {len(distinct)} 个互异列；"
        f"MIN_CSCV_VARIANTS={MIN_CSCV_VARIANTS} 会被重复列凑数满足"
    )


@pytest.mark.parametrize("profile", PROFILES_MEETING_MIN_CSCV)
def test_profile_meets_min_cscv_variants_with_distinct_columns(profile: str) -> None:
    """所有「声称 N>=MIN_CSCV_VARIANTS」的档位，互异列数必须真的够。

    #175 回归点：``htf_mr``（T3 方案 A）此前把 ``lookback_days`` 只写进
    ``MomentumThresholds``，而该档位把 momentum 权重清零 ⇒ ``lb`` 是**死旋钮**，
    8 列里只有 6 个互异策略（``WF-H04`` / ``WF-H05`` 与 ``WF-H01`` 打分+top_n+horizon
    完全相同）。这正是 ``stable_plus`` 在 #135 修过的同一型缺陷，只是当时
    ``PROFILES`` 未含 ``htf_mr``（该档位由 #163 后加入 main），守卫覆盖不到。

    故此处按**档位**参数化而非只钉 ``stable_plus``：任何新增档位只要声称 N>=8
    就自动被这条守卫覆盖，不会再有「新档位漏网」。
    """
    data = _data()
    variants = _walkforward_grid_variants(profile)
    assert len(variants) >= MIN_CSCV_VARIANTS, (
        f"[{profile}] 只有 {len(variants)} 列，本用例只约束声称够数的档位"
    )
    distinct = set(_profile_signature(variants, data).values())
    assert len(distinct) >= MIN_CSCV_VARIANTS, (
        f"[{profile}] 有 {len(variants)} 列但仅 {len(distinct)} 个互异列；"
        f"MIN_CSCV_VARIANTS={MIN_CSCV_VARIANTS} 会被重复列凑数满足"
    )


# ---------------------------------------------------------------------------
# 方案 B（planb）专属契约：7 维 composite 权重向量必须被精确注入，且按权重
# 逐个 enable 因子（thresholds.yaml 默认 enabled=False，只改权重不 enable 会
# 静默退化成「权重写了、因子没开」的幽灵变体）。
# 7 维序 = (mom, quality, value, volume, mean_reversion, triple_rise, high_tight_flag)
# ---------------------------------------------------------------------------


def test_planb_profiles_declare_7_dim_composite_weights() -> None:
    """每个 planb profile 的 8 臂都必须显式声明 7 维 composite_weights。"""
    for profile, variants in _PLANB_VARIANTS.items():
        assert len(variants) == 8, f"[{profile}] 应为 8 臂（4 top_n × 2 horizon）"
        for variant in variants:
            assert variant.strategy_mix == "planb", variant.variant_id
            assert variant.composite_weights is not None, (
                f"[{profile}] {variant.variant_id} 缺 composite_weights"
            )
            assert len(variant.composite_weights) == 7, (
                f"[{profile}] {variant.variant_id} composite_weights 须 7 维"
            )


@pytest.mark.parametrize("profile", ("planb_v1", "planb_v2", "planb_v3"))
def test_planb_injects_exact_composite_weights(profile: str) -> None:
    """_apply_walkforward_grid_variant 必须把 7 维权重向量精确写进 CompositeThresholds。"""
    variants = _walkforward_grid_variants(profile)
    expected = tuple(float(x) for x in variants[0].composite_weights)  # 8 臂同向量
    thresholds = load_thresholds()
    first = _apply_walkforward_grid_variant(thresholds, variants[0])
    composite = first.composite
    injected = (
        composite.momentum_weight,
        composite.quality_weight,
        composite.value_weight,
        composite.volume_weight,
        composite.mean_reversion_weight,
        composite.triple_rise_weight,
        composite.high_tight_flag_weight,
    )
    assert injected == expected, f"[{profile}] 注入 {injected} != 声明 {expected}"


@pytest.mark.parametrize("profile", ("planb_v1", "planb_v2", "planb_v3"))
def test_planb_enables_only_declared_factors(profile: str) -> None:
    """声明权重 >0 的因子必须被 enable（防静默退化）；未声明的保持原 enabled 态。

    V1 摘 momentum（weight=0）但摊权给 quality/value/volume ⇒ 三者必须 enabled；
    V2 启用 htf(0.2) + 降 momentum(0.2) ⇒ quality/value/volume/htf enabled；
    V3 同 V2 但 htf 顶格(0.4) + momentum 摘除。
    """
    thresholds = load_thresholds()
    variant = _walkforward_grid_variants(profile)[0]
    applied = _apply_walkforward_grid_variant(thresholds, variant)
    w = variant.composite_weights
    # (composite 权重, 因子名, applied 上的 enabled 字段路径)
    cases = (
        (w[1], "quality", applied.quality.enabled),
        (w[2], "value", applied.value.enabled),
        (w[3], "volume", applied.volume.enabled),
        (w[4], "mean_reversion", applied.mean_reversion.enabled),
        (w[6], "high_tight_flag", applied.high_tight_flag.enabled),
    )
    for weight, name, enabled in cases:
        if weight > 0:
            assert enabled, f"[{profile}] {name} 权重={weight}>0 但未 enabled（静默退化）"
        else:
            # 未声明权重（=0）的因子不得被本分支误 enable
            assert not enabled or getattr(thresholds, name).enabled, (
                f"[{profile}] {name} 权重=0 却被 enable"
            )


@pytest.mark.parametrize("profile", ("planb_v1", "planb_v2", "planb_v3"))
def test_planb_v1_not_collapsed_to_volume_only(profile: str) -> None:
    """V1 摘 momentum 后质量/价值必须真在打分里（否则塌缩成 volume-only 幽灵变体）。"""
    if profile != "planb_v1":
        pytest.skip("仅 V1 校验摘 momentum 后是否塌缩")
    data = _data()
    v1 = _walkforward_grid_variants("planb_v1")[0]
    strat = CompositeStrategy(
        thresholds=_apply_walkforward_grid_variant(load_thresholds(), v1)
    )
    # V1 向量 (0.0,0.4,0.4,0.4,0,0,0)：quality/value/volume 三项都必须生效
    assert strat._has_quality(), "V1 quality 未生效（塌缩风险）"
    assert strat._has_value(), "V1 value 未生效（塌缩风险）"
    assert strat._has_volume(), "V1 volume 未生效（塌缩风险）"
    # 打分向量必须非退化（不是全 0 / 单一量）——确认三项因子真实参与
    scores = strat.calculate_score(data, regime="unknown")
    assert len(scores) > 0
    assert any(s != 0.0 for s in scores.values()), "V1 打分为全 0（因子族全未生效）"
