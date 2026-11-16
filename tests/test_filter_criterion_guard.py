"""`scripts/research/filter_criterion_guard.py` 判据守卫回归。

分层（红线）：
- **重计算**（全市场面板取逐截面超额）留在 runner 的 `regime_robustness_probe.py`；
- 本测试只钉**判据逻辑**（给定超额序列 → 三轴判决），合成数据、秒级、零联网。

钉的东西（未来改判据先被抓）：
- `nw_t`/`acf_lag` 统计原语口径（n<2→nan、零序列 se 落 floor→t=0、强正→大正）；
- regime / yearly / conditioning 三轴判决的生死边界（CRIT_T=1.96、MIN_SECTIONS=5）；
- `verdict` 汇总（筛选器 = regime 轴 viable，恒不做动态切换）。

确定性构造说明：常数零序列 = 「中性组」（t=0，不显著为负）；常数 -0.05 =
「显著负组」（se 落 1e-18 floor，t 巨负）。避免用带噪序列（t 随 seed 飘会 flaky）。
"""
from __future__ import annotations

import math

import numpy as np

from scripts.research import filter_criterion_guard as g

def zero(n: int) -> np.ndarray:
    """中性组：mean=0 ⇒ t=0（不显著为负）。"""
    return np.full(n, 0.0)


def big_neg(n: int) -> np.ndarray:
    """显著负组：se 落 1e-18 floor，t 巨负（not_sig_neg=False）。"""
    return np.full(n, -0.05)


# ---------------------------------------------------------------------------
# 统计原语
# ---------------------------------------------------------------------------
def test_nw_t_too_few_points_is_nan() -> None:
    assert math.isnan(g.nw_t(np.array([1.0]), 5))
    assert math.isnan(g.nw_t(np.array([]), 5))


def test_nw_t_zero_series_is_zero() -> None:
    # 常数零序列：var=0，se 落 1e-18 floor，mean=0 ⇒ t=0.0（非 nan）
    assert g.nw_t(np.full(40, 0.0), lag=5) == 0.0


def test_nw_t_positive_signal_significant() -> None:
    rng = np.random.default_rng(7)
    arr = np.full(60, 0.02) + rng.normal(0, 0.001, 60)
    assert g.nw_t(arr, lag=3) > 3.0


def test_acf_lag_bounds() -> None:
    rng = np.random.default_rng(1)
    arr = rng.normal(0, 1, 80)
    assert 1 <= g.acf_lag(arr, cap=20) <= 20
    assert g.acf_lag(np.full(5, 1.0), cap=20) == 1  # 短序列退化


def test_effective_lag_matches_probe_rule() -> None:
    vals = np.full(30, 0.01)
    assert g.effective_lag(vals, step=5, horizon=10, snap_len=100) >= 1
    assert g.effective_lag(vals, step=5, horizon=20, snap_len=100) >= math.ceil(20 / 5)


# ---------------------------------------------------------------------------
# 单组判读
# ---------------------------------------------------------------------------
def test_group_judgment_not_sig_neg_bound() -> None:
    j_neg = g.group_judgment(big_neg(40), lag=2)
    assert j_neg["excess_t_nw"] < -g.CRIT_T
    assert j_neg["not_sig_neg"] is False

    j_neu = g.group_judgment(zero(40), lag=2)
    assert j_neu["excess_t_nw"] == 0.0
    assert j_neu["not_sig_neg"] is True


# ---------------------------------------------------------------------------
# regime 轴
# ---------------------------------------------------------------------------
def test_judge_regime_viable_when_most_regimes_not_sig_neg() -> None:
    overall = np.concatenate([zero(30) for _ in range(3)])
    res = g.judge_regime({"bear": zero(30), "sideways": zero(30), "bull": zero(30)},
                         overall, step=5, horizon=20, snap_len=90)
    assert res["viable"] is True
    assert res["n_sig_neg"] == 0
    assert res["n_reported"] == 3


def test_judge_regime_not_viable_when_majority_sig_neg() -> None:
    overall = np.concatenate([big_neg(30) for _ in range(3)])
    res = g.judge_regime({"bear": big_neg(30), "sideways": big_neg(30), "bull": big_neg(30)},
                         overall, step=5, horizon=20, snap_len=90)
    assert res["viable"] is False
    assert res["n_sig_neg"] == 3


def test_judge_regime_small_group_not_reported() -> None:
    # tiny 组 n=3 < MIN_SECTIONS=5 ⇒ 不判（不拉崩 viable）
    res = g.judge_regime({"bear": zero(40), "tiny": big_neg(3)}, zero(40),
                         step=5, horizon=20, snap_len=43)
    assert res["per_regime"]["tiny"]["reported"] is False
    assert res["n_reported"] == 1
    assert res["viable"] is True


# ---------------------------------------------------------------------------
# 年度轴
# ---------------------------------------------------------------------------
def test_judge_yearly_stable() -> None:
    res = g.judge_yearly({"2025": zero(30), "2026": zero(30)}, overall_t=0.0,
                         step=5, horizon=20, snap_len=60)
    assert res["year_stable"] is True
    assert res["n_sig_neg"] == 0


def test_judge_yearly_unstable_when_majority_sig_neg() -> None:
    res = g.judge_yearly({"2024": big_neg(30), "2025": big_neg(30), "2026": big_neg(30)},
                         overall_t=-5.0, step=5, horizon=20, snap_len=90)
    assert res["year_stable"] is False
    assert res["n_sig_neg"] == 3


# ---------------------------------------------------------------------------
# 行情条件化轴（方向漂移 ⇒ 不做动态切换的依据）
# ---------------------------------------------------------------------------
def test_judge_conditioning_direction_drift_fails() -> None:
    res = g.judge_conditioning(
        {"bear": {"A_双反向(当前yaml)": zero(30), "B_双正向(历史实验)": big_neg(30)}},
        step=5, horizon=20, snap_len=90,
    )
    assert res["cond_pass"] is False
    assert res["detail"]["bear"]["ok"] is False


def test_judge_conditioning_all_neutral_passes() -> None:
    res = g.judge_conditioning(
        {"bear": {"A_双反向(当前yaml)": zero(30), "B_双正向(历史实验)": zero(30)}},
        step=5, horizon=20, snap_len=90,
    )
    assert res["cond_pass"] is True


def test_judge_conditioning_insufficient_sections_fails() -> None:
    res = g.judge_conditioning({"bull": {"A_双反向(当前yaml)": zero(3)}},
                               step=5, horizon=20, snap_len=30)
    assert res["cond_pass"] is False
    assert res["detail"]["bull"]["reason"] == "截面不足"


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def test_verdict_summary() -> None:
    overall = np.concatenate([zero(30) for _ in range(3)])
    regime = g.judge_regime({"bear": zero(30), "sideways": zero(30), "bull": zero(30)},
                            overall, step=5, horizon=20, snap_len=90)
    yearly = g.judge_yearly({"2025": zero(30), "2026": zero(30)}, regime["overall_t_nw"],
                            step=5, horizon=20, snap_len=90)
    cond = g.judge_conditioning({"bear": {"A_双反向(当前yaml)": zero(30)}},
                                step=5, horizon=20, snap_len=90)
    v = g.verdict(regime, yearly, cond)
    assert v["filter_viable"] is True
    assert v["do_dynamic_switching"] is False  # 定位锁定：恒不动态切换
