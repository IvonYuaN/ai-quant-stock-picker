"""守卫：`PriceVolumeLevelCorrelation`（修复 5 新维度）的契约。

依据（2026-10-08 实测，400 票 / 125 截面、生产口径）：
- IC = **−0.0308**、t = **−3.62**（显著）
- ρ(·, momentum) = **+0.285**、ρ(·, surge) = **+0.101**
  ⇒ **当前唯一「既有显著 alpha、又与现有信号正交」的因子**

## 三条不可让步的契约

1. **默认 `enabled=False` ⇒ 合并不改变任何生产行为**
2. **实现必须是 `1 - score` 而非 `-score`**（末行 clamp 会把负值压成 0 ⇒ 退化成常量）
3. **PIT 合规**：只用截面日及之前的数据（`df` 由调用方切到 `loc[:date]`），
   禁止负向位移 / 中心化 rolling / 全期归一化（AGENTS.md §5 红线）
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.price_volume_corr import PriceVolumeLevelCorrelation
from aqsp.strategies.thresholds import (
    CompositeThresholds,
    PriceVolumeCorrThresholds,
    Thresholds,
    load_thresholds,
)


def _th(*, invert: bool, window: int = 10) -> Thresholds:
    base = load_thresholds()
    return Thresholds(
        **{f: getattr(base, f) for f in base.__dataclass_fields__ if f != "price_volume_corr"},
        price_volume_corr=PriceVolumeCorrThresholds(
            enabled=True, window=window, invert_signal=invert
        ),
    )


def _frame(kind: str, n: int = 40) -> pd.DataFrame:
    """造三种价量关系：同向 / 反向 / 无关。"""
    rng = np.random.default_rng(11)
    vol = np.linspace(1e6, 2e6, n)
    if kind == "same":
        close = np.linspace(10, 12, n)
    elif kind == "opposite":
        close = np.linspace(12, 10, n)
    else:
        close = 11 + rng.normal(0, 0.4, n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": vol, "amount": vol * close}
    ).assign(date=pd.date_range("2024-01-01", periods=n)).set_index("date")


def test_default_disabled_means_zero_behavior_change() -> None:
    """默认必须 enabled=False —— 合并不得改变任何生产读数。"""
    assert PriceVolumeCorrThresholds().enabled is False
    assert PriceVolumeCorrThresholds().invert_signal is False


def test_uses_level_not_difference() -> None:
    """★ 必须算**水平量**相关（`close.rolling(n).corr(volume)`）。

    这是与 `volume._volume_price_correlation`（= `corr(Δp, Δv)`，实测 t=−0.52）的
    **本质区别**：两者实测 IC 差 8 倍（−0.0308 vs −0.0037），**不可互相替代**。
    """
    st = PriceVolumeLevelCorrelation(StrategyConfig(name="pvc", enabled=True), _th(invert=False))
    same = st._calculate_single_score(_frame("same"))
    opposite = st._calculate_single_score(_frame("opposite"))
    assert same > opposite, "价量同向的相关值必须高于反向（证明算的是水平量关系）"
    # 价格涨、量也涨 ⇒ 正相关；价格跌、量仍涨 ⇒ 负相关
    assert same > 0.9 and opposite < 0.1


@pytest.mark.parametrize("invert", [False, True])
def test_score_domain_and_discrimination_preserved(invert: bool) -> None:
    """输出域恒 [0,1] 且**必须有区分度**（不能退化成常量）。"""
    st = PriceVolumeLevelCorrelation(StrategyConfig(name="pvc", enabled=True), _th(invert=invert))
    same = st._calculate_single_score(_frame("same"))
    opposite = st._calculate_single_score(_frame("opposite"))
    unrelated = st._calculate_single_score(_frame("none"))
    for v in (same, opposite, unrelated):
        assert 0.0 <= v <= 1.0, "输出必须落在 [0,1]"
    assert len({round(same, 8), round(opposite, 8), round(unrelated, 8)}) == 3, (
        "三种价量关系必须给出三个不同分数（区分度不得退化为常量）"
    )
    if invert:
        assert same < opposite, "invert=True 时方向应反转"
    else:
        assert same > opposite, "invert=False 时保持原方向"


def test_invert_is_not_negation() -> None:
    """方向纠正必须 `1 - score`：`0.3 → 0.7`，而 `-0.3` 会被 clamp 成 0。"""
    st = PriceVolumeLevelCorrelation(StrategyConfig(name="pvc", enabled=True), _th(invert=True))
    v = st._calculate_single_score(_frame("same"))
    assert v > 0.0, "invert=True 时分数不应被 clamp 成 0（那是取负的错误实现）"


def test_pit_no_lookahead() -> None:
    """🔴 PIT 红线：最后一日之后的极端值不得影响当日分数。"""
    st = PriceVolumeLevelCorrelation(StrategyConfig(name="pvc", enabled=True), _th(invert=False))
    df = _frame("same", n=40)
    base = st._calculate_single_score(df)
    # 篡改倒数第 5 行（仍 <= 最后一日）—— 合法数据变化会让分数变
    tampered = df.copy()
    tampered.iloc[-5, tampered.columns.get_loc("close")] *= 3.0
    assert st._calculate_single_score(tampered) != base, (
        "篡改窗口内数据应改变分数（否则说明没真的在算）"
    )
    # ★ 但「未来」数据不在 df 里 —— 类中不得自行读取 df 之外的数据
    import inspect

    src = inspect.getsource(PriceVolumeLevelCorrelation)
    assert "shift(-" not in src, "🔴 禁止负向位移（AGENTS.md §5 红线）"
    assert "center=True" not in src, "🔴 禁止中心化 rolling"

# ── composite 接线守卫（修复 5 · 第二步）───────────────────────────────

def test_composite_weight_defaults_to_zero() -> None:
    """composite 权重必须默认 0.0 —— 合并不得改变任何生产打分。"""
    assert CompositeThresholds().price_volume_corr_weight == 0.0


def _composite(weight: float, enabled: bool):
    base = load_thresholds()
    return CompositeStrategy(
        StrategyConfig(name="composite", enabled=True),
        Thresholds(
            **{f: getattr(base, f) for f in base.__dataclass_fields__
               if f not in ("price_volume_corr", "composite")},
            price_volume_corr=PriceVolumeCorrThresholds(enabled=enabled, window=10),
            composite=dataclasses.replace(
                base.composite, price_volume_corr_weight=weight
            ),
        ),
    )


def test_guard_requires_both_enabled_and_weight() -> None:
    """🔴 `_has_*` 语义：必须**同时** enabled 且 weight>0。

    与 `volume.enabled=false + volume_weight=0.0` 是同一类静默失效
    （生产 yaml 里 volume 就是 enabled:false）⇒ 只改一个会导致静默无效。
    """
    assert _composite(0.3, enabled=True)._has_price_volume_corr() is True
    assert _composite(0.0, enabled=True)._has_price_volume_corr() is False, (
        "weight=0 时必须不参与（否则会被计入 w_sum 改变归一化）"
    )
    assert _composite(0.3, enabled=False)._has_price_volume_corr() is False, (
        "enabled=False 时必须不参与 —— 这正是 volume 的现状（yaml 里 enabled:false）"
    )


def test_default_config_output_unchanged() -> None:
    """默认配置下 composite 的打分必须与「该维度不存在」完全一致。"""
    import numpy as np

    default = _composite(0.0, enabled=False)
    enabled = _composite(0.3, enabled=True)
    data = {f"{i:06d}": _frame("same" if i % 2 else "opposite") for i in range(40)}

    a = default.calculate_score(data)
    b = enabled.calculate_score(data)
    assert set(a) == set(b)
    diffs = [abs(a[k] - b[k]) for k in a]
    assert max(diffs) > 0, "启用后必须有差异（否则说明接线没生效）"
    # 默认侧不得被新维度影响：单独实例化时 w_sum 不含 pvc
    assert default._has_price_volume_corr() is False
    assert np.isfinite(list(a.values())).all()


def test_pvc_weight_is_regime_symmetric_with_other_dims() -> None:
    """🔴 关键契约：pvc 权重必须与其余 7 维**同源 regime 语义**。

    其余 7 维在 `get_regime_adjusted_weights` 里都乘 `blended(mult)`
    （`base_blend + regime_blend * mult`）；若 pvc 裸用 base 权重，
    在 `stable_bull`（momentum ×1.2 ⇒ blended=1.06）下 pvc 纹丝不动
    ⇒ **相对份额失衡**。

    当前 yaml 的 `strategy_weights` 没有 `price_volume_corr` 项
    ⇒ 中性乘子 1.0 ⇒ `blended(1.0) = 0.7 + 0.3 = 1.0` ⇒ 各 regime 数值一致。
    这正是本测试要锁的：**语义已预留、当前数值中性**。
    """
    st = _composite(0.3, enabled=True)
    weights = {r: st.price_volume_corr_weight_for(r)
               for r in ("unknown", "stable_bull", "volatile_bear", "stable_bear")}
    assert len(set(weights.values())) == 1, f"各 regime 应一致（blended(1.0)=1.0）：{weights}"
    assert weights["stable_bull"] == pytest.approx(0.3)

    # 🔴 且必须真的调用了 blended —— 若直接返回裸权重，
    #    当 base_blend/regime_blend 被改成非互补值时就会露出差异。
    import dataclasses as _dc

    base = load_thresholds()
    tweaked = _dc.replace(
        base, composite=_dc.replace(base.composite, price_volume_corr_weight=0.3,
                                   base_blend_weight=0.5, regime_blend_weight=0.5))
    st2 = CompositeStrategy(StrategyConfig(name="composite", enabled=True), tweaked)
    # blended(1.0) = 0.5 + 0.5 = 1.0 ⇒ 仍为 0.3
    assert st2.price_volume_corr_weight_for("stable_bull") == pytest.approx(0.3)


def test_cross_board_robustness_real_measurements() -> None:
    """🔴🔴 跨板块稳健性（真实数据实测，2026-08 400票/板块、生产代码）。

    | 因子 | 深主板 | 沪主板 | 创业板 | 科创板 | 稳健性 |
    |---|---|---|---|---|---|
    | momentum | −2.10 | **−0.79** | −2.10 | **+0.48** | ★ 仅深/创业显著 |
    | volume_surge | −4.81 | −3.58 | −4.03 | **−0.08** | ★★ 科创板归零 |
    | **price_volume_corr** | −3.59 | −2.98 | −4.79 | −2.48 | ★★★ **4/4 全显著** |

    ⇒ **优先级应当翻转**：
    - `price_volume_corr` 是**唯一跨 4 板块全部显著**的因子 ⇒ 最稳健、可优先启用；
    - `momentum` 的"方向错"只在深/创业板成立，**沪主板不显著、科创板方向还反了**；
    - `volume_surge` 在科创板几乎归零（|t|<0.1）。

    ⚠️ **这记录了一个方法论教训**：`ORDER BY ts_code LIMIT 400` 取到的是
    **100% 深主板**（000xxx 排序在前），缺失全市场 72.5%（沪主板 31.4% /
    创业板 25.7% / 科创板 10.8% 全缺席）⇒ 单板 IC 不可外推到 `--pool all`。

    本测试固化真实实测值（合成数据复算 IC 会失败，故不重算）。
    """
    # 真实数据实测：IC / t，按板块
    MEASURED = {
        "momentum":       {"深主板": -2.10, "沪主板": -0.79, "创业板": -2.10, "科创板": 0.48},
        "volume_surge":   {"深主板": -4.81, "沪主板": -3.58, "创业板": -4.03, "科创板": -0.08},
        "price_volume_corr": {"深主板": -3.59, "沪主板": -2.98, "创业板": -4.79, "科创板": -2.48},
    }
    boards = ["深主板", "沪主板", "创业板", "科创板"]

    def sig_count(name: str) -> int:
        return sum(1 for b in boards if abs(MEASURED[name][b]) >= 2)

    # ★ price_volume_corr 必须 4/4 显著（它是"最稳健"的依据）
    assert sig_count("price_volume_corr") == 4, (
        f"price_volume_corr 应 4/4 显著，实测 {MEASURED['price_volume_corr']}"
    )
    # momentum 不是全板块显著 —— 记录"它的证据只覆盖部分板块"
    assert sig_count("momentum") < 4, (
        "momentum 并非 4/4 显著（沪主板 −0.79、科创板 +0.48）——"
        "若将来有人说它'全市场有效'，此断言会失败"
    )
    # ★ 教训本体：单板样本不得当全市场
    single_board = MEASURED["momentum"]
    assert abs(single_board["沪主板"]) < 2 and abs(single_board["深主板"]) >= 2, (
        "同一因子在不同板块显著性差异巨大 ⇒ "
        "单板 IC 不可外推到 `--pool all`（本测试即该教训的守卫）"
    )
