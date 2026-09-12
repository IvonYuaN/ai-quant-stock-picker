#!/usr/bin/env python3
"""T3 方案 A 双窗口对比：htf_mr(WF-H0x) vs WF-001 基线，3y + 5y 两不重叠窗口。

读取 4 个 gate_run 目录的：
  - report.md          逐变体 Sharpe / 总收益 表
  - walkforward_gate.json  门禁指标（pbo / dsr / both_pass / effective_symbols / cscv_reliability）

产出双窗口对比结论 markdown，并判定「方案 A 是否成立」（两窗口方向一致才成立）。

用法：
  python scripts/compare_t3_dual_window.py \
    --wf001_5y   outputs/gate_run_5y_server \
    --wf001_3y   outputs/gate_run_wf001_3y \
    --htf_mr_3y  outputs/gate_run_htf_mr_3y \
    --htf_mr_5y  outputs/gate_run_htf_mr_5y \
    --out        outputs/T3_双窗口对比结论_<date>.md

判据（与 MEMORY 一致）：htf_mr 相对 WF-001 的「改善方向」须在 3y 与 5y 两窗口一致，
才判方案 A（htf+mr 换 mom+tr）成立；任一窗口不及 → 退回方案 B（纯 htf）或诊断。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BASELINE = "WF-001"
HTF_MR_VARIANTS = [f"WF-H0{i}" for i in range(1, 9)]


def _norm_num(s: str) -> float | None:
    """把 '−0.65' / '-15.30%' / '0.51' 转 float；失败返 None。"""
    if s is None:
        return None
    s = s.replace("−", "-").replace("%", "").replace(",", "").strip()
    s = s.rstrip("%").strip()
    if s in ("", "-", "—", "N/A", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None


def parse_report_variants(report_path: Path) -> dict[str, dict]:
    """解析 report.md 逐变体表 -> {variant: {sharpe, total_return_pct, periods, raw}}。

    表头判定：含 '臂' 且 'Sharpe' 且 '总收益' 的行；其后每个 '|' 行按列解析，
    取 WF-001 / WF-H0x 开头的变体名。容忍分隔线、列顺序、unicode 减号。
    """
    out: dict[str, dict] = {}
    if not report_path.exists():
        return out
    lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\|\s*臂\s*\|", line) and "Sharpe" in line and "总收益" in line:
            header_idx = i
            break
    if header_idx is None:
        return out
    for line in lines[header_idx + 1:]:
        if not line.strip().startswith("|"):
            if out:
                break
            continue
        if set(line.strip()) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        m = re.search(r"(WF-001|WF-H0\d)", cells[0])
        if not m:
            continue
        key = m.group(1)
        sharpe = _norm_num(cells[1]) if len(cells) > 1 else None
        total_return = _norm_num(cells[2]) if len(cells) > 2 else None
        periods = _norm_num(cells[3]) if len(cells) > 3 else None
        out[key] = {
            "sharpe": sharpe,
            "total_return_pct": total_return,
            "periods": periods,
            "raw": cells[0],
        }
    return out


def parse_gate(gate_path: Path) -> dict:
    if not gate_path.exists():
        return {}
    try:
        return json.loads(gate_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%" if v >= 0 else f"{v:.2f}%"


def _fmt_sharpe(v: float | None) -> str:
    return f"{v:+.2f}" if v is not None else "—"


def build_window_table(title: str, base: dict, cand: dict) -> str:
    """baseline=WF-001 dict, cand=htf_mr 全变体 dict。"""
    rows = [f"### {title}", "", "| 变体 | Sharpe | 总收益 | 周期数 |", "|---|---|---|---|"]
    if base:
        rows.append(
            f"| **WF-001**（基线）| {_fmt_sharpe(base.get('sharpe'))} | "
            f"{_fmt_pct(base.get('total_return_pct'))} | {base.get('periods') or '—'} |"
        )
    for v in HTF_MR_VARIANTS:
        if v in cand:
            d = cand[v]
            rows.append(
                f"| {v} | {_fmt_sharpe(d.get('sharpe'))} | "
                f"{_fmt_pct(d.get('total_return_pct'))} | {d.get('periods') or '—'} |"
            )
    if not base and not cand:
        rows.append("| （无解析结果）| — | — | — |")
    return "\n".join(rows)


def verdict_for_window(base: dict, cand: dict) -> dict:
    """返回该窗口 htf_mr 相对 WF-001 的方向判定。"""
    if not base:
        return {"best": None, "beats_sharpe": None, "beats_return": None, "note": "基线缺失"}
    b_sh = base.get("sharpe")
    b_ret = base.get("total_return_pct")
    best_sh, best_ret, best_name = None, None, None
    for v in HTF_MR_VARIANTS:
        if v not in cand:
            continue
        d = cand[v]
        if d.get("sharpe") is None:
            continue
        if best_sh is None or d["sharpe"] > best_sh:
            best_sh, best_ret, best_name = d["sharpe"], d.get("total_return_pct"), v
    if best_name is None:
        return {"best": None, "beats_sharpe": None, "beats_return": None, "note": "无 htf_mr 变体"}
    beats_sharpe = (best_sh > b_sh) if (b_sh is not None) else None
    beats_return = (best_ret > b_ret) if (b_ret is not None) else None
    return {
        "best": best_name,
        "best_sharpe": best_sh,
        "best_return": best_ret,
        "base_sharpe": b_sh,
        "base_return": b_ret,
        "beats_sharpe": beats_sharpe,
        "beats_return": beats_return,
        "note": f"最佳 htf_mr = {best_name}",
    }


def direction(verdict: dict) -> str | None:
    """把单窗口判定压成方向标签：改善 / 退步 / 混合 / None（数据缺失）。"""
    sh = verdict.get("beats_sharpe")
    rt = verdict.get("beats_return")
    if sh is None and rt is None:
        return None
    # 两指标均劣化记「退步」；任一改善记「改善」；一劣一缺记「混合」
    if sh is False and rt is False:
        return "退步"
    if sh is True or rt is True:
        return "改善"
    return "混合"


def decide_scheme(d3: str | None, d5: str | None) -> str:
    """双窗口方向一致性判据 —— T3 方案 A 是否成立。

    只有「两窗口方向一致」才算数：一致改善 → 成立；一致退步 → 不成立；
    方向不一致或任一窗口数据缺失 → **不判成立**（宁可不判，也不拿单窗口结论下判断）。
    """
    if d3 == d5 and d3 in ("改善", "退步"):
        if d3 == "改善":
            return "✅ **方案 A 成立**：htf+mr 替换 mom+tr 在 3y 与 5y 两窗口方向一致改善 WF-001。"
        return "❌ **方案 A 不成立**：两窗口一致劣于 WF-001 → 退回方案 B（纯 htf）或诊断因子族。"
    if d3 is None or d5 is None:
        return "⏳ **暂不可判**：某窗口基线/变体缺失，待数据补全后重算。"
    return (
        f"🔶 **方向不一致**（3y={d3} / 5y={d5}）：疑似窗口依赖或因子族在不同 regime 下"
        "表现分化，需诊断，不判方案 A 成立。"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wf001_5y", required=True)
    ap.add_argument("--wf001_3y", required=True)
    ap.add_argument("--htf_mr_3y", required=True)
    ap.add_argument("--htf_mr_5y", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    dirs = {
        "WF-001 5y": Path(args.wf001_5y),
        "WF-001 3y": Path(args.wf001_3y),
        "htf_mr 3y": Path(args.htf_mr_3y),
        "htf_mr 5y": Path(args.htf_mr_5y),
    }

    parsed: dict[str, dict] = {}
    gates: dict[str, dict] = {}
    parse_notes: list[str] = []
    for label, d in dirs.items():
        rp = d / "report.md"
        gp = d / "walkforward_gate.json"
        variants = parse_report_variants(rp)
        gate = parse_gate(gp)
        parsed[label] = variants
        gates[label] = gate
        if not variants:
            parse_notes.append(f"⚠️ {label}: report.md 未解析到逐变体（可能格式不同或缺失）")

    # 逐窗口对比表
    wf001_5y_var = parsed["WF-001 5y"]
    wf001_3y_var = parsed["WF-001 3y"]
    htf_5y_var = parsed["htf_mr 5y"]
    htf_3y_var = parsed["htf_mr 3y"]

    v3 = verdict_for_window(wf001_3y_var.get("WF-001", {}), htf_3y_var)
    v5 = verdict_for_window(wf001_5y_var.get("WF-001", {}), htf_5y_var)

    # 门禁指标（gate json）
    def gate_line(label: str) -> str:
        g = gates[label]
        if not g:
            return f"| {label} | （无 gate json）| — | — | — | — |"
        return (
            f"| {label} | {g.get('grid_profile','—')} | "
            f"{g.get('effective_symbols','—')} | "
            f"{g.get('pbo','—')} | {g.get('deflated_sharpe','—')} | "
            f"{g.get('both_pass','—')} |"
        )

    # 方向一致性判定（逻辑已抽到模块级 direction / decide_scheme，便于单测）
    d3 = direction(v3)
    d5 = direction(v5)
    scheme = decide_scheme(d3, d5)

    # 组装 markdown
    out = ["# T3 方案 A 双窗口对比结论", ""]
    out.append("> 生成脚本：`scripts/compare_t3_dual_window.py`。判据：htf_mr 相对 WF-001 的改善方向须在 3y 与 5y 两窗口一致，才判方案 A（htf+mr 换 mom+tr）成立。")
    out.append("")
    out.append("## 一、门禁指标（gate json，可靠读数）")
    out.append("")
    out.append("| 窗口 | grid_profile | effective_symbols | PBO | DSR(deflated) | both_pass |")
    out.append("|---|---|---|---|---|---|")
    for label in dirs:
        out.append(gate_line(label))
    out.append("")
    out.append("## 二、逐变体 Sharpe / 总收益（report.md 解析）")
    out.append("")
    out.append(build_window_table("htf_mr 3y vs WF-001 3y", wf001_3y_var.get("WF-001", {}), htf_3y_var))
    out.append("")
    out.append(build_window_table("htf_mr 5y vs WF-001 5y", wf001_5y_var.get("WF-001", {}), htf_5y_var))
    out.append("")
    out.append("## 三、方向判定")
    out.append("")
    out.append(f"- **3y 窗口**：最佳 htf_mr = `{v3.get('best')}`，Sharpe {_fmt_sharpe(v3.get('best_sharpe'))} vs 基线 {_fmt_sharpe(v3.get('base_sharpe'))}；"
               f"总收益 {_fmt_pct(v3.get('best_return'))} vs 基线 {_fmt_pct(v3.get('base_return'))} → 方向 **{d3}**")
    out.append(f"- **5y 窗口**：最佳 htf_mr = `{v5.get('best')}`，Sharpe {_fmt_sharpe(v5.get('best_sharpe'))} vs 基线 {_fmt_sharpe(v5.get('base_sharpe'))}；"
               f"总收益 {_fmt_pct(v5.get('best_return'))} vs 基线 {_fmt_pct(v5.get('base_return'))} → 方向 **{d5}**")
    out.append("")
    out.append("## 四、方案 A 判决")
    out.append("")
    out.append(scheme)
    out.append("")
    if parse_notes:
        out.append("## 五、解析备注")
        out.append("")
        out.append("\n".join(parse_notes))
        out.append("")
    out.append("---")
    out.append("> 注：DSR 全负属已知 alpha 赤字（R3）；若方案 A 成立仅代表「因子族替换方向正确」，不代表样本外已盈利。")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(out), encoding="utf-8")
    print(f"✅ 对比结论已写出: {args.out}")
    print(f"   方向 3y={d3} / 5y={d5}")
    print(f"   判决: {scheme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
