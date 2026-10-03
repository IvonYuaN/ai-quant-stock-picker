"""守卫：gate 逐变体报告表必须让 planb 候选的**真实 7 维权重**可见。

背景（2026-10-03 判读障碍实证）：
`_append_walkforward_grid_rows` 原来只渲染 `momentum_weight` / `triple_rise_weight`
两列，而方案 B 候选（`planb_v1/v2/v3`）的真实权重全部在第三个字段
`composite_weights`（7 维）里，且 planb 分支把 `momentum_weight`/`triple_rise_weight`
**都写成 0.0**。

后果：V1 / V2 / V3 三行在报告里 `mom`/`tr` 两列**完全相同**（实测全为 0.0），
判读者只能靠 `variant_id`（`WB-V1-10x3` 等）反推是哪个候选、7 维到底是什么
⇒ 触发日「机械判读」落不了地。

本守卫（纯渲染函数调用，零副作用、不跑回测）断言：
1. 表头含 7 维全部列（mom/tr/qual/val/vol/mr/htf）；
2. planb 变体行里能**看到它自己的 7 维权重**（不只 mom/tr）；
3. 报告里有「必须看这 5 列」的判读提示（避免下次又被当成噪音列删掉）；
4. 旧 2 维 mix 变体（`composite_weights=None`）新增五列渲染为 0.0 而非错位/报错。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aqsp.cli import (  # noqa: E402
    _PLANB_VARIANTS,
    _append_walkforward_grid_rows,
    _walkforward_grid_variants,
)

PLANB_PROFILES = ("planb_v1", "planb_v2", "planb_v3")
EXPECTED_HEADER = (
    "| 变体 | mom | tr | qual | val | vol | mr | htf | lb | h | top | "
    "Sharpe | 总收益 | 暴露归一化收益 | 周期数 |"
)


def _render(profile: str) -> str:
    variants = _walkforward_grid_variants(profile)
    rows = [(v, 0.85, 0.12, 20, 200) for v in variants]
    lines: list[str] = []
    _append_walkforward_grid_rows(
        lines,
        dsr=0.94,
        pbo=0.21,
        periods=20,
        rows=rows,
        details=None,
        test_days=146,
    )
    return "\n".join(lines)


@pytest.mark.parametrize("profile", PLANB_PROFILES)
def test_grid_report_header_shows_all_seven_dims(profile: str) -> None:
    """表头必须含 7 维全部列（含 qual/val/vol/mr/htf）。"""
    assert EXPECTED_HEADER in _render(profile), (
        "逐变体表头缺 7 维列（qual/val/vol/mr/htf）—— "
        "planb 候选的 mom/tr 恒为 0.0，不加这几列 V1/V2/V3 在报告里长得一样"
    )


@pytest.mark.parametrize("profile", PLANB_PROFILES)
def test_planb_row_exposes_its_own_seven_dim_weights(profile: str) -> None:
    """planb 变体行里必须能看到该候选自己的 7 维权重（qual/val/vol/mr/htf 五列）。

    注意 `mom`/`tr` 两列渲染的是 dataclass 的 `momentum_weight`/`triple_rise_weight`
    （planb 分支下**恒为 0.0**，7 维里的 mom/tr 走 `composite_weights`），所以本守卫
    只断言新增的五列——它们才是 planb 变体之间**唯一**有区别的权重。
    """
    report = _render(profile)
    expected = _PLANB_VARIANTS[profile][0].composite_weights
    assert expected is not None
    _mom, qual, val, vol, mr_w, _tr, htf = expected
    # variant_id 形如 WB-V1-10x3（profile 名是 planb_v1 ⇒ 取后两段 V1）
    tag = profile.split("_")[1].upper()  # 'V1'
    first_row = next(ln for ln in report.splitlines() if ln.startswith(f"| WB-{tag}-"))
    cells = [c.strip() for c in first_row.strip("|").split("|")]
    # 列序：变体 mom tr qual val vol mr htf lb h top ...
    assert cells[3] == f"{qual:.1f}"
    assert cells[4] == f"{val:.1f}"
    assert cells[5] == f"{vol:.1f}"
    assert cells[6] == f"{mr_w:.1f}"
    assert cells[7] == f"{htf:.1f}"


def test_three_candidates_are_distinguishable_by_five_new_cols() -> None:
    """V1/V2/V3 三行必须在这五列上**互不相同**（否则报告里分不出候选）。"""
    signatures = set()
    for profile in PLANB_PROFILES:
        report = _render(profile)
        tag = profile.split("_")[1].upper()
        row = next(ln for ln in report.splitlines() if ln.startswith(f"| WB-{tag}-"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        signatures.add(tuple(cells[3:8]))  # qual/val/vol/mr/htf
    assert len(signatures) == 3, (
        f"V1/V2/V3 在 qual/val/vol/mr/htf 上应互不相同，实得 {signatures}"
    )


@pytest.mark.parametrize("profile", PLANB_PROFILES)
def test_grid_report_explains_which_columns_are_authoritative(profile: str) -> None:
    """报告必须说明「判读 planb 要看 7 维列」，否则这五列会被当噪音删掉。"""
    report = _render(profile)
    assert "7 维列说明" in report
    assert "必须看这 5 列" in report


def test_legacy_two_dim_mix_renders_five_new_cols_as_zero() -> None:
    """旧 2 维 mix（composite_weights=None）新增五列应为 0.0，不得错位或报错。"""
    variants = _walkforward_grid_variants("stable_plus")
    assert variants[0].composite_weights is None, "基线臂应仍是 2 维 mix（无 7 维向量）"
    rows = [(variants[0], 1.61, 0.07, 20, 300)]
    lines: list[str] = []
    _append_walkforward_grid_rows(
        lines, dsr=0.94, pbo=0.21, periods=20, rows=rows, details=None, test_days=146
    )
    report = "\n".join(lines)
    assert EXPECTED_HEADER in report
    row = next(ln for ln in report.splitlines() if ln.startswith("| WF-001 |"))
    assert row.startswith("| WF-001 | 0.3 | 0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 10 |")
    # 2 维 mix 不应出现「必须看 7 维」的提示（那条只对 planb 有意义）
    assert "7 维列说明" not in report
