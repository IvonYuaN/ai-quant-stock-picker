"""T3 双窗口对比的**分层口径**（issue #194）。

原判据 = 「8 个 htf_mr 变体里的**最佳** vs 单个基线变体 WF-001」，两处缺陷：

1. **best-of-8 vs single** —— 不对称，白送候选臂一个选择性偏差；
2. 该「最佳」是 `WF-H07`(**h=10**)，而基线 `WF-001` 是 **h=3** —— **跨了 horizon 轴**。

实测后果（2026-09-23）：3y 窗口据此判「改善」（+0.29 vs −0.63），
按同 `h` 分层后两臂其实**无差别**（ΔSharpe=+0.039，小于同层离散度的 1/4）
⇒ 那是**持有期效应**，不是因子族效应。

本文件用两窗口的**真实报告读数**做 fixture 守住分层口径，并专门构造一个
「最佳变体在高 horizon、同层却更差」的算例，确保跨轴混杂**不会被复现**。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "compare_t3_dual_window", PROJECT_ROOT / "scripts" / "compare_t3_dual_window.py"
)
assert _SPEC is not None and _SPEC.loader is not None
mod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mod
_SPEC.loader.exec_module(mod)


# --------------------------------------------------------------------------- #
# 真实读数 fixture（取自 outputs/gate_run_*/report.md，2026-09-23 实测）
# 列：变体, mom, tr, lb, h, top, Sharpe, 总收益, 暴露归一化收益, 周期数
# --------------------------------------------------------------------------- #
_HEADER = (
    "| 变体 | mom | tr | lb | h | top | Sharpe | 总收益 | 暴露归一化收益 | 周期数 |\n"
    "|------|-----|----|----|---|-----|--------|--------|----------------|--------|\n"
)

# 基线臂（stable_plus：mom=0.3 tr=0.3），3y 窗口 20 期
_WF001_3Y_ROWS = """| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | -0.63 | -12.23% | -122.27% | 20 |
| WF-B01 | 0.3 | 0.3 | 60 | 3 | 5 | -0.78 | -15.05% | -150.46% | 20 |
| WF-B02 | 0.3 | 0.3 | 60 | 3 | 20 | -0.56 | -9.34% | -93.42% | 20 |
| WF-B04 | 0.3 | 0.3 | 120 | 3 | 10 | -0.80 | -12.97% | -129.75% | 20 |
| WF-B07 | 0.2 | 0.4 | 40 | 5 | 5 | -0.63 | -15.58% | -93.48% | 20 |
| WF-B08 | 0.4 | 0.2 | 100 | 2 | 15 | -0.22 | -2.67% | -40.04% | 20 |
| WF-V01 | 0.3 | 0.3 | 60 | 3 | 10 | -1.03 | -14.02% | -140.24% | 20 |
| WF-MR1 | 0.3 | 0.3 | 20 | 3 | 10 | -0.55 | -8.04% | -80.41% | 20 |
"""

# 候选臂（htf_mr：mom=0 tr=0），3y 窗口 20 期
_HTF_MR_3Y_ROWS = """| WF-H01 | 0.0 | 0.0 | 60 | 3 | 10 | -0.72 | -11.82% | -118.21% | 20 |
| WF-H02 | 0.0 | 0.0 | 60 | 3 | 5 | -0.65 | -11.46% | -114.63% | 20 |
| WF-H03 | 0.0 | 0.0 | 60 | 3 | 20 | -0.62 | -9.53% | -95.30% | 20 |
| WF-H04 | 0.0 | 0.0 | 20 | 3 | 10 | -0.72 | -11.82% | -118.21% | 20 |
| WF-H05 | 0.0 | 0.0 | 120 | 3 | 10 | -0.72 | -11.82% | -118.21% | 20 |
| WF-H06 | 0.0 | 0.0 | 60 | 1 | 10 | -0.32 | -2.50% | -75.10% | 20 |
| WF-H07 | 0.0 | 0.0 | 60 | 10 | 10 | 0.29 | 6.67% | 20.02% | 20 |
| WF-H08 | 0.0 | 0.0 | 40 | 5 | 5 | -0.39 | -8.34% | -50.04% | 20 |
"""

# 基线臂 5y 窗口 36 期
_WF001_5Y_ROWS = """| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 1.10 | 43.99% | 439.89% | 36 |
| WF-B01 | 0.3 | 0.3 | 60 | 3 | 5 | 0.89 | 47.96% | 479.60% | 36 |
| WF-B02 | 0.3 | 0.3 | 60 | 3 | 20 | 1.30 | 42.50% | 425.02% | 36 |
| WF-B04 | 0.3 | 0.3 | 120 | 3 | 10 | 0.91 | 34.03% | 340.33% | 36 |
| WF-B07 | 0.2 | 0.4 | 40 | 5 | 5 | 0.48 | 25.60% | 153.57% | 36 |
| WF-B08 | 0.4 | 0.2 | 100 | 2 | 15 | 1.14 | 34.91% | 523.65% | 36 |
| WF-V01 | 0.3 | 0.3 | 60 | 3 | 10 | 1.10 | 35.95% | 359.47% | 36 |
| WF-MR1 | 0.3 | 0.3 | 20 | 3 | 10 | 0.77 | 18.66% | 186.56% | 36 |
"""

# 候选臂 5y 窗口 36 期
_HTF_MR_5Y_ROWS = """| WF-H01 | 0.0 | 0.0 | 60 | 3 | 10 | 0.57 | 13.89% | 138.87% | 36 |
| WF-H02 | 0.0 | 0.0 | 60 | 3 | 5 | 0.69 | 18.57% | 185.72% | 36 |
| WF-H03 | 0.0 | 0.0 | 60 | 3 | 20 | 0.65 | 15.19% | 151.88% | 36 |
| WF-H04 | 0.0 | 0.0 | 20 | 3 | 10 | 0.57 | 13.89% | 138.87% | 36 |
| WF-H05 | 0.0 | 0.0 | 120 | 3 | 10 | 0.57 | 13.89% | 138.87% | 36 |
| WF-H06 | 0.0 | 0.0 | 60 | 1 | 10 | 0.42 | 4.28% | 128.28% | 36 |
| WF-H07 | 0.0 | 0.0 | 60 | 10 | 10 | 0.44 | 16.62% | 49.86% | 36 |
| WF-H08 | 0.0 | 0.0 | 40 | 5 | 5 | -0.22 | -7.99% | -47.94% | 36 |
"""


def _write_report(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / "report.md"
    path.write_text("# gate\n\n## 多变体 CSCV\n\n" + _HEADER + rows, encoding="utf-8")
    return path


@pytest.fixture()
def wf001_3y(tmp_path: Path) -> dict:
    return mod.parse_report_variants(_write_report(tmp_path, _WF001_3Y_ROWS))


@pytest.fixture()
def htf_3y(tmp_path: Path) -> dict:
    return mod.parse_report_variants(_write_report(tmp_path, _HTF_MR_3Y_ROWS))


@pytest.fixture()
def wf001_5y(tmp_path: Path) -> dict:
    return mod.parse_report_variants(_write_report(tmp_path, _WF001_5Y_ROWS))


@pytest.fixture()
def htf_5y(tmp_path: Path) -> dict:
    return mod.parse_report_variants(_write_report(tmp_path, _HTF_MR_5Y_ROWS))


# --------------------------------------------------------------------------- #
# 解析：必须捕获**每一个**变体行，臂归属由目录决定而非名字前缀
# --------------------------------------------------------------------------- #
def test_parser_captures_every_variant_row_not_only_baseline_and_htf(
    wf001_3y: dict, htf_3y: dict
) -> None:
    """回归：早期版本只认 `WF-001|WF-H0x`，基线臂被解析成 n=1，
    「臂均值对比」退化成「单点 vs 单点」。"""
    assert set(wf001_3y) == {
        "WF-001",
        "WF-B01",
        "WF-B02",
        "WF-B04",
        "WF-B07",
        "WF-B08",
        "WF-V01",
        "WF-MR1",
    }
    assert set(htf_3y) == {f"WF-H0{i}" for i in range(1, 9)}


def test_parser_puts_axis_columns_in_axes_and_excludes_metrics(
    wf001_3y: dict,
) -> None:
    axes = wf001_3y["WF-001"]["axes"]
    assert axes == {"mom": "0.3", "tr": "0.3", "lb": "60", "h": "3", "top": "10"}
    # 指标列绝不能混进 axes（`暴露归一化收益` 含「收益」、`周期数` 含「周期」）
    for bad in ("Sharpe", "总收益", "暴露归一化收益", "周期数"):
        assert bad not in axes


def test_parser_reads_metrics_into_numeric_fields(wf001_3y: dict) -> None:
    row = wf001_3y["WF-001"]
    assert row["sharpe"] == pytest.approx(-0.63)
    assert row["total_return_pct"] == pytest.approx(-12.23)
    assert row["periods"] == pytest.approx(20)


def test_is_axis_column_rejects_metric_headers() -> None:
    assert mod._is_axis_column("h")
    assert mod._is_axis_column("top")
    assert not mod._is_axis_column("变体")
    assert not mod._is_axis_column("Sharpe")
    assert not mod._is_axis_column("总收益")
    assert not mod._is_axis_column("暴露归一化收益")
    assert not mod._is_axis_column("周期数")


# --------------------------------------------------------------------------- #
# 分层与统计
# --------------------------------------------------------------------------- #
def test_stratify_variants_filters_by_axis_value(wf001_3y: dict) -> None:
    layer = mod.stratify_variants(wf001_3y, "h", "3")
    assert set(layer) == {
        "WF-001",
        "WF-B01",
        "WF-B02",
        "WF-B04",
        "WF-V01",
        "WF-MR1",
    }
    assert "WF-B07" not in layer  # h=5
    assert "WF-B08" not in layer  # h=2


def test_arm_stats_reports_n_mean_and_extremes(wf001_3y: dict) -> None:
    stats = mod.arm_stats(mod.stratify_variants(wf001_3y, "h", "3"))
    assert stats["n"] == 6
    assert stats["mean_sharpe"] == pytest.approx(-0.725, abs=1e-9)
    assert stats["min_sharpe"] == pytest.approx(-1.03)
    assert stats["max_sharpe"] == pytest.approx(-0.55)
    assert stats["mean_return"] == pytest.approx(-11.9416666, abs=1e-6)


# --------------------------------------------------------------------------- #
# 主判据：同层臂均值对比
# --------------------------------------------------------------------------- #
def test_stratified_verdict_three_year_is_flat_within_noise(
    wf001_3y: dict, htf_3y: dict
) -> None:
    """3y：两臂同层均值 −0.725 vs −0.686 ⇒ ΔSharpe=+0.039，落在噪声带内 → 持平。"""
    verdict = mod.stratified_verdict(wf001_3y, htf_3y)
    assert verdict is not None
    assert verdict["axis"] == "h"
    assert verdict["layer"] == "3"
    assert verdict["base"]["n"] == 6
    assert verdict["cand"]["n"] == 5
    assert verdict["base"]["mean_sharpe"] == pytest.approx(-0.725, abs=1e-9)
    assert verdict["cand"]["mean_sharpe"] == pytest.approx(-0.686, abs=1e-9)
    assert verdict["delta_sharpe"] == pytest.approx(0.039, abs=1e-9)
    # 符号方向是「改善」，但幅度落在噪声带内 ⇒ 降级为持平
    assert verdict["direction_raw"] == "改善"
    assert verdict["within_noise"] is True
    assert verdict["direction"] == "持平"


def test_stratified_verdict_five_year_is_real_regression(
    wf001_5y: dict, htf_5y: dict
) -> None:
    """5y：同层均值 +1.012 vs +0.610 ⇒ ΔSharpe=−0.402，远超噪声带 → 退步。"""
    verdict = mod.stratified_verdict(wf001_5y, htf_5y)
    assert verdict is not None
    assert verdict["base"]["n"] == 6
    assert verdict["cand"]["n"] == 5
    assert verdict["base"]["mean_sharpe"] == pytest.approx(1.0116666, abs=1e-6)
    assert verdict["cand"]["mean_sharpe"] == pytest.approx(0.610, abs=1e-9)
    assert verdict["delta_sharpe"] == pytest.approx(-0.4016666, abs=1e-6)
    assert verdict["within_noise"] is False
    assert verdict["direction"] == "退步"


def test_stratified_verdict_picks_the_baseline_layer_not_the_best_layer(
    wf001_3y: dict, htf_3y: dict
) -> None:
    """层必须由**基线**决定（h=3），不能跟着候选臂的最佳变体跑到 h=10。"""
    verdict = mod.stratified_verdict(wf001_3y, htf_3y)
    assert verdict is not None
    assert verdict["layer"] == "3"
    assert "WF-H07" not in verdict["cand_layer_variants"]


def test_stratified_verdict_does_not_reproduce_cross_horizon_confound() -> None:
    """核心回归（#194）：候选臂最佳变体在高 horizon，同层却更差。

    朴素口径会判「改善」（best 0.90 > 基线 0.10），分层口径必须判「退步」。
    """
    base_arm = {
        "WF-001": {"sharpe": 0.10, "total_return_pct": 1.0, "axes": {"h": "3"}},
        "WF-B01": {"sharpe": 0.05, "total_return_pct": 0.5, "axes": {"h": "3"}},
    }
    cand_arm = {
        "WF-H01": {"sharpe": -0.40, "total_return_pct": -4.0, "axes": {"h": "3"}},
        "WF-H02": {"sharpe": -0.30, "total_return_pct": -3.0, "axes": {"h": "3"}},
        # 唯一亮点在别的 horizon 上 —— 旧判据正是拿它当「最佳」
        "WF-H07": {"sharpe": 0.90, "total_return_pct": 9.0, "axes": {"h": "10"}},
    }

    naive = mod.verdict_for_window(base_arm["WF-001"], cand_arm)
    assert naive["best"] == "WF-H07"
    assert mod.direction(naive) == "改善"

    stratified = mod.stratified_verdict(base_arm, cand_arm)
    assert stratified is not None
    assert stratified["layer"] == "3"
    assert stratified["cand"]["n"] == 2
    assert "WF-H07" not in stratified["cand_layer_variants"]
    assert stratified["direction"] == "退步"


def test_stratified_verdict_returns_none_when_layer_has_no_peer() -> None:
    base_arm = {"WF-001": {"sharpe": 0.1, "total_return_pct": 1.0, "axes": {"h": "3"}}}
    cand_arm = {"WF-H07": {"sharpe": 0.9, "total_return_pct": 9.0, "axes": {"h": "10"}}}
    assert mod.stratified_verdict(base_arm, cand_arm) is None


def test_stratified_verdict_returns_none_without_baseline_variant() -> None:
    cand_arm = {"WF-H01": {"sharpe": 0.1, "total_return_pct": 1.0, "axes": {"h": "3"}}}
    assert mod.stratified_verdict({}, cand_arm) is None


def test_magnitude_note_flags_noise_only_for_the_flat_window(
    wf001_3y: dict, htf_3y: dict, wf001_5y: dict, htf_5y: dict
) -> None:
    note3 = mod.magnitude_note(mod.stratified_verdict(wf001_3y, htf_3y))
    note5 = mod.magnitude_note(mod.stratified_verdict(wf001_5y, htf_5y))
    assert "接近噪声" in note3
    assert note5 == ""


# --------------------------------------------------------------------------- #
# 方向标签与判决
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("ds", "dr", "expected"),
    [
        (0.5, 3.0, "改善"),
        (-0.5, -3.0, "退步"),
        (0.5, -3.0, "持平"),
        (-0.5, 3.0, "持平"),
        (None, -3.0, "退步"),
        (0.5, None, "改善"),
        (None, None, None),
    ],
)
def test_direction_from_delta_sign_rules(
    ds: float | None, dr: float | None, expected: str | None
) -> None:
    assert mod.direction_from_delta(ds, dr) == expected


def test_decide_scheme_requires_same_direction_in_both_windows() -> None:
    assert "成立" in mod.decide_scheme("改善", "改善")
    assert "不成立" in mod.decide_scheme("退步", "退步")
    assert "不判方案 A 成立" in mod.decide_scheme("改善", "退步")
    assert "暂不可判" in mod.decide_scheme(None, "退步")


def test_decide_scheme_both_flat_does_not_claim_success() -> None:
    verdict = mod.decide_scheme("持平", "持平")
    assert verdict.startswith("⚪")
    assert "不判方案 A 成立" in verdict
    assert "✅" not in verdict


def test_real_two_window_verdict_is_not_scheme_a(
    wf001_3y: dict, htf_3y: dict, wf001_5y: dict, htf_5y: dict
) -> None:
    """端到端：真实两窗口读数 ⇒ 3y 持平 / 5y 退步 ⇒ 不判方案 A 成立。"""
    s3 = mod.stratified_verdict(wf001_3y, htf_3y)
    s5 = mod.stratified_verdict(wf001_5y, htf_5y)
    assert s3 is not None and s5 is not None
    scheme = mod.decide_scheme(s3["direction"], s5["direction"])
    assert s3["direction"] == "持平"
    assert s5["direction"] == "退步"
    assert "不判方案 A 成立" in scheme
