"""#199 v1.1.21 配置不变量守卫：锁住「因子方向纠正 ⇔ 配对权重 ⇔ 门槛」三件套。

背景（2026-10-13，生产 gate 实测 DSR=−1.4354 的修复）：
- momentum 近 3 年 A 股反向有效（IC −2.52 / OOS t=−3.85）⇒ `momentum.invert_signal=True`；
- price_volume_corr 是唯一独立第三源（IC −0.0308 / t=−3.62，ρ(·,momentum)=+0.285）；
- 守卫测试证明的交互约束（outputs/issue199_AC_proposal_2026-10-13.md §1）：
  ① ρ(momentum, triple_rise)=0.609 ⇒ **翻 mom 必须成对清零 triple_rise_weight**，
     否则两者在归一化 (mom+tr)/2 里互相抵消，区分度腰斩；
  ② 翻向后分数整体上移 ⇒ **min_total_score 必须同步抬高**（否则通过率 9.7%→87%，放宽选股 9 倍）；
  ③ pvc 的 IC 为负 ⇒ **启用 pvc 必须 invert_signal=True**（不反转等于赌反方向）；
  ④ `_has_*` 语义 ⇒ **权重写了必须 enabled**（只写权重不开关 = 幽灵维度）。

本文件把这些约束固化成 CI 断言：任何将来「只改一半」的配置都会在此失败。
"""

from __future__ import annotations

import pytest

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import load_thresholds


@pytest.fixture(scope="module")
def th():
    return load_thresholds()


def test_momentum_invert_pairs_with_triple_rise_removal(th) -> None:
    """① momentum.invert_signal=True 时 triple_rise_weight 必须为 0。

    实测（400 票、生产代码、composite 层）：只翻 mom 区分度 0.1504→0.0757（腰斩），
    因 ρ(mom,tr)=0.609 方向冲突；配对清零后区分度恢复（0.1498）。
    """
    if th.momentum.invert_signal:
        assert th.composite.triple_rise_weight == 0.0, (
            "momentum.invert_signal=True 但 triple_rise_weight != 0："
            "ρ(mom,tr)=0.609 会让翻正向的 mom 与 tr 互相抵消（区分度腰斩）。"
            "要么把 triple_rise_weight 归零，要么把 invert_signal 关回去"
        )


def test_pvc_weight_requires_enabled_and_wired(th) -> None:
    """② composite.price_volume_corr_weight>0 ⇒ pvc.enabled 必须为 True 且真的接线。

    `_has_price_volume_corr()` 要求 enabled AND weight>0 —— 只写权重不开关
    会静默退化成幽灵维度（与 volume.enabled=false + volume_weight=0.0 同类坑）。
    """
    if th.composite.price_volume_corr_weight > 0:
        assert th.price_volume_corr.enabled is True, (
            f"composite.price_volume_corr_weight="
            f"{th.composite.price_volume_corr_weight}>0 但 price_volume_corr.enabled"
            "=False —— 维度静默失效（写了权重没开因子）"
        )
        st = CompositeStrategy(StrategyConfig(name="composite", enabled=True), th)
        assert st._has_price_volume_corr() is True, (
            "权重>0 且 enabled=True，但 _has_price_volume_corr() 仍为 False —— 接线断了"
        )


def test_pvc_enabled_requires_invert(th) -> None:
    """③ pvc.enabled=True ⇒ pvc.invert_signal 必须为 True。

    pvc 的 IC 为负（实测 −0.0308 / t=−3.62，4 板块复核全负）⇒ 近 3 年反向有效；
    不反转等于赌反方向。实现必须是 1−score（阈值代码已保证，此处只查标志位）。
    """
    if th.price_volume_corr.enabled:
        assert th.price_volume_corr.invert_signal is True, (
            "price_volume_corr.enabled=True 但 invert_signal=False —— IC 为负的因子"
            "不反转等于系统性买反"
        )


def test_min_total_score_strictness_restored(th) -> None:
    """④ min_total_score 必须随方向纠正抬高（否则放宽选股）。

    基线门槛 0.4 对应旧配置（mom 正向 + tr=0.3）~10% 通过率；翻正+接线后
    分数整体上移，门槛须 > 0.4 才有意义。本断言锁「门槛确实被抬高」这个事实，
    具体数值由 runner 生产宇宙校准（test_strategies.py::test_load_thresholds
    断言当前值；校准后两处同步更新）。
    """
    if th.momentum.invert_signal and th.composite.triple_rise_weight == 0.0:
        assert th.composite.min_total_score > 0.4, (
            "mom 已翻正 + tr 已归零，但 min_total_score 仍 <= 0.4："
            "翻向后通过率会从 ~10% 跳到 ~90%（选股条件放宽 9 倍）"
        )
