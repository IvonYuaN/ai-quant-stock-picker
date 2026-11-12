"""holder_overlay 取证判读脚本的行为级单测。

锁三条不变量：
1. **按 id 匹配，不按参数签名** —— 报告表的权重列是**声明值**，`WF-001` 与
   `WF-V01` 声明参数完全相同（但 `WF-V01` 额外启用 volume）⇒ 按签名匹配会把
   两臂塌成一个（实测 8 臂只解析出 7 个）。
2. `WO-` 前缀的去前缀匹配是精确的（档位由 `replace(v, variant_id=f"WO-{...}")`
   逐臂复制 ⇒ 构造即 1:1）。
3. 候选侧未跑完时优雅降级（不报错、标"未完成"）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "runner"))

from compare_holder_overlay import (  # noqa: E402
    build_report,
    parse_log_arms,
    parse_report_table,
)

#: 精简版基线表：`WF-001` 与 `WF-V01` 的**声明参数完全相同**（这是塌臂陷阱）。
BASELINE_MD = """
| 变体 | mom | tr | qual | val | vol | mr | htf | lb | h | top | Sharpe | 总收益 | 暴露归一化收益 | 周期数 |
|------|-----|----|------|-----|-----|----|-----|----|---|-----|--------|--------|----------------|--------|
| WF-001 | 0.3 | 0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 10 | 0.05 | 0.13% | 1.27% | 20 |
| WF-B07 | 0.2 | 0.4 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 40 | 5 | 5 | 0.34 | 8.64% | 51.86% | 20 |
| WF-V01 | 0.3 | 0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 10 | -0.05 | -1.93% | -19.26% | 20 |
"""

#: 候选表：同参数但 id 带 `WO-` 前缀；故意让 WF-001 变好、WF-V01 变差。
CANDIDATE_MD = """
| 变体 | mom | tr | qual | val | vol | mr | htf | lb | h | top | Sharpe | 总收益 | 暴露归一化收益 | 周期数 |
|------|-----|----|------|-----|-----|----|-----|----|---|-----|--------|--------|----------------|--------|
| WO-WF-001 | 0.3 | 0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 10 | 0.15 | 1.13% | 2.27% | 20 |
| WO-WF-B07 | 0.2 | 0.4 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 40 | 5 | 5 | 0.30 | 7.00% | 40.00% | 20 |
| WO-WF-V01 | 0.3 | 0.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 10 | -0.15 | -3.00% | -25.00% | 20 |
"""


def test_parse_report_table_does_not_collapse_same_signature_arms() -> None:
    """`WF-001` 与 `WF-V01` 声明参数相同，必须各自保留（按 id 索引）。"""
    rows = parse_report_table(BASELINE_MD)
    assert set(rows) == {"WF-001", "WF-B07", "WF-V01"}
    # 两个臂的签名确实相同 —— 这正是「按签名会塌臂」的前提
    assert rows["WF-001"]["signature"] == rows["WF-V01"]["signature"]


def test_parse_report_table_skips_header_and_separator() -> None:
    rows = parse_report_table(BASELINE_MD)
    assert "变体" not in rows
    assert all(not key.startswith("-") for key in rows)


def test_build_report_matches_wo_prefix_by_id() -> None:
    """候选侧 `WO-X` 必须与基线 `X` 配对（id 去前缀精确匹配）。"""
    baseline = parse_report_table(BASELINE_MD)
    candidate = parse_report_table(CANDIDATE_MD)
    text = build_report(baseline=baseline, candidate=candidate)

    assert "已配对臂数：**3** / 基线 3 臂" in text
    # ΔSharpe 逐臂：WF-001 +0.10、WF-B07 -0.04、WF-V01 -0.10
    assert "+0.10" in text
    assert "-0.04" in text
    assert "-0.10" in text


def test_build_report_degrades_when_candidate_incomplete() -> None:
    """候选侧只跑完一部分时：不报错、已配对正常算、未配对明确标注。"""
    baseline = parse_report_table(BASELINE_MD)
    candidate = {
        "WO-WF-001": parse_report_table(CANDIDATE_MD)["WO-WF-001"],
    }
    text = build_report(baseline=baseline, candidate=candidate)
    assert "已配对臂数：**1** / 基线 3 臂" in text
    assert "未配对基线臂：WF-B07, WF-V01" in text


def test_build_report_notes_dsr_not_comparable_across_arm_counts() -> None:
    """必须显式提示 DSR 不可跨臂数直接比（多重检验惩罚依赖 n_variants）。"""
    text = build_report(
        baseline=parse_report_table(BASELINE_MD),
        baseline_gate={"deflated_sharpe": -1.2463, "n_variants": 8},
        candidate_gate={"deflated_sharpe": -0.9, "n_variants": 13},
    )
    assert "不可跨臂数直接比" in text
    assert "n_variants=8" in text
    assert "n_variants=13" in text


def test_parse_log_arms_reads_completed_arms() -> None:
    """跑批中可从日志增量读已完成臂（id/sharpe/总收益）。"""
    log = """
