"""守卫：#382 清零后的 triple_rise **生效权重**必须在 gate 报告里如实可见。

背景（2026-11-09 判读事故）：
`_apply_walkforward_grid_variant` 在 base `momentum.invert_signal=True` 时会把变体
声明的 `triple_rise_weight` 强制清零（#382 强制配对）。但 `report.md` 的变体表此前
渲染 `variant.triple_rise_weight`（**申请值**），于是 WF-B07 显示 `mom=0.2, tr=0.4`，
读者会以为它是「动量+三连涨」组合——实际跑的是 `mom(inv)+pvc(inherited)`，tr 从未参与。

本守卫断言：
1. `details["effective_triple_rise_weights"]` 与声明值不同时，该行 tr 单元格渲染
   **生效值 + `*` 星标**，且报告含解释脚注；
2. 生效值与声明值一致时不加星标、不加脚注（不制造噪音）；
3. tr 生效值为 0.0 的变体不得再显示成 0.3/0.4。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aqsp.cli import (  # noqa: E402
    _append_walkforward_grid_rows,
    _walkforward_grid_variants,
)


def _render(details: dict | None, profile: str = "stable_plus") -> str:
    variants = _walkforward_grid_variants(profile)
    rows = [(v, 0.3, 0.05, 20, 100) for v in variants]
    lines: list[str] = []
    _append_walkforward_grid_rows(
        lines, dsr=0.9, pbo=0.2, periods=20, rows=rows, details=details, test_days=146
    )
    return "\n".join(lines)


def _row(report: str, variant_id: str) -> list[str]:
    line = next(ln for ln in report.splitlines() if ln.startswith(f"| {variant_id} |"))
    return [c.strip() for c in line.strip("|").split("|")]


def test_zeroed_tr_weight_renders_effective_value_with_star() -> None:
    variants = _walkforward_grid_variants("stable_plus")
    declared = {v.variant_id: float(v.triple_rise_weight) for v in variants}
    # 模拟 #382：所有声明 tr>0 的变体生效值被清零
    effective = {vid: (0.0 if w > 0 else w) for vid, w in declared.items()}
    report = _render({"effective_triple_rise_weights": effective})

    zeroed = [vid for vid, w in declared.items() if w > 0]
    assert zeroed, "stable_plus 应至少有声明 tr>0 的变体（否则本守卫无意义）"
    for vid in zeroed:
        cells = _row(report, vid)
        assert cells[2] == "0.0*", (
            f"{vid} 声明 tr={declared[vid]:.1f} 但生效 0.0，表格应显示 `0.0*`，"
            f"实得 {cells[2]!r}——显示申请值会误导为 triple_rise 参与打分"
        )
    assert "强制清零" in report and "#382" in report, "必须有解释脚注"


def test_unchanged_tr_weight_has_no_star_or_footnote() -> None:
    variants = _walkforward_grid_variants("stable_plus")
    declared = {v.variant_id: float(v.triple_rise_weight) for v in variants}
    report = _render({"effective_triple_rise_weights": declared})
    for vid, w in declared.items():
        assert _row(report, vid)[2] == f"{w:.1f}", "未清零时 tr 单元格应保持原样"
    assert "强制清零" not in report
    assert "*" not in _row(report, "WF-001")[2]


def test_missing_details_keeps_legacy_rendering() -> None:
    """details=None（老报告路径）不得报错，也不得凭空加星标。"""
    report = _render(None)
    assert "| WF-001 |" in report
    assert "强制清零" not in report
