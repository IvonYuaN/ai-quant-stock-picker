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