grid variant WO-WF-001/13: mw=0.3 trw=0.3→0.0(#382 强制配对) lookback=60 horizon=3 top_n=10
grid variant WO-WF-001: periods=20 sharpe=0.05 return=0.13%
grid variant WO-WF-B01: periods=20 sharpe=-0.15 return=-3.81%
"""
    arms = parse_log_arms(log)
    assert set(arms) == {"WO-WF-001", "WO-WF-B01"}
    assert arms["WO-WF-001"]["sharpe"] == 0.05
    assert abs(arms["WO-WF-B01"]["total_return"] + 0.0381) < 1e-9


def test_log_mode_matches_by_stripped_id() -> None:
    """日志模式（无权重列）也必须能按 id 去前缀与基线配对。"""
    baseline = parse_report_table(BASELINE_MD)
    log = "grid variant WO-WF-001: periods=20 sharpe=0.20 return=2.00%\n"
    text = build_report(baseline=baseline, candidate_log=parse_log_arms(log))
    assert "已配对臂数：**1** / 基线 3 臂" in text
    assert "+0.15" in text  # 0.20 - 0.05


def test_gate_summary_handles_missing_payload() -> None:
    text = build_report(baseline=parse_report_table(BASELINE_MD))
    assert "gate json 未提供或不可读" in text


def test_json_roundtrip_gate_summary(tmp_path: Path) -> None:
    """gate json 的读取与摘要（确保字段名与真实产物一致）。"""
    gate = tmp_path / "walkforward_gate.json"
    gate.write_text(
        json.dumps({"deflated_sharpe": -1.2463, "pbo": 0.6627, "n_variants": 8}),
        encoding="utf-8",
    )
    text = build_report(
        baseline=parse_report_table(BASELINE_MD),
        baseline_gate=json.loads(gate.read_text(encoding="utf-8")),
    )
    assert "deflated_sharpe=-1.2463" in text
    assert "pbo=0.6627" in text


# --------------------------------------------------------------------------- #
# 预注册判定：防止「看到结果再编标准」
# --------------------------------------------------------------------------- #


def _verdict(matched: int, baseline_total: int, deltas, cand_dsr=None):
    from compare_holder_overlay import _preregistered_verdict

    return _preregistered_verdict(
        matched=matched,
        baseline_total=baseline_total,
        deltas=list(deltas),
        baseline_gate={"deflated_sharpe": -1.2463},
        candidate_gate=({} if cand_dsr is None else {"deflated_sharpe": cand_dsr}),
    )


def test_verdict_refuses_halfway_conclusion() -> None:
    """未跑完（配对臂数 < 基线臂数）必须明确「暂不判定」，不给半程结论。"""
    verdict, reasons = _verdict(3, 8, [0.5, 0.4, 0.3], cand_dsr=0.5)
    assert "未完成" in verdict and "暂不判定" in verdict
    assert any("半程数据不构成结论" in r for r in reasons)


def test_verdict_pass_requires_both_criteria() -> None:
    """A（≥5/8 臂改善且均值>0）+ B（DSR 优于基线）都满足才算 PASS。"""
    deltas = [0.2, 0.1, 0.05, 0.03, 0.02, -0.01, -0.02, -0.03]  # 5 改善、均值>0
    verdict, reasons = _verdict(8, 8, deltas, cand_dsr=-1.0)
    assert "PASS" in verdict
    assert any("A 逐臂：改善 5/8" in r for r in reasons)


def test_verdict_fail_when_arms_not_improved() -> None:
    """改善臂数不足（<5）时，即使均值>0 也不得 PASS（防单臂极值带偏）。"""
    deltas = [3.0, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1]  # 仅 1 臂改善
    verdict, _ = _verdict(8, 8, deltas, cand_dsr=0.5)
    assert "FAIL" in verdict or "部分通过" in verdict
    assert "PASS（有正效果）" not in verdict


def test_verdict_fail_when_dsr_worse() -> None:
    """DSR 未优于基线时不得判 PASS。"""
    deltas = [0.9, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
    verdict, reasons = _verdict(8, 8, deltas, cand_dsr=-1.5)
    assert "PASS（有正效果）" not in verdict
    assert any("B DSR" in r and "FAIL" in r for r in reasons)


def test_report_includes_preregistered_section() -> None:
    """报告必须含「预注册判定」章节，且写明不许改判据重跑。"""
    text = build_report(baseline=parse_report_table(BASELINE_MD))
    assert "预注册判定" in text
    assert "不许改判据" in text


def test_report_flags_period_count_mismatch() -> None:
    """period 数不一致必须被自动报出（窗口错位 ⇒ 对齐期数不同 ⇒ 可比性下降）。"""
    baseline = parse_report_table(BASELINE_MD)  # 全部 20 期
    # 日志里首臂实测 19 期
    log = "grid variant WO-WF-001: periods=19 sharpe=0.40 return=8.86%\n"
    text = build_report(baseline=baseline, candidate_log=parse_log_arms(log))
    assert "period 数不一致" in text
    assert "WF-001: 基线 20 期 vs overlay 19 期" in text


def test_report_no_period_warning_when_aligned() -> None:
    """期数一致时不得误报。"""
    baseline = parse_report_table(BASELINE_MD)
    log = "grid variant WO-WF-001: periods=20 sharpe=0.40 return=8.86%\n"
    text = build_report(baseline=baseline, candidate_log=parse_log_arms(log))
    assert "period 数不一致" not in text


def test_parse_handles_starred_values() -> None:
    """报告表里 `tr` 列会有 `0.0*`（#382 强制配对标记），不得因此丢掉整行。

    2026-11-12 实测：候选侧 8 臂全被跳过 ⇒ 逐臂表显示"已配对 0/8"，
    而 gate 级数字其实正确 —— 极易误判为"没有可对比的臂"。
    """
    md = """
| 变体 | mom | tr | qual | val | vol | mr | htf | lb | h | top | Sharpe | 总收益 | 暴露归一化收益 | 周期数 |
|------|-----|----|------|-----|-----|----|-----|---|-----|--------|--------|----------------|--------|
| WO-WF-001 | 0.3 | 0.0* | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 60 | 3 | 10 | 0.09 | 0.92% | 9.23% | 20 |
"""
    rows = parse_report_table(md)
    assert "WO-WF-001" in rows
    assert rows["WO-WF-001"]["sharpe"] == 0.09
