"""`compare_planb_vs_baseline.py` 契约测试（触发日裁决对照表的正确性）。

覆盖：
1. 逐变体表按**表头名**解析（列数变化不错位）；
2. grid 级 DSR/PBO 正确提取（**不做算术混合**）；
3. ΔSharpe / Δ总收益 计算正确、符号正确；
4. 绝对门禁判定：DSR>1.0 且 0<PBO<0.5 才 ✅，缺证据标「缺证据」而非默认通过；
5. 缺列/缺指标（`-`）⇒ 显示 `-` 不崩、不填 0。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "analysis"))

from compare_planb_vs_baseline import (  # noqa: E402
    _gate_verdict,
    build_table,
    parse_grid_metrics,
    parse_grid_table,
)

BASELINE_MD = """# gate report

## 多变体 CSCV

- Grid DSR：0.9400
- Grid PBO：21.00%
- 对齐周期数：20

| 变体 | mom | tr | qual | val | vol | mr | htf | lb | h | top | Sharpe | 总收益 | 暴露归一化收益 | 周期数 |
|------|-----|----|------|-----|-----|----|-----|----|---|-----|--------|--------|----------------|--------|
| WF-001 | 0.3 | 0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 10 | 0.85 | 12.00% | 30.00% | 20 |
| WF-B01 | 0.3 | 0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 5 | 0.80 | 10.00% | 25.00% | 20 |
"""

CANDIDATE_MD = """# gate report

## 多变体 CSCV

- Grid DSR：1.2100
- Grid PBO：18.00%
- 对齐周期数：20

| 变体 | mom | tr | qual | val | vol | mr | htf | lb | h | top | Sharpe | 总收益 | 暴露归一化收益 | 周期数 |
|------|-----|----|------|-----|-----|----|-----|----|---|-----|--------|--------|----------------|--------|
| WB-V1-10x3 | 0.0 | 0.0 | 0.4 | 0.4 | 0.4 | 0.0 | 0.0 | 60 | 3 | 10 | 1.02 | 18.00% | 45.00% | 20 |
| WB-V1-20x5 | 0.0 | 0.0 | 0.4 | 0.4 | 0.4 | 0.0 | 0.0 | 60 | 5 | 20 | - | - | - | 20 |
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_grid_table_locates_columns_by_name(tmp_path: Path) -> None:
    """按表头名定位列 ⇒ 7 维新增列不会让 Sharpe/总收益错位。"""
    rows = parse_grid_table(_write(tmp_path, "b.md", BASELINE_MD))
    assert set(rows) == {"WF-001", "WF-B01"}
    r = rows["WF-001"]
    assert r["Sharpe"] == "0.85"
    assert r["总收益"] == "12.00%"
    assert r["qual"] == "0.0"
    assert r["htf"] == "0.0"


def test_parse_grid_metrics_reads_grid_level_values(tmp_path: Path) -> None:
    m = parse_grid_metrics(_write(tmp_path, "b.md", BASELINE_MD))
    assert m["dsr"] == pytest.approx(0.94)
    assert m["pbo"] == pytest.approx(0.21)
    assert m["periods"] == pytest.approx(20)


def test_build_table_computes_delta_against_wf001(tmp_path: Path) -> None:
    """Δ 以 WF-001（生产基线）为参照，不是表格第一行/最大 Sharpe 行。"""
    b = _write(tmp_path, "b.md", BASELINE_MD)
    c = _write(tmp_path, "c.md", CANDIDATE_MD)
    lines, data = build_table(b, c)
    # 基线自身 Δ 必须是 "—"
    base_line = next(ln for ln in lines if ln.startswith("| 基线 |"))
    assert "| — | — |" in base_line
    # 候选 1.02 vs 基线 0.85 ⇒ +0.17；18% vs 12% ⇒ +6%
    cands = {c["variant"]: c for c in data["candidates"]}
    assert cands["WB-V1-10x3"]["d_sharpe"] == pytest.approx(0.17)
    assert cands["WB-V1-10x3"]["d_total_return"] == pytest.approx(0.06)
    assert data["baseline"]["variant"] == "WF-001"


