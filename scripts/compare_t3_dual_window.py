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

判据（2026-09-24 修订，见 issue #194）：**主判据 = 同 `horizon` 层内的臂均值对比**。
htf_mr 臂相对基线臂的改善方向须在 3y 与 5y 两窗口一致，才判方案 A（htf+mr 换 mom+tr）成立；
任一窗口不及 → 退回方案 B（纯 htf）或诊断。

⚠️ 原判据是「8 个 htf_mr 变体里的**最佳** vs 单个基线变体 WF-001」，有两处缺陷：
  1) **best-of-8 vs single** —— 不对称，等于给候选臂白送一个选择性偏差；
  2) 该「最佳」是 `WF-H07`(h=10)，而基线 `WF-001` 是 h=3 —— **跨了 `horizon` 轴**。
实测后果：3y 窗口据此判「改善」，按同 `h` 分层后两臂其实无差别（ΔSharpe=+0.039，
小于同层离散度的 1/4）⇒ 那是**持有期效应**，不是因子族效应。
故本脚本保留原口径但**显式标注为不可单独引用**，判决只走分层口径。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BASELINE = "WF-001"
HTF_MR_VARIANTS = [f"WF-H0{i}" for i in range(1, 9)]

#: 分层轴 —— 两臂差异最大的那个非目标轴（见模块 docstring 缺陷 2）
STRATIFY_AXIS = "h"

#: 非轴列：变体名列，以及任何含这些子串的指标列（Sharpe / 总收益 / 暴露归一化收益 / 周期数）
AXIS_EXCLUDE_SUBSTRINGS = ("Sharpe", "收益", "周期")
NAME_HEADERS = ("变体", "臂", "variant")

#: 变体名 —— 必须覆盖两臂的全部命名（`WF-001` / `WF-B01` / `WF-V01` / `WF-MR01` / `WF-H01` …）。
#: 臂归属由报告所在目录决定，不能靠名字前缀过滤，否则基线臂只剩 WF-001 一个点。
VARIANT_NAME_RE = r"(WF-[A-Za-z0-9]+)"

#: 噪声带宽度 = 同层基线离散度（max−min）的这个比例。差异小于它 ⇒ 判「持平」。
#: 用相对口径而非绝对阈值，换窗口/换量纲不必重调常数。
NOISE_SPREAD_FRACTION = 0.25


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


def _is_axis_column(header: str) -> bool:
    """判断 report 表头某一列是不是「分层轴」（mom / tr / lb / h / top …）。

    排除变体名列，以及任何含 Sharpe / 收益 / 周期的指标列 —— 注意
    `暴露归一化收益` 含「收益」也会被正确排除。
    """
    h = header.strip()
    if h.lower() in NAME_HEADERS:
        return False
    return not any(sub in h for sub in AXIS_EXCLUDE_SUBSTRINGS)


