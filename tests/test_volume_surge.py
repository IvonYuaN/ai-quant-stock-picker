"""守卫：`VolumeSurge`（量能冲击独立维度）的契约。

依据（2026-10-08 实测，400 票 / 125 截面、生产代码）：
- `volume` 三个子分逐一单独 IC：**surge −0.0370/t−4.81** ★、breakout −0.0264/t−2.24、
  correlation −0.0037/t−0.52（无 alpha）⇒ `volume` 的有效性几乎全部来自 `surge`。
- 独立性：ρ(surge, momentum)=+0.500、ρ(surge, triple_rise)=+0.580（中等相关，不同簇）。

## 三条契约
1. **默认 `enabled=False` + composite 权重 0.0 ⇒ 合并不改变生产行为**
2. **必须复用 `VolumeBreakoutStrategy._volume_surge`**（不复制算法，避免两处漂移）
3. **PIT 合规**：只用截面日及之前的数据；无负向位移 / 中心化 rolling / 全期归一化
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import (
    CompositeThresholds,
    Thresholds,
    VolumeSurgeThresholds,
    load_thresholds,
)
from aqsp.strategies.volume import VolumeBreakoutStrategy
from aqsp.strategies.volume_surge import VolumeSurge


def _th(*, invert: bool) -> Thresholds:
    base = load_thresholds()
    return Thresholds(
        **{f: getattr(base, f) for f in base.__dataclass_fields__ if f != "volume_surge"},
        volume_surge=VolumeSurgeThresholds(enabled=True, invert_signal=invert),
    )


def _frame(ramp: float) -> pd.DataFrame:
    """ramp>1 逐步放量；ramp<1 逐步缩量。"""
    n = 70
    vol = np.linspace(1e6, 1e6 * ramp, n)
    base = np.linspace(10, 12, n)
    return pd.DataFrame(
        {"open": base, "high": base * 1.01, "low": base * 0.99,
         "close": base, "volume": vol, "amount": vol * base}
    ).assign(date=pd.date_range("2024-01-01", periods=n)).set_index("date")


def test_defaults_off() -> None:
    assert VolumeSurgeThresholds().enabled is False
    assert VolumeSurgeThresholds().invert_signal is False
    assert CompositeThresholds().volume_surge_weight == 0.0


def test_reuses_volume_impl_no_algorithm_duplication() -> None:
    """🔴 必须复用 `VolumeBreakoutStrategy._volume_surge`，不复制算法。

    复制会导致两处实现漂移（阈值改动只改一处 ⇒ 维度与 volume 行为不一致）。
    """
    st = VolumeSurge(StrategyConfig(name="vs", enabled=True), _th(invert=False))
    assert isinstance(st._impl, VolumeBreakoutStrategy)
    df = _frame(2.5)
    cfg = load_thresholds().volume_surge
    w = df.sort_values("date").tail(cfg.lookback_days)
    expected = st._impl._volume_surge(
        w["volume"].values, cfg.volume_ma_period, cfg.surge_multiplier
    )
    got = st.calculate_score({"x": df})["x"]
    assert got == pytest.approx(max(0.0, min(1.0, expected))), "必须与 volume 的 surge 一致"


@pytest.mark.parametrize("invert", [False, True])
def test_domain_and_discrimination(invert: bool) -> None:
    st = VolumeSurge(StrategyConfig(name="vs", enabled=True), _th(invert=invert))
    rising = st.calculate_score({"a": _frame(3.0), "b": _frame(2.5), "c": _frame(0.4)})["a"]
    falling = st.calculate_score({"a": _frame(0.4)})["a"]
    assert 0.0 <= rising <= 1.0 and 0.0 <= falling <= 1.0
    assert rising != falling, "放量/缩量必须不同分数（区分度不得退化为常量）"
    if invert:
        assert rising < falling
    else:
        assert rising > falling


def test_invert_not_negation() -> None:
    """方向纠正必须 `1 - score`（取负会被 clamp 成 0 ⇒ 退化成常量）。"""
    st = VolumeSurge(StrategyConfig(name="vs", enabled=True), _th(invert=True))
    v = st.calculate_score({"a": _frame(2.5)})["a"]
    assert v > 0.0, "invert=True 时分数不应被 clamp 成 0"


def test_composite_guard_requires_both_flags() -> None:
    """`_has_*` 必须要求 enabled AND weight>0 —— 只改一个会静默无效（同 volume 的坑）。"""
    base = load_thresholds()

    def build(weight: float, enabled: bool) -> CompositeStrategy:
        return CompositeStrategy(
            StrategyConfig(name="composite", enabled=True),
            Thresholds(
                **{f: getattr(base, f) for f in base.__dataclass_fields__
                   if f not in ("volume_surge", "composite")},
                volume_surge=VolumeSurgeThresholds(enabled=enabled),
                composite=dataclasses.replace(base.composite, volume_surge_weight=weight),
            ),
        )

    assert build(0.3, True)._has_volume_surge() is True
    assert build(0.0, True)._has_volume_surge() is False
    assert build(0.3, False)._has_volume_surge() is False
    # regime 同源：yaml 无 volume_surge 项 ⇒ blended(1.0)=1.0 ⇒ 各 regime 一致
    st = build(0.3, True)
    ws = {r: st.volume_surge_weight_for(r)
          for r in ("unknown", "stable_bull", "volatile_bear", "stable_bear")}
    assert len(set(ws.values())) == 1, ws
    assert ws["stable_bull"] == pytest.approx(0.3)


def test_pit_guard() -> None:
    """🔴 源码不得含负向位移 / 中心化 rolling。"""
    import inspect

    src = inspect.getsource(VolumeSurge)
    assert "shift(-" not in src, "🔴 禁止负向位移（AGENTS.md §5）"
    assert "center=True" not in src, "🔴 禁止中心化 rolling"


def test_momentum_invert_must_paired_with_triple_rise_removal() -> None:
    """★★★ 组合层**交互效应**守卫（2026-08 实测发现）。

    实测（400 票、生产代码、composite 层）：

    | 场景 | score std | top20 与基线重合 |
    |---|---|---|
    | 现状（基线） | **0.1504** | 20/20 |
    | **只开 `momentum.invert_signal`** | **0.0757** ⚠️ | 8/20 |
    | mom 反向 + triple_rise 归零 | 0.1498 | 0/20 |

    **单独把 momentum 反向会让打分区分度腰斩** —— 因为 `momentum` 与 `triple_rise`
    同源（ρ = 0.609，生产实测），翻转 mom 后它与 tr **方向冲突**，
    两者在归一化的 `(mom + tr)/2` 里互相抵消。

    ⇒ 单因子层 IC 翻正（t=+2.88）**不代表组合层受益**；
    ⇒ **修复 1 必须与「降/清 triple_rise 权重」成对启用**。

    本测试锁住这条交互约束，防止将来只开 `momentum.invert_signal` 就上线。
    """
    base = load_thresholds()

    def build(*, mom_inv: bool, tr_w: float) -> CompositeStrategy:
        mom = base.momentum
        return CompositeStrategy(
            StrategyConfig(name="composite", enabled=True),
            Thresholds(
                **{f: getattr(base, f) for f in base.__dataclass_fields__
                   if f not in ("momentum", "composite")},
                momentum=dataclasses.replace(mom, invert_signal=mom_inv),
                composite=dataclasses.replace(base.composite, triple_rise_weight=tr_w),
            ),
        )

    import numpy as np

    def spread(st: CompositeStrategy) -> float:
        rng = np.random.default_rng(7)
        data = {}
        for i in range(60):
            n = 70
            vol = np.linspace(1e6, 1e6 * (0.4 + 0.05 * (i % 8)), n)
            base_px = np.linspace(10, 10 + 0.2 * (i % 7), n) + rng.normal(0, 0.25, n)
            data[f"{i:06d}"] = pd.DataFrame(
                {"open": base_px, "high": base_px * 1.01, "low": base_px * 0.99,
                 "close": base_px, "volume": vol, "amount": vol * base_px}
            ).assign(date=pd.date_range("2024-01-01", periods=n)).set_index("date")
        vals = np.array(list(st.calculate_score(data).values()))
        return float(vals.std())

    baseline = spread(build(mom_inv=False, tr_w=0.3))
    mom_only = spread(build(mom_inv=True, tr_w=0.3))
    paired = spread(build(mom_inv=True, tr_w=0.0))

    # 反向且保留 tr ⇒ 区分度应明显低于基线（两者互相抵消）
    assert mom_only < baseline * 0.75, (
        f"只开 momentum 反向会让区分度塌陷（baseline={baseline:.4f} "
        f"mom_only={mom_only:.4f}）—— 说明 tr 未清理时两者冲突"
    )
    # 反向且清掉 tr ⇒ 区分度应恢复
    assert paired > mom_only * 1.3, (
        f"清掉 triple_rise 后区分度应恢复（paired={paired:.4f} vs mom_only={mom_only:.4f}）"
    )


def test_min_total_score_strictness_flips_under_fixes() -> None:
    """🔴🔴 启用清单的**第三件事**：`min_total_score` 的实际严格度会随修复翻转。

    实测（300 票、生产代码，门槛 = 0.4）：

    | 场景 | score 均值 | 通过率 |
    |---|---|---|
    | 基线（mom 0.3 + tr 0.3） | 0.17 | **9.7%**（29/300） |
    | mom 反向 | 0.47 | **87.0%** |
    | mom 反向 + tr 归零 | 0.82 | **94.0%** |

    ⇒ **只改 `invert_signal` 与权重、而不动 `min_total_score`，
    等于顺手把选股条件放宽了 9 倍**（29 只候选 → 261 只候选）。

    ⚠️ 表面看"都选 top_n 只"没差别，但基线是「29 只里挑 top 10」、
    修复后是「261 只里挑 top 10」—— **取数范围完全不同**。

    本测试**记录并锁住这个事实**：若将来有人只改开关+权重就上线，
    本测试会明确显示通过率跳到 ~90%（提醒门槛没跟着调）。
    """
    import numpy as np

    base = load_thresholds()

    def build(*, mom_inv: bool, tr_w: float) -> CompositeStrategy:
        mom = base.momentum
        return CompositeStrategy(
            StrategyConfig(name="composite", enabled=True),
            Thresholds(
                **{f: getattr(base, f) for f in base.__dataclass_fields__
                   if f not in ("momentum", "composite")},
                momentum=dataclasses.replace(mom, invert_signal=mom_inv),
                composite=dataclasses.replace(base.composite, triple_rise_weight=tr_w),
            ),
        )

    rng = np.random.default_rng(5)
    data = {}
    for i in range(120):
        n = 70
        vol = np.linspace(1e6, 1e6 * (0.4 + 0.04 * (i % 7)), n)
        px = np.linspace(10, 10 + 0.15 * (i % 6), n) + rng.normal(0, 0.2, n)
        data[f"{i:06d}"] = pd.DataFrame(
            {"open": px, "high": px * 1.01, "low": px * 0.99,
             "close": px, "volume": vol, "amount": vol * px}
        ).assign(date=pd.date_range("2024-01-01", periods=n)).set_index("date")

    thr = base.composite.min_total_score
    base_rate = float((np.array(list(build(mom_inv=False, tr_w=0.3)
                                  .calculate_score(data).values())) >= thr).mean())
    inv_rate = float((np.array(list(build(mom_inv=True, tr_w=0.3)
                                .calculate_score(data).values())) >= thr).mean())

    # 基线应是"紧门槛"（少数通过）
    assert base_rate < 0.5, f"基线通过率应偏低（实测 {base_rate:.1%}）"
    # ★ 反向后若通过率大幅跳升 ⇒ 说明门槛必须跟着调，否则等于放宽选股条件
    assert inv_rate > base_rate, (
        f"反向使通过率上升 {base_rate:.1%} → {inv_rate:.1%}；"
        "启用清单必须同时调 min_total_score"
    )