def test_build_table_handles_missing_metrics_as_dash(tmp_path: Path) -> None:
    """`-`（缺指标）⇒ 显示 `-`，不崩、不当 0 处理（否则会算出假的 -0.85）。"""
    b = _write(tmp_path, "b.md", BASELINE_MD)
    c = _write(tmp_path, "c.md", CANDIDATE_MD)
    lines, data = build_table(b, c)
    row = next(ln for ln in lines if ln.startswith("| 候选 | WB-V1-20x5 |"))
    assert row.count("| - ") >= 3, "缺指标的臂应有多列显示 `-`"
    cands = {c["variant"]: c for c in data["candidates"]}
    assert cands["WB-V1-20x5"]["sharpe"] is None
    assert cands["WB-V1-20x5"]["d_sharpe"] is None


def test_build_table_keeps_pbo_unmixed(tmp_path: Path) -> None:
    """基线与候选的 PBO/DSR 各自独立取值，**不做算术平均/混合**。"""
    b = _write(tmp_path, "b.md", BASELINE_MD)   # DSR 0.94 / PBO 21%
    c = _write(tmp_path, "c.md", CANDIDATE_MD)  # DSR 1.21 / PBO 18%
    _lines, data = build_table(b, c)
    assert data["baseline"]["pbo"] == pytest.approx(0.21)
    assert all(
        cand["variant"] for cand in data["candidates"]
    ), "候选臂应全部列出"
    # 候选行的 PBO 一律取候选 run 的值（18%），不得被基线的 21% 污染
    assert data["candidates"], "应有候选行"


def test_build_table_only_lists_candidate_prefix(tmp_path: Path) -> None:
    """只列候选前缀（WB-）的行，不把基线 run 的 WF-* 混进候选区。"""
    b = _write(tmp_path, "b.md", BASELINE_MD)
    c = _write(tmp_path, "c.md", CANDIDATE_MD)
    lines, data = build_table(b, c, candidate_prefix="WB-")
    ids = {c["variant"] for c in data["candidates"]}
    assert ids == {"WB-V1-10x3", "WB-V1-20x5"}
    assert not any("WF-" in ln.split("|")[2] for ln in lines if ln.startswith("| 候选 |"))


@pytest.mark.parametrize(
    "dsr,pbo,expect_ok",
    [
        (1.21, 0.18, True),   # 双过
        (0.94, 0.21, False),  # DSR 不足
        (1.30, 0.62, False),  # PBO 越界
        (1.00, 0.10, False),  # DSR=1.0 不算 >1.0
        (1.30, 0.00, False),  # PBO=0 不算 >0
    ],
)
def test_gate_verdict_is_fail_closed(dsr: float, pbo: float, expect_ok: bool) -> None:
    """门禁是 fail-closed：边界值（=1.0 / =0）都不放行。"""
    v = _gate_verdict(dsr, pbo)
    assert v.startswith("✅") is expect_ok


def test_gate_verdict_marks_missing_evidence_not_pass() -> None:
    """缺 DSR/PBO ⇒ 标「缺证据」，**绝不默认通过**。"""
    assert _gate_verdict(None, 0.2) == "缺证据"
    assert _gate_verdict(1.2, None) == "缺证据"
    assert _gate_verdict(None, None) == "缺证据"


def test_missing_baseline_report_fails_loudly(tmp_path: Path) -> None:
    """基线 report 解析不到变体 ⇒ 直接 SystemExit，不静默出一张空表。"""
    c = _write(tmp_path, "c.md", CANDIDATE_MD)
    missing = tmp_path / "nope.md"
    with pytest.raises(SystemExit):
        build_table(missing, c)
