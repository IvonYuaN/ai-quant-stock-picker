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

import csv
import json
import math
import os

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


# ---------------------------------------------------------------------------
# 前端工件投影（build_verdict_artifact）
# 合成 probe CSV 行做投影，验证：regime 中文化 + 固定序、百分比/t 格式化、
# viable 逐 horizon 保守交集、conditioning 保守全真、缺 cond 行即 False。
# 零联网、不碰 db（重计算仍留 runner 探针）。
# ---------------------------------------------------------------------------
def _row(view: str, regime: str, h: int, mean: float, t: float,
         not_sig_neg: str) -> dict:
    """模拟 probe CSV 读回的字符串字段（regime 带机器前缀、数值存成字符串）。"""
    return {"view": view, "regime": regime, "horizon": str(h),
            "excess_mean": str(mean), "excess_t_nw": str(t), "not_sig_neg": not_sig_neg}


def _regime3(h: int, mean: float = 0.0, t: float = 0.5, ok: str = "True") -> list:
    return [_row("基线A_分regime", f"{r}_{c}", h, mean, t, ok)
            for r, c in (("bear", "熊"), ("sideways", "震荡"), ("bull", "牛"))]


def test_build_verdict_artifact_all_viable() -> None:
    rows = _regime3(10) + _regime3(20, mean=-0.01, t=-1.67)  # h=20 单 regime 显著负
    rows.append(_row("基线A_整体", "ALL", 10, 0.0001, 0.3, "True"))
    rows.append(_row("基线A_整体", "ALL", 20, -0.0005, -0.4, "True"))
    art = g.build_verdict_artifact(rows, as_of="2026-11-16")
    assert art["viable"] is True
    assert len(art["evidence"]) == 6
    # regime 机器前缀 → 中文，且按「熊/震荡/牛」固定序（不依赖 Unicode 码位）
    assert [e["regime"] for e in art["evidence"][:3]] == ["熊", "震荡", "牛"]
    assert art["evidence"][0]["excess"] == "+0.00%"
    assert art["as_of"] == "2026-11-16"
    assert art["dynamicSwitching"] is False  # 定位锁定：恒不动态切换


def test_build_verdict_artifact_h_fail_widens_to_false() -> None:
    # h=10 熊显著为负(1/3 容许) + h=20 熊显著为负 ⇒ 两 horizon 均过容许上限
    rows = (
        [_row("基线A_分regime", "bear_熊", 10, -0.05, -3.0, "False")]
        + [_row("基线A_分regime", f"{r}_{c}", 10, 0.0, 0.5, "True")
           for r, c in (("sideways", "震荡"), ("bull", "牛"))]
        + _regime3(20, mean=-0.05, t=-3.0, ok="False")
        + [_row("基线A_整体", "ALL", 10, -0.05, -3.0, "False")]
        + [_row("基线A_整体", "ALL", 20, -0.05, -3.0, "False")]
    )
    art = g.build_verdict_artifact(rows, as_of="x")
    assert art["viable"] is False  # h=10: 1 sig-neg ≤1/3 但整体 False ⇒ 不满足


def test_build_verdict_artifact_nan_formats_to_dash() -> None:
    r = _row("基线A_分regime", "bear_熊", 10, float("nan"), float("nan"), "True")
    art = g.build_verdict_artifact([r, _row("基线A_整体", "ALL", 10, 0.0, 0.5, "True")],
                                   as_of="x")
    assert art["evidence"][0]["excess"] == "—"
    assert art["evidence"][0]["t"] == "—"


def test_build_verdict_artifact_conditioning_consensus() -> None:
    rows = (
        _regime3(10)
        + [_row("基线A_整体", "ALL", 10, 0.0, 0.5, "True")]
        + [_row("条件化_方向×regime", f"{r}_{c}", 10, 0.0, 1.0, "True")
           for r, c in (("bear", "熊"), ("sideways", "震荡"), ("bull", "牛"))]
    )
    art = g.build_verdict_artifact(rows, as_of="x")
    assert art["conditioning_ok"] is True
    # 无 cond 行 ⇒ 保守判 False（不做动态切换的提示仍成立）
    art2 = g.build_verdict_artifact(_regime3(10) + [_row("基线A_整体", "ALL", 10, 0.0, 0.5, "True")],
                                    as_of="x")
    assert art2["conditioning_ok"] is False


# ---------------------------------------------------------------------------
# CLI 出口：--probe-csv + --emit-verdict 一键离线生成前端工件
# （runner 重算探针后，给一份 CSV 即可产 pit_cache/filter_verdict.json，不重算、不联网）
# ---------------------------------------------------------------------------
def _write_probe_csv(tmp, rows: list) -> str:
    """把 rows 写成探针 main() 实际落盘形状的 CSV（字段 = 探针 records 列的并集）。"""
    path = os.path.join(tmp, "probe.csv")
    # 探针 records 列并集（不同 view 缺的列留空，DictWriter 容错）
    cols = ["horizon", "view", "regime", "direction",
            "excess_mean", "excess_t_nw", "win_vs_pool", "n", "not_sig_neg", "spread_pp"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


def _full_records() -> list:
    """探针 main() 会写的完整 records：h=10/20 各（分regime + 整体 + 条件化）。"""
    rows = []
    for H in (10, 20):
        for r, c in (("bear", "熊"), ("sideways", "震荡"), ("bull", "牛")):
            rows.append(_row("基线A_分regime", f"{r}_{c}", H, 0.003 if H == 10 else -0.01,
                             0.5 if H == 10 else -1.67, "True"))
        rows.append(_row("基线A_整体", "ALL", H, 0.0001, 0.3, "True"))
        for r, c in (("bear", "熊"), ("sideways", "震荡"), ("bull", "牛")):
            for d in ("A_双反向(当前yaml)", "B_双正向(历史实验)"):
                rows.append(dict(_row("条件化_方向×regime", f"{r}_{c}", H, 0.0, 1.0, "True"),
                                 direction=d))
    return rows


def test_cli_emit_verdict_writes_artifact(tmp_path) -> None:
    p = _write_probe_csv(str(tmp_path), _full_records())
    out = str(tmp_path / "verdict.json")
    rc = g.main(["--probe-csv", p, "--emit-verdict", "--as-of", "2026-11-16", "--out", out])
    assert rc == 0
    doc = json.loads(open(out, encoding="utf-8").read())
    art = doc["verdict_artifact"]
    # 前端 FilterVerdict 字段齐全
    for k in ("viable", "baseline", "positioning", "criterion",
              "dynamicSwitching", "dynamicNote", "evidence", "as_of", "source"):
        assert k in art, f"verdict_artifact 缺字段 {k}"
    assert art["as_of"] == "2026-11-16"
    assert art["viable"] is True
    assert len(art["evidence"]) == 6, "h=10/20 × 3 regime = 6 行"
    # 固定序：每个 horizon 内 熊/震荡/牛
    assert [e["regime"] for e in art["evidence"][:3]] == ["熊", "震荡", "牛"]


def test_cli_without_emit_verdict_is_backward_compatible(tmp_path) -> None:
    p = _write_probe_csv(str(tmp_path), _full_records())
    out = str(tmp_path / "summary.json")
    rc = g.main(["--probe-csv", p, "--out", out])
    assert rc == 0
    doc = json.loads(open(out, encoding="utf-8").read())
    # 不加 --emit-verdict ⇒ 输出形状不变（有 criterion/baseline_overall，无 verdict_artifact）
    assert "criterion" in doc
    assert "probe_rows" in doc
    assert "verdict_artifact" not in doc, "向后兼容：无 flag 不得凭空多出 verdict_artifact"
