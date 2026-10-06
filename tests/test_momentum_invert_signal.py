"""守卫：momentum `invert_signal` 方向纠正开关的契约。

背景（2026-10-06 实测）：
- momentum 在近 3 年 A 股**反向有效**：146 截面单窗 IC = **−2.52**（0930 窗 −1.87）；
  样本内 t=**−6.46**、样本外 t=**−3.85**（同号 + 双侧 |t|≥2，OOS 独立复核）。
- 同期股票池等权 **+45.40%** 而生产基线 **−13.89%**（超额约 **−59pp**）
  ⇒ 生产给 momentum 最高权重 0.30 = **系统性买反**。
- ⇒ 引入 `invert_signal` 开关做方向纠正。

## 两条不可让步的契约

1. **默认 False ⇒ 行为逐位不变**（合并本身不得改变任何生产读数）
2. **实现必须是 `1 - score` 而非 `-score`**
   因为 `_calculate_single_score` 末行 `max(0.0, min(1.0, final_score))` 把分数钳制在
   [0,1]：取负会被 clamp 成全 0 ⇒ 该维度**退化成常量**（无区分度），
   等于把「买反」变成「完全不打分」，**比现状更糟**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MOMENTUM_SRC = REPO_ROOT / "src" / "aqsp" / "strategies" / "momentum.py"
THRESHOLDS_SRC = REPO_ROOT / "src" / "aqsp" / "strategies" / "thresholds.py"


def test_invert_flag_defaults_to_false() -> None:
    """默认必须 False —— 合并不得改变任何生产行为。"""
    src = THRESHOLDS_SRC.read_text(encoding="utf-8")
    assert "invert_signal: bool = False" in src, (
        "invert_signal 必须默认 False；默认 True 会在合并瞬间改变生产打分"
    )


def test_direction_inversion_uses_one_minus_not_negation() -> None:
    """方向纠正必须 `1.0 - final_score`；`clip(-s,0,1)` 会把 3 维全 clamp 成 0。"""
    src = MOMENTUM_SRC.read_text(encoding="utf-8")
    assert "1.0 - final_score" in src, "必须用 `1.0 - final_score` 做方向翻转"
    # 不允许出现裸取负后再 clamp 的形态
    assert "final_score = -final_score" not in src, (
        "禁止 `-final_score`：末行 clamp 会让它全变 0，维度退化成常量"
    )


def test_clamp_still_applied_after_inversion() -> None:
    """翻转后仍须 clamp（保证输出域不变，[0,1]）。"""
    src = MOMENTUM_SRC.read_text(encoding="utf-8")
    assert "max(0.0, min(1.0, final_score))" in src, "翻转后必须保留 clamp"
    # 翻转语句必须出现在 clamp **之前**（否则 1-s 会被再 clamp 一次，语义虽同但顺序易误读）
    assert src.index("1.0 - final_score") < src.index("max(0.0, min(1.0, final_score))")


@pytest.mark.parametrize("invert", [False, True])
def test_score_domain_and_discrimination_preserved(invert: bool) -> None:
    """两种模式下：输出域恒为 [0,1]，且**必须有区分度**（不能退化成常量）。

    这是最关键的一条 —— 它直接拦住「取负导致全 0 变成常量」那个陷阱。
    """
    from aqsp.strategies.momentum import MomentumStrategy
    from aqsp.strategies.base import StrategyConfig
    from aqsp.strategies.thresholds import MomentumThresholds, load_thresholds

    th = load_thresholds()
    obj = MomentumStrategy(StrategyConfig(name="momentum", enabled=True), th)
    m = th.momentum
    obj.thresholds = type(th)(
        **{
            **{f.name: getattr(th, f.name) for f in th.__dataclass_fields__.values()},
            "momentum": MomentumThresholds(
                lookback_days=m.lookback_days, min_returns=m.min_returns,
                max_volatility=m.max_volatility, rsi_overbought=m.rsi_overbought,
                rsi_oversold=m.rsi_oversold, ma_period=m.ma_period,
                trend_strength_threshold=m.trend_strength_threshold,
                weights=m.weights, invert_signal=invert,
            ),
        }
    )

    import numpy as np
    import pandas as pd

    def frame(up: bool) -> pd.DataFrame:
        n = 70
        base = np.linspace(10, 12 if up else 10.5, n)
        return pd.DataFrame(
            {"open": base, "high": base * 1.01, "low": base * 0.99,
             "close": base, "volume": np.full(n, 1e6), "amount": np.full(n, 1e7)}
        ).assign(date=pd.date_range("2024-01-01", periods=n)).set_index("date")

    s_up = obj._calculate_single_score(frame(True))
    s_dn = obj._calculate_single_score(frame(False))
    assert 0.0 <= s_up <= 1.0 and 0.0 <= s_dn <= 1.0, "输出必须落在 [0,1]"
    assert s_up != s_dn, "上涨票与下跌票必须给出不同分数（区分度不得退化为常量）"
    if invert:
        assert s_up < s_dn, "invert=True 时方向应反转：上涨票得分应更低"
    else:
        assert s_up > s_dn, "invert=False 时保持原方向：上涨票得分应更高"


def test_invert_is_wired_to_thresholds_not_hardcoded() -> None:
    """开关必须读配置，不可硬编码（否则实验口径无法审计）。"""
    src = MOMENTUM_SRC.read_text(encoding="utf-8")
    assert "self.thresholds.momentum.invert_signal" in src, (
        "invert 必须读 thresholds 配置，不得硬编码"
    )
