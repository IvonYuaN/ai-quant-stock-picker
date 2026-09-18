"""``compare_t3_dual_window.parse_report_variants`` 的回归测试。

为什么单独测这个函数（`test_compare_t3_dual_window.py` 已存在却不覆盖它）：
该文件只测 `direction` / `decide_scheme` / `verdict_for_window`，全部用手工构造的
dict 喂入 —— **没有任何一个用例经过真实的 `report.md` 文本**。于是下面这个缺陷
在测试全绿的情况下长期存在：

  旧解析器要求表头**字面含 `臂`** 且把 Sharpe/总收益/周期数**硬编码**在 columns 1/2/3；
  而真实 `--grid-cscv` 报告（`cli.py:_append_walkforward_grid_rows`）的表头是
  `| 变体 | mom | tr | lb | h | top | Sharpe | 总收益 | 周期数 |`，三者位于 columns 6/7/8。
  ⇒ 判据不匹配导致**整表 miss（返回 {}）**；即便匹配上，也会把 mom 的**权重**当成
  Sharpe、把 tr 的权重当成总收益、把 lb(=60) 当成周期数。

后果不是"报错"，而是**静默降级成「暂不可判」** —— 跑完一场数小时的 walk-forward 门禁
后，收口结论是一句无害的"数据缺失"。故此处以**真实报告原文**为金标准锁死。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import compare_t3_dual_window as m

# --------------------------------------------------------------------------
# 金标准：以下三行**逐字**取自真实 gate 报告 `gate_run_5y/p2/report.md`
# （真实产物目录已 gitignore，故内联为 fixture，保证 CI 可复现）
# --------------------------------------------------------------------------
REAL_HEADER = "| 变体 | mom | tr | lb | h | top | Sharpe | 总收益 | 周期数 |"
REAL_SEP = "|------|-----|----|----|---|-----|--------|--------|--------|"
REAL_WF001_ROW = "| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 1.14 | 47.65% | 36 |"
# 同文件的 TL;DR 行，作**独立交叉验证**来源（同一事实的第二处记载）
REAL_TLDR = "**TL;DR**: FAIL — DSR=0.5450, PBO=14.29%, Sharpe=1.14, TotalReturn=47.65%"

# 真实报告里其它**含 "Sharpe" 的干扰表**（解析器必须跳过它们）
REAL_STAGE_TABLE = "\n".join(
    [
        "| 阶段 | 收益 | Sharpe | 胜率 | 交易次数 | 不可成交 |",
        "|---|---|---|---|---|---|",
        "| 训练 | 30.00% | 1.20 | 55.0% | 120 | 0 |",
    ]
)
REAL_TRAIN_TABLE = "\n".join(
    [
        "| 训练选中变体 | 训练块 | 测试块 | 训练 Sharpe | 测试 Sharpe | "
        "测试倒数排名 | 测试最优变体 | Lambda |",
        "|---|---|---|---|---|---|---|---|",
        "| WF-V2A | 1 | 2 | 0.51 | -0.89 | 2 | WF-V2B | 0.6931 |",
    ]
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "report.md"
    p.write_text(body, encoding="utf-8")
    return p


def _real_report(*variant_rows: str) -> str:
    """真实格式的报告：干扰表在前、逐变体表在后（与真实产物同序）。"""
    return "\n".join(
        [
            "# walk-forward gate report",
            "",
            REAL_TLDR,
            "",
            "## 分阶段表现",
            "",
            REAL_STAGE_TABLE,
            "",
            "## 逐变体（grid CSCV）",
            "",
            REAL_HEADER,
            REAL_SEP,
            *variant_rows,
            "",
            "### PBO 失败定位",
            "",
            "| 指标 | 值 |",
            "|---|---|",
            "| CSCV 失败组合占比 | 14.29% |",
            "",
            "## 训练/测试块明细",
            "",
            REAL_TRAIN_TABLE,
            "",
        ]
    )


# --------------------------------------------------------------------------
# 核心：真实 9 列表头必须被识别，且列位必须按表头动态定位
# --------------------------------------------------------------------------
def test_parses_real_nine_column_grid_table(tmp_path: Path) -> None:
    out = m.parse_report_variants(_write(tmp_path, _real_report(REAL_WF001_ROW)))
    assert set(out) == {"WF-001"}
    assert out["WF-001"]["sharpe"] == pytest.approx(1.14)
    assert out["WF-001"]["total_return_pct"] == pytest.approx(47.65)
    assert out["WF-001"]["periods"] == pytest.approx(36)


def test_parsed_values_match_independent_tldr_line(tmp_path: Path) -> None:
    """与同文件的 TL;DR 行交叉验证 —— 两处记载必须一致。

    TL;DR 是独立来源（由另一段代码写出），若解析器错位取值，此处必然不一致。
    """
    out = m.parse_report_variants(_write(tmp_path, _real_report(REAL_WF001_ROW)))
    got = out["WF-001"]
    assert f"Sharpe={got['sharpe']:.2f}" in REAL_TLDR
    assert f"TotalReturn={got['total_return_pct']:.2f}%" in REAL_TLDR


def test_sharpe_is_not_read_from_weight_column(tmp_path: Path) -> None:
    """回归：旧解析器取 columns 1/2/3 ⇒ mom 权重被当成 Sharpe。

    真实行 `| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 1.14 | 47.65% | 36 |`
    的列 1/2/3 分别是 mom=0.3 / tr=0.3 / lb=60。若解析器错位，会得到
    sharpe=0.3、总收益=0.3、周期数=60 —— 三个值都"像数字"，不会报错，只会静默错误。
    """
    out = m.parse_report_variants(_write(tmp_path, _real_report(REAL_WF001_ROW)))
    got = out["WF-001"]
    assert got["sharpe"] != pytest.approx(0.3), "把 mom 权重当成了 Sharpe"
    assert got["total_return_pct"] != pytest.approx(0.3), "把 tr 权重当成了总收益"
    assert got["periods"] != pytest.approx(60), "把 lb 当成了周期数"


def test_distractor_sharpe_tables_are_not_used(tmp_path: Path) -> None:
    """分阶段表 / 训练-测试块表都含 "Sharpe"，不得被当成逐变体表。

    若误取分阶段表，会解析出 0 个变体；若误取训练块表，"变体"列里是 WF-V2A/WF-V2B，
    但本项目只关心 WF-001/WF-H0x，故同样应得空 —— 关键是**不得**由此产出假读数。
    """
    out = m.parse_report_variants(_write(tmp_path, _real_report(REAL_WF001_ROW)))
    assert "WF-V2A" not in out and "WF-V2B" not in out
    assert out["WF-001"]["periods"] == pytest.approx(36)  # 不是 PBO 表的 14.29


# --------------------------------------------------------------------------
# 表头兼容：真实 `变体` 为主，旧 `臂` 仍需可用（不制造回归）
# --------------------------------------------------------------------------
def test_real_variant_header_is_recognised(tmp_path: Path) -> None:
    """真实表头写的是 `变体`；旧解析器硬要字面 `臂` ⇒ 整表 miss。

    此用例在旧解析器下返回 {} —— 这是"跑完数小时才发现的静默降级"的最小复现。
    """
    out = m.parse_report_variants(_write(tmp_path, _real_report(REAL_WF001_ROW)))
    assert out, "真实 `变体` 表头未被识别 → 收口会静默降级为「暂不可判」"


def test_legacy_arm_header_still_supported(tmp_path: Path) -> None:
    """旧版 4 列表头（`| 臂 | Sharpe | 总收益 | 周期数 |`）仍应可解析。"""
    body = "\n".join(
        [
            "| 臂 | Sharpe | 总收益 | 周期数 |",
            "|---|---|---|---|",
            "| WF-001 | 0.88 | 12.34% | 20 |",
        ]
    )
    out = m.parse_report_variants(_write(tmp_path, body))
    assert out["WF-001"]["sharpe"] == pytest.approx(0.88)
    assert out["WF-001"]["total_return_pct"] == pytest.approx(12.34)
    assert out["WF-001"]["periods"] == pytest.approx(20)


# --------------------------------------------------------------------------
# 变体收集与数值归一
# --------------------------------------------------------------------------
def test_collects_all_htf_mr_variants(tmp_path: Path) -> None:
    rows = [REAL_WF001_ROW]
    for i in range(1, 9):
        rows.append(f"| WF-H0{i} | 0.0 | 0.0 | 60 | 3 | 10 | 0.{50 + i} | {10 + i}.00% | 36 |")
    out = m.parse_report_variants(_write(tmp_path, _real_report(*rows)))
    assert {f"WF-H0{i}" for i in range(1, 9)} <= set(out)
    assert out["WF-001"]["sharpe"] == pytest.approx(1.14)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("−0.65", -0.65),      # unicode 减号（真实报告会用）
        ("-15.30%", -15.30),   # 负百分比
        ("+18.20%", 18.20),    # 正号 + 百分号
        ("1,234.5", 1234.5),   # 千分位
        ("—", None),           # em dash = 无值（跳过期）
        ("N/A", None),
        ("", None),
    ],
)
def test_number_normalisation(cell: str, expected: float | None) -> None:
    assert m._norm_num(cell) == (None if expected is None else pytest.approx(expected))


def test_unusable_metric_stays_none_not_zero(tmp_path: Path) -> None:
    """`—`（跳过期）必须落成 None 而非 0.0，否则会把"没数据"算成"零收益"。"""
    row = "| WF-H01 | 0.0 | 0.0 | 60 | 3 | 10 | — | — | 36 |"
    out = m.parse_report_variants(_write(tmp_path, _real_report(REAL_WF001_ROW, row)))
    assert out["WF-H01"]["sharpe"] is None
    assert out["WF-H01"]["total_return_pct"] is None


# --------------------------------------------------------------------------
# 缺失容忍：文件不存在 / 无逐变体表 ⇒ {}，绝不抛异常
# --------------------------------------------------------------------------
def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert m.parse_report_variants(tmp_path / "nope.md") == {}


def test_report_without_variant_table_returns_empty(tmp_path: Path) -> None:
    """单序列回测的报告（PBO=N/A）没有逐变体表 —— 真实存在的形态。"""
    body = "\n".join([REAL_TLDR, "", REAL_STAGE_TABLE, ""])
    assert m.parse_report_variants(_write(tmp_path, body)) == {}


# --------------------------------------------------------------------------
# 端到端：四臂目录 → 结论文档（覆盖 main() 的编排与判决落文）
# --------------------------------------------------------------------------
def _make_arm(root: Path, name: str, rows: list[str], gate: dict | None) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(_real_report(*rows), encoding="utf-8")
    if gate is not None:
        import json

        (d / "walkforward_gate.json").write_text(
            json.dumps(gate, ensure_ascii=False), encoding="utf-8"
        )
    return d


def _run_main(monkeypatch: pytest.MonkeyPatch, root: Path, out: Path) -> str:
    argv = [
        "compare_t3_dual_window.py",
        "--wf001_5y", str(root / "wf001_5y"),
        "--wf001_3y", str(root / "wf001_3y"),
        "--htf_mr_3y", str(root / "htf_mr_3y"),
        "--htf_mr_5y", str(root / "htf_mr_5y"),
        "--out", str(out),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert m.main() == 0
    return out.read_text(encoding="utf-8")


def test_end_to_end_establishes_scheme_a_when_both_windows_improve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "arms"
    gate = {"grid_profile": "htf_mr", "effective_symbols": 5508, "pbo": 0.31,
            "deflated_sharpe": -1.10, "both_pass": False}
    # 基线刻意取较弱值：判定用的是**基线臂**的 WF-001 行（非候选臂自己的 WF-001）
    _make_arm(root, "wf001_5y", ["| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 0.40 | 12.00% | 40 |"], gate)
    _make_arm(root, "wf001_3y", ["| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 0.55 | 18.00% | 24 |"], gate)
    _make_arm(
        root, "htf_mr_5y",
        ["| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 0.55 | 18.00% | 40 |",
         "| WF-H01 | 0.0 | 0.0 | 60 | 3 | 10 | 0.90 | 30.00% | 40 |"],
        gate,
    )
    _make_arm(
        root, "htf_mr_3y",
        ["| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 0.55 | 18.00% | 24 |",
         "| WF-H01 | 0.0 | 0.0 | 60 | 3 | 10 | 0.95 | 33.00% | 24 |"],
        gate,
    )
    text = _run_main(monkeypatch, root, tmp_path / "out.md")
    assert "✅ **方案 A 成立**" in text
    assert "| htf_mr 5y | htf_mr | 5508 |" in text          # gate json 被读到
    assert "WF-H01 | +0.90 | +30.00% | 40.0 |" in text       # 变体表被读到


def test_end_to_end_defers_when_reports_have_no_variant_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """四臂都缺逐变体表 ⇒ 必须**如实报暂不可判**并列出备注，不得编造结论。"""
    root = tmp_path / "arms"
    for name in ("wf001_5y", "wf001_3y", "htf_mr_3y", "htf_mr_5y"):
        _make_arm(root, name, [], None)
    text = _run_main(monkeypatch, root, tmp_path / "out.md")
    assert "⏳ **暂不可判**" in text
    assert "解析备注" in text
    assert text.count("未解析到逐变体") == 4
    # 不得出现「✅ 成立」判决（文档尾注里的"若方案 A 成立…"是固定免责声明，不在此列）
    assert "✅ **方案 A 成立**" not in text
