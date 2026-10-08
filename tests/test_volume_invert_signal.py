"""守卫：volume `invert_signal` 方向纠正开关的契约（与 momentum 同模式）。

背景（2026-10-08 实测，生产 `VolumeBreakoutStrategy` + 真实行情，250 票 / 125 截面）：
- volume 近 3 年 A 股**反向有效**：原向 IC = **−0.0324**、t = **−3.07**（显著）；
- 而生产给它 **0.0 权重**（唯一被实测证实的强信号却完全未启用），
  给 0.3 权重的 `triple_rise` 的 t 仅 −1.25（噪音）。

## 两条不可让步的契约

1. **默认 False ⇒ 行为逐位不变**（合并本身不得改变任何生产读数）
2. **实现必须是 `1 - score` 而非 `-score`**
   因为 `_calculate_single_score` 末行 `max(0.0, min(1.0, final))` 把分数钳制在 [0,1]：
   取负会被 clamp 成全 0 ⇒ 该维度**退化成常量**（无区分度），
   等于把「未启用」变成「启用了但无区分度」，**比现状更糟**。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VOLUME_SRC = REPO_ROOT / "src" / "aqsp" / "strategies" / "volume.py"
THRESHOLDS_SRC = REPO_ROOT / "src" / "aqsp" / "strategies" / "thresholds.py"


def test_invert_flag_defaults_to_false() -> None:
    """默认必须 False —— 合并不得改变任何生产行为。"""
    src = THRESHOLDS_SRC.read_text(encoding="utf-8")
    assert "invert_signal: bool = False" in src, (
        "volume.invert_signal 必须默认 False；默认 True 会在合并瞬间改变生产打分"
    )


def test_direction_inversion_uses_one_minus_not_negation() -> None:
    """方向纠正必须 `1.0 - final`；`clip(-x,0,1)` 会把该维全 clamp 成 0。"""
    src = VOLUME_SRC.read_text(encoding="utf-8")
    assert "1.0 - final" in src, "必须用 `1.0 - final` 做方向翻转"
    assert "final = -final" not in src, (
        "禁止 `-final`：末行 clamp 会让它全变 0，维度退化成常量"
    )


def test_clamp_still_applied_after_inversion() -> None:
    """翻转后仍须 clamp，且翻转在 clamp **之前**。"""
    src = VOLUME_SRC.read_text(encoding="utf-8")
    assert "max(0.0, min(1.0, final))" in src, "翻转后必须保留 clamp"
    assert src.index("1.0 - final") < src.index("max(0.0, min(1.0, final))")


def test_invert_is_wired_to_thresholds_not_hardcoded() -> None:
    """开关必须读配置，不可硬编码（否则实验口径无法审计）。"""
    src = VOLUME_SRC.read_text(encoding="utf-8")
    assert "cfg.invert_signal" in src, "invert 必须读 thresholds 配置，不得硬编码"


@pytest.mark.parametrize("invert", [False, True])
def test_score_domain_and_discrimination_preserved(invert: bool) -> None:
    """两种模式下：输出域恒为 [0,1]，且**必须有区分度**（不能退化成常量）。

    这条直接拦住「取负导致全 0 变成常量」那个陷阱。
    """
    from aqsp.strategies.base import StrategyConfig
    from aqsp.strategies.thresholds import VolumeThresholds, load_thresholds
    from aqsp.strategies.volume import VolumeBreakoutStrategy

    th = load_thresholds()
    v = th.volume
    th2 = type(th)(
        **{
            **{f: getattr(th, f) for f in th.__dataclass_fields__},
            "volume": VolumeThresholds(
                enabled=v.enabled, lookback_days=v.lookback_days,
                volume_ma_period=v.volume_ma_period, surge_multiplier=v.surge_multiplier,
                price_ma_period=v.price_ma_period, correlation_window=v.correlation_window,
                weights=v.weights, invert_signal=invert,
            ),
        }
    )
    obj = VolumeBreakoutStrategy(StrategyConfig(name="volume", enabled=True), th2)

    # 🔍 注意：volume 的子分都是**相对量**（`_volume_surge` 对 MA20 归一、
    # `_volume_price_correlation` 取相关系数）⇒ **绝对量级的倍数不影响分数**。
    # ⚠️ 第一次写测试时我用了「1x vs 3x 恒定量」⇒ 两组得分完全相同 ⇒ 测试失败。
    # 正确做法：用**放量/缩量的时间斜率**制造区分度。
    def frame(ramp: float) -> pd.DataFrame:
        n = 80
        base = np.linspace(10, 12, n)
        vol = np.linspace(1.0e6, 1.0e6 * ramp, n)   # 逐步放量 vs 逐步缩量
        return pd.DataFrame(
            {"open": base, "high": base * 1.01, "low": base * 0.99,
             "close": base, "volume": vol, "amount": vol * base}
        ).assign(date=pd.date_range("2024-01-01", periods=n)).set_index("date")

    s_rising = obj._calculate_single_score(frame(2.5))   # 放量
    s_falling = obj._calculate_single_score(frame(0.4))  # 缩量
    assert 0.0 <= s_rising <= 1.0 and 0.0 <= s_falling <= 1.0, "输出必须落在 [0,1]"
    assert s_rising != s_falling, (
        "放量/缩量必须给出不同分数（区分度不得退化为常量）"
    )
    if invert:
        assert s_rising < s_falling, "invert=True 时方向应反转：放量得分应更低"
    else:
        assert s_rising > s_falling, "invert=False 时保持原方向：放量得分应更高"