def parse_report_variants(report_path: Path) -> dict[str, dict]:
    """解析 report.md 逐变体表 -> {variant: {sharpe, total_return_pct, periods, raw}}。

    适配真实 gate（--grid-cscv）报告表头（cli.py:_append_walkforward_grid_rows）：
      | 变体 | mom | tr | lb | h | top | Sharpe | 总收益 | 周期数 |
    Sharpe/总收益/周期数 在第 6/7/8 列（旧 '臂' 4 列表头在第 1/2/3 列），
    故按表头列名**动态定位**列，避免硬编码错位。

    🔴 必须捕获表里**每一个**变体行（`WF-001` / `WF-B0x` / `WF-V0x` / `WF-MR1` / `WF-H0x` …），
    而不是只认 `WF-001|WF-H0x`：臂归属由**报告来自哪个目录**决定，不是由变体名决定。
    早期版本只认那两个前缀，导致基线臂被解析成 n=1（只有 WF-001），
    「臂均值对比」退化成「单点 vs 单点」—— 修 #194 时踩到过。
    同时把非指标列（mom / tr / lb / h / top）收进 `axes` 供分层使用。
    """
    out: dict[str, dict] = {}
    if not report_path.exists():
        return out
    lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if (
            "Sharpe" in line
            and "总收益" in line
            and re.search(r"\|\s*(变体|臂|variant)\s*\|", line, re.IGNORECASE)
        ):
            header_idx = i
            break
    if header_idx is None:
        return out
    header_cells = [c.strip() for c in lines[header_idx].strip().strip("|").split("|")]

    def _col(sub: str) -> int:
        for j, h in enumerate(header_cells):
            if sub in h:
                return j
        return -1

    name_idx = _col("变体") if _col("变体") >= 0 else _col("臂")
    if name_idx < 0:
        name_idx = 0
    sharpe_idx = _col("Sharpe")
    return_idx = _col("总收益")
    periods_idx = _col("周期数") if _col("周期数") >= 0 else _col("周期")
    if sharpe_idx < 0 or return_idx < 0:
        return out
    min_cols = max(sharpe_idx, return_idx, name_idx)
    if periods_idx >= 0:
        min_cols = max(min_cols, periods_idx)

    # 分层轴（mom / tr / lb / h / top …）—— 指标列与变体名列之外的都是轴
    axis_idx = {
        j: header_cells[j].strip()
        for j in range(len(header_cells))
        if _is_axis_column(header_cells[j])
    }

    for line in lines[header_idx + 1:]:
        if not line.strip().startswith("|"):
            if out:
                break
            continue
        if set(line.strip()) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) <= min_cols:
            continue
        m = re.search(VARIANT_NAME_RE, cells[name_idx])
        if not m:
            continue
        key = m.group(1)
        sharpe = _norm_num(cells[sharpe_idx])
        total_return = _norm_num(cells[return_idx])
        periods = _norm_num(cells[periods_idx]) if periods_idx >= 0 else None
        axes = {name: cells[j] for j, name in axis_idx.items() if j < len(cells)}
        out[key] = {
            "sharpe": sharpe,
            "total_return_pct": total_return,
            "periods": periods,
            "axes": axes,
            "raw": cells[name_idx],
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
    """把单窗口**朴素**判定压成方向标签：改善 / 退步 / 混合 / None（数据缺失）。

    ⚠️ 仅供「朴素口径」小节使用，不可作为判决依据（见模块 docstring）。
    """
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


def stratify_variants(
    variants: dict[str, dict], axis: str, value: str
) -> dict[str, dict]:
    """取 `axes[axis] == value` 的变体子集（同层）。"""
    target = str(value).strip()
    return {
        name: d
        for name, d in variants.items()
        if str(d.get("axes", {}).get(axis, "")).strip() == target
    }


def arm_stats(variants: dict[str, dict]) -> dict:
    """臂内统计。**报均值必须带 n 与离散度**，否则无法判断差异是否只是噪声。"""
    sharpes = [
        d["sharpe"] for d in variants.values() if d.get("sharpe") is not None
    ]
    returns = [
        d["total_return_pct"]
        for d in variants.values()
        if d.get("total_return_pct") is not None
    ]
    stats: dict = {"n": len(variants), "n_sharpe": len(sharpes), "n_return": len(returns)}
    if sharpes:
        stats["mean_sharpe"] = sum(sharpes) / len(sharpes)
        stats["min_sharpe"] = min(sharpes)
        stats["max_sharpe"] = max(sharpes)
    if returns:
        stats["mean_return"] = sum(returns) / len(returns)
        stats["min_return"] = min(returns)
        stats["max_return"] = max(returns)
    return stats


def direction_from_delta(
    delta_sharpe: float | None, delta_return: float | None
) -> str | None:
    """按两指标差的符号定方向：同正 → 改善 / 同负 → 退步 / 异号 → 持平。"""
    vals = [v for v in (delta_sharpe, delta_return) if v is not None]
    if not vals:
        return None
    if all(v > 0 for v in vals):
        return "改善"
    if all(v < 0 for v in vals):
        return "退步"
    return "持平"


def stratified_verdict(
    base_arm: dict[str, dict],
    cand_arm: dict[str, dict],
    baseline_name: str = BASELINE,
    axis: str = STRATIFY_AXIS,
) -> dict | None:
    """**主判据**：以基线所在的那一层为准，两臂各取臂内均值同层对比。

    返回 None 表示不可判（基线缺失 / 层值缺失 / 任一侧该层为空）。
    """
    base_row = base_arm.get(baseline_name)
    if not base_row:
        return None
    layer = str(base_row.get("axes", {}).get(axis, "")).strip()
    if not layer:
        return None
    base_layer = stratify_variants(base_arm, axis, layer)
    cand_layer = stratify_variants(cand_arm, axis, layer)
    if not base_layer or not cand_layer:
        return None

    base_stats = arm_stats(base_layer)
    cand_stats = arm_stats(cand_layer)
    out: dict = {
        "axis": axis,
        "layer": layer,
        "baseline_name": baseline_name,
        "base": base_stats,
        "cand": cand_stats,
        "base_layer_variants": sorted(base_layer),
        "cand_layer_variants": sorted(cand_layer),
    }
    if "mean_sharpe" in base_stats and "mean_sharpe" in cand_stats:
        out["delta_sharpe"] = cand_stats["mean_sharpe"] - base_stats["mean_sharpe"]
    if "mean_return" in base_stats and "mean_return" in cand_stats:
        out["delta_return"] = cand_stats["mean_return"] - base_stats["mean_return"]
    out["direction_raw"] = direction_from_delta(
        out.get("delta_sharpe"), out.get("delta_return")
    )
    sharpe_tiny, return_tiny = _noise_flags(out)
    out["within_noise"] = sharpe_tiny and return_tiny
    # 两指标差异**都**落在噪声带内 ⇒ 降级为「持平」，不给噪声级差异挂方向标签
    out["direction"] = "持平" if out["within_noise"] else out["direction_raw"]
    return out


def _noise_flags(verdict: dict) -> tuple[bool, bool]:
    """(ΔSharpe 在噪声带内?, Δ总收益 在噪声带内?)。

    带宽是**相对**的 —— 取同层基线离散度（max−min）的 `NOISE_SPREAD_FRACTION`，
    不用绝对阈值，否则换窗口/换量纲就要重调常数。
    """
    base_stats = verdict.get("base", {})
    flags: list[bool] = []
    for delta_key, lo_key, hi_key in (
        ("delta_sharpe", "min_sharpe", "max_sharpe"),
        ("delta_return", "min_return", "max_return"),
    ):
        delta = verdict.get(delta_key)
        if delta is None or lo_key not in base_stats or hi_key not in base_stats:
            flags.append(False)
            continue
        spread = base_stats[hi_key] - base_stats[lo_key]
        flags.append(spread > 0 and abs(delta) < NOISE_SPREAD_FRACTION * spread)
    return flags[0], flags[1]


def magnitude_note(verdict: dict) -> str:
    """幅度提示：说明差异为何被判为噪声（相对口径）。"""
    sharpe_tiny, return_tiny = _noise_flags(verdict)
    if not (sharpe_tiny or return_tiny):
        return ""
    base_stats = verdict.get("base", {})
    parts: list[str] = []
    if sharpe_tiny:
        spread = base_stats["max_sharpe"] - base_stats["min_sharpe"]
        parts.append(
            f"ΔSharpe={verdict['delta_sharpe']:+.3f} 小于同层基线离散度 {spread:.2f} 的 1/4"
        )
    if return_tiny:
        spread = base_stats["max_return"] - base_stats["min_return"]
        parts.append(
            f"Δ总收益={verdict['delta_return']:+.2f}pp 小于同层基线离散度 {spread:.2f}pp 的 1/4"
        )
    return (
        "⚠️ 幅度提示：" + "；".join(parts) + " ⇒ 该方向差异接近噪声，不足以支撑因子族结论。"
    )


def decide_scheme(d3: str | None, d5: str | None) -> str:
    """双窗口方向一致性判据 —— T3 方案 A 是否成立。

    只有「两窗口方向一致」才算数：一致改善 → 成立；一致退步 → 不成立；
    两窗口一致持平 → 无可测差异，同样**不判成立**；
    方向不一致或任一窗口数据缺失 → **不判成立**（宁可不判，也不拿单窗口结论下判断）。
    """
    if d3 == d5 and d3 in ("改善", "退步"):
        if d3 == "改善":
            return "✅ **方案 A 成立**：htf+mr 替换 mom+tr 在 3y 与 5y 两窗口方向一致改善 WF-001。"
        return "❌ **方案 A 不成立**：两窗口一致劣于 WF-001 → 退回方案 B（纯 htf）或诊断因子族。"
    if d3 == d5 and d3 == "持平":
        return (
            "⚪ **两窗口均持平**：htf+mr 相对基线在同层内无可测差异（差异落在噪声带内）⇒ "
            "因子族替换不构成改进，**不判方案 A 成立**。"
        )
    if d3 is None or d5 is None:
        return "⏳ **暂不可判**：某窗口基线/变体缺失，待数据补全后重算。"
    return (
        f"🔶 **方向不一致**（3y={d3} / 5y={d5}）：疑似窗口依赖或因子族在不同 regime 下"
        "表现分化，需诊断，不判方案 A 成立。"
    )


def _fmt_stat(mean: float | None, lo: float | None, hi: float | None, pct: bool = False) -> str:
    """`均值 (min~max)`；缺数据返回 `—`。"""
    if mean is None:
        return "—"
    if pct:
        body = f"{_fmt_pct(mean)} ({_fmt_pct(lo)}~{_fmt_pct(hi)})" if lo is not None and hi is not None else _fmt_pct(mean)
    else:
        body = f"{_fmt_sharpe(mean)} ({_fmt_sharpe(lo)}~{_fmt_sharpe(hi)})" if lo is not None and hi is not None else _fmt_sharpe(mean)
    return body


def build_stratified_table(label: str, verdict: dict | None) -> list[str]:
    """分层对比表（**主判据**）。"""
    rows = [f"### {label}", ""]
    if verdict is None:
        rows.append("（不可判：基线缺失、层值缺失，或任一侧该层为空）")
        return rows
    axis, layer = verdict["axis"], verdict["layer"]
    bs, cs = verdict["base"], verdict["cand"]
    rows.append(
        f"分层轴 `{axis}={layer}`（取**基线所在层**；两臂各取臂内均值，同层同口径）"
    )
    rows.append("")
    rows.append("| 臂 | n | 均值 Sharpe (min~max) | 均值 总收益 (min~max) | 变体 |")
    rows.append("|---|---|---|---|---|")
    rows.append(
        f"| 基线臂（{verdict['baseline_name']} 所在族） | {bs['n']} | "
        f"{_fmt_stat(bs.get('mean_sharpe'), bs.get('min_sharpe'), bs.get('max_sharpe'))} | "
        f"{_fmt_stat(bs.get('mean_return'), bs.get('min_return'), bs.get('max_return'), pct=True)} | "
        f"{', '.join(verdict['base_layer_variants'])} |"
    )
    rows.append(
        f"| htf_mr 臂 | {cs['n']} | "
        f"{_fmt_stat(cs.get('mean_sharpe'), cs.get('min_sharpe'), cs.get('max_sharpe'))} | "
        f"{_fmt_stat(cs.get('mean_return'), cs.get('min_return'), cs.get('max_return'), pct=True)} | "
        f"{', '.join(verdict['cand_layer_variants'])} |"
    )
    rows.append("")
    ds, dr = verdict.get("delta_sharpe"), verdict.get("delta_return")
    head = (
        f"Δ(htf_mr − 基线)：Sharpe {ds:+.3f} / 总收益 {dr:+.2f}pp"
        if ds is not None and dr is not None
        else "Δ(htf_mr − 基线)"
    )
    tail = f" → 方向 **{verdict['direction']}**"
    if verdict.get("direction_raw") and verdict["direction_raw"] != verdict["direction"]:
        tail += f"（原始符号方向 {verdict['direction_raw']}，因幅度落在噪声带内降级）"
    rows.append(head + tail)
    note = magnitude_note(verdict)
    if note:
        rows.append("")
        rows.append(note)
    return rows


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

    # **主判据**：同 horizon 层内的臂均值对比（issue #194）
    s3 = stratified_verdict(wf001_3y_var, htf_3y_var)
    s5 = stratified_verdict(wf001_5y_var, htf_5y_var)

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

    # 方向一致性判定：判决只走分层口径；朴素口径仅作参照打印
    naive3 = direction(v3)
    naive5 = direction(v5)
    d3 = s3["direction"] if s3 else None
    d5 = s5["direction"] if s5 else None
    scheme = decide_scheme(d3, d5)

    # 组装 markdown
    out = ["# T3 方案 A 双窗口对比结论", ""]
    out.append(
        "> 生成脚本：`scripts/compare_t3_dual_window.py`。"
        "**判据 = 同 `horizon` 层内的臂均值对比**（htf_mr 臂相对基线臂的改善方向须在 3y 与 5y 两窗口一致）。"
    )
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
    out.append("## 三、同 horizon 分层对比（**主判据**）")
    out.append("")
    out.append(
        "> issue #194：原判据「最佳 htf_mr vs 单个基线变体」既非 best-vs-best，又**跨了 `h` 轴**"
        "（3y 的最佳 `WF-H07` 是 h=10，基线 `WF-001` 是 h=3）⇒ 会把**持有期效应**误读成**因子族效应**。"
        "下表以**基线所在层**为准，两臂各取臂内**均值**，同层同口径。"
    )
    out.append("")
    out.extend(build_stratified_table("3y 窗口", s3))
    out.append("")
    out.extend(build_stratified_table("5y 窗口", s5))
    out.append("")
    out.append("## 四、朴素口径（best-vs-基线，**跨 horizon，不可单独引用**）")
    out.append("")
    out.append(
        f"- **3y 窗口**：最佳 htf_mr = `{v3.get('best')}`，Sharpe {_fmt_sharpe(v3.get('best_sharpe'))} "
        f"vs 基线 {_fmt_sharpe(v3.get('base_sharpe'))}；总收益 {_fmt_pct(v3.get('best_return'))} "
        f"vs 基线 {_fmt_pct(v3.get('base_return'))} → 方向 **{naive3}**"
    )
    out.append(
        f"- **5y 窗口**：最佳 htf_mr = `{v5.get('best')}`，Sharpe {_fmt_sharpe(v5.get('best_sharpe'))} "
        f"vs 基线 {_fmt_sharpe(v5.get('base_sharpe'))}；总收益 {_fmt_pct(v5.get('best_return'))} "
        f"vs 基线 {_fmt_pct(v5.get('base_return'))} → 方向 **{naive5}**"
    )
    out.append("")
    out.append(
        "⚠️ 本节的「方向」是 **best-of-8 vs single 且跨 horizon** 的读数，"
        "**不构成因子族结论**（3y 的「改善」即由此产生）。判决只走 §三。"
    )
    out.append("")
    out.append("## 五、方案 A 判决（依 §三 分层口径）")
    out.append("")
    out.append(scheme)
    out.append("")
    if parse_notes:
        out.append("## 六、解析备注")
        out.append("")
        out.append("\n".join(parse_notes))
        out.append("")
    out.append("---")
    out.append("> 注：DSR 全负属已知 alpha 赤字（R3）；若方案 A 成立仅代表「因子族替换方向正确」，不代表样本外已盈利。")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(out), encoding="utf-8")
    print(f"✅ 对比结论已写出: {args.out}")
    print(f"   分层口径方向 3y={d3} / 5y={d5}")
    print(f"   朴素口径方向 3y={naive3} / 5y={naive5}（不可单独引用）")
    print(f"   判决: {scheme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
