#!/usr/bin/env python3
"""
extract_gate_summary.py — walk-forward gate 产物一键摘要（item 2 读数 + item 3 Part A）

解析三处来源，产出紧凑 markdown：
  1. gate.json（双门 sidecar）—— 顶层判级：deflated_sharpe / pbo / both_pass / effective_symbols 等
  2. report.md（人工报告）—— 「## 多变体 CSCV」变体表：每 variant 的 top/Sharpe/总收益/周期数
  3. 由 1+2 推导 **WF-001(top_n=10) vs WF-B01(top_n=5)** 的 top_n 对比（item 3 Part A 零成本证据）

设计要点（为什么两边都要读）：
  - gate.json 的 `grid_diagnostics` 只含 best_variant/worst_variant(ID)、variant_dispersion_*、worst_periods，
    **不含逐 variant 行**（variant_rows 只进 report.md）。所以 top_n 10vs5 必须从 report.md 取。
  - report.md 在 gate.json **之前**写入（cli.py:6985 report → :6990 gate），故见到 gate.json 时 report.md 必在。

只读、不改任何策略参数。产物仅用于证据汇报。

用法：
  python3 scripts/extract_gate_summary.py \
      --gate  /opt/aqsp/data/gate_run/walkforward_gate.json \
      --report /opt/aqsp/data/gate_run/report.md \
      --output /opt/aqsp/data/gate_run/gate_summary.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# report.md 变体表：| WF-001 | 0.3 | 0.3 | 60 | 3 | 10 | 1.23 | 4.56% | 19 |
_VARIANT_ROW = re.compile(r"^\|\s*(WF-[A-Za-z0-9]+)\s*\|(.+)\|\s*$")
_TOP_N_COMPARE = ("WF-001", "WF-B01")  # top_n=10 vs top_n=5（stable_plus 网格内）


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _to_float(raw: str) -> float | None:
    text = raw.replace("%", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_report_variants(report_text: str) -> list[dict]:
    """解析 report.md 的「多变体 CSCV」变体表。

    表头：| 变体 | mom | tr | lb | h | top | Sharpe | 总收益 | 周期数 |
    """
    variants: list[dict] = []
    for line in report_text.splitlines():
        match = _VARIANT_ROW.match(line)
        if not match:
            continue
        variant_id = match.group(1)
        cells = _cells(line)
        # cells[0]=variant_id, 1=mom, 2=tr, 3=lb, 4=h, 5=top, 6=Sharpe, 7=总收益, 8=周期数
        if len(cells) < 9:
            continue
        variants.append(
            {
                "variant_id": variant_id,
                "mom": cells[1],
                "tr": cells[2],
                "lookback": cells[3],
                "horizon": cells[4],
                "top_n": cells[5],
                "sharpe": _to_float(cells[6]),
                "total_return": _to_float(cells[7]),
                "periods": cells[8],
            }
        )
    return variants


def _fmt(value: float | None, spec: str = ".4f") -> str:
    return "N/A" if value is None else format(value, spec)


def _fmt_pct(value: float | None) -> str:
    """值为比率（0.3857 → 38.57%），用于 gate.json 的 pbo。"""
    return "N/A" if value is None else f"{value:.2%}"


def _fmt_pct_num(value: float | None) -> str:
    """值本身已是百分数（12.34 → 12.34%），用于 report.md 的「总收益」列。

    report.md 里总收益渲染为 `{total_return:.2%}` → 「12.34%」；_to_float 剥掉 % 后
    得 12.34（已是百分数），故不能再套 `:.2%`，否则会变成 1234%。
    """
    return "N/A" if value is None else f"{value:.2f}%"


def build_summary(gate: dict, variants: list[dict], report_text: str) -> str:
    lines: list[str] = ["# Walk-Forward Gate 摘要（item 2 首个可信 CSCV 读数）", ""]

    # ---- 双门判级 ----
    dsr = gate.get("deflated_sharpe")
    pbo = gate.get("pbo")
    both = gate.get("both_pass")
    verdict = "PASS" if both else "FAIL"
    lines += [
        "## 双门判定",
        "",
        f"- **结论**：{'✅' if both else '❌'} **{verdict}**",
        f"- DSR（>1.0 为 PASS）：{_fmt(dsr)}（dsr_pass={gate.get('dsr_pass')}）",
        f"- PBO（<50% 且非占位为 PASS）：{_fmt_pct(pbo)}（pbo_pass={gate.get('pbo_pass')}, pbo_valid={gate.get('pbo_valid')}）",
        f"- 区间：{gate.get('data_start')} ~ {gate.get('data_end')}，对齐周期数 n_periods={gate.get('n_periods')}",
        f"- 口径：grid_profile={gate.get('grid_profile')}，memory_mode={gate.get('memory_mode')}，"
        f"batch={gate.get('stream_batch_size')}，skip_pit={gate.get('skip_pit_financials')}",
        f"- **effective_symbols={gate.get('effective_symbols')}**（⚠️ =3 是废跑；正常量级 ~4400~5500）",
        f"- run_date={gate.get('run_date')}",
        "",
    ]

    # ---- CSCV 可信度（老大决策：接受 3 年窗口，读数须显性标注 degraded）----
    diags = gate.get("grid_diagnostics") or {}
    rel = diags.get("cscv_reliability")
    warns = diags.get("cscv_warnings") or []
    lines += ["## CSCV 可信度", ""]
    if rel is None:
        lines += ["- ⚠️ gate.json 未含 `cscv_reliability`（grid_diagnostics 缺失）", ""]
    else:
        flag = "✅ ok" if rel == "ok" else "⚠️ **degraded**"
        lines += [f"- **cscv_reliability = {flag}**"]
        lines += [
            f"- 样本量：T(对齐周期)={gate.get('n_periods')}，n_variants={diags.get('n_variants')}，"
            f"s={diags.get('s')}，block_size={diags.get('block_size')}，t_trimmed={diags.get('t_trimmed')}",
        ]
        if rel != "ok":
            lines += [
                "",
                "> ⚠️ **口径警示**：`block_size < 4` 或 `n_variants < 8` 时 CSCV 置 degraded"
                "（walk_forward.py:1042-1053）。3 年窗口 T=19 → block_size=2，"
                "**本读数统计分辨率不足**（train/test Sharpe 估计噪声大、PBO 方差高），"
                "**但 degraded 只关乎 CSCV 分辨率，不代表策略无效**。",
                "> 解除需 T≥40（5 年窗口）—— prod 库 2021-09~2023-08 无数据，须先回填；"
                "老大已决策接受 3 年，不再回填。",
            ]
        for w in warns:
            lines.append(f"  - warning: {w}")
        lines.append("")

    # ---- 变体全表 ----
    if variants:
        lines += [
            "## 多变体 CSCV 变体表（来自 report.md）",
            "",
            "| 变体 | mom | tr | lookback | horizon | top_n | Sharpe | 总收益 | 周期数 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for v in variants:
            lines.append(
                f"| {v['variant_id']} | {v['mom']} | {v['tr']} | {v['lookback']} | "
                f"{v['horizon']} | {v['top_n']} | {_fmt(v['sharpe'], '.2f')} | "
                f"{_fmt_pct_num(v['total_return'])} | {v['periods']} |"
            )
        lines.append("")

    # ---- item 3 Part A：top_n 10 vs 5 ----
    by_id = {v["variant_id"]: v for v in variants}
    pair = [by_id.get(vid) for vid in _TOP_N_COMPARE]
    lines += ["## item 3 Part A：top_n 10 vs 5（WF-001 vs WF-B01）", ""]
    if all(pair):
        a, b = pair  # a=WF-001 top_n=10, b=WF-B01 top_n=5
        lines += [
            f"| 指标 | WF-001 (top_n={a['top_n']}) | WF-B01 (top_n={b['top_n']}) | 差值 (10−5) |",
            "|---|---|---|---|",
        ]
        for label, key, fmt in (
            ("Sharpe", "sharpe", lambda x: format(x, ".2f")),
            ("总收益", "total_return", lambda x: f"{x:.2f}%"),
        ):
            va, vb = a[key], b[key]
            delta = "N/A" if (va is None or vb is None) else fmt(va - vb)
            lines.append(
                f"| {label} | {'N/A' if va is None else fmt(va)} | "
                f"{'N/A' if vb is None else fmt(vb)} | {delta} |"
            )
        better = None
        if a["sharpe"] is not None and b["sharpe"] is not None:
            better = "WF-001(top_n=10)" if a["sharpe"] > b["sharpe"] else "WF-B01(top_n=5)"
        lines += [
            "",
            f"- 其余维度相同（mom/tr/lookback/horizon = {a['mom']}/{a['tr']}/{a['lookback']}/{a['horizon']}），**唯一变量是 top_n**。",
            f"- Sharpe 更优者：{better or 'N/A'}",
            "- ⚠️ 本组为同一 OOS 样本内的网格对比，**仅作证据**，不构成改参依据；改参须走 PR。",
            "",
        ]
    else:
        missing = [v for v, p in zip(_TOP_N_COMPARE, pair) if p is None]
        lines += [f"- ⚠️ 未在 report.md 变体表中找到：{', '.join(missing)}（可能网格未含该 variant）", ""]

    # ---- 报告 TL;DR ----
    # 标题行是「## TL;DR」，内容行是「**TL;DR**: ...」——只取内容行
    tldr = [ln for ln in report_text.splitlines() if "**TL;DR**" in ln]
    if tldr:
        lines += ["## report.md TL;DR", "", tldr[0].lstrip("- ").strip(), ""]

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    gate_path, report_path = Path(args.gate), Path(args.report)
    if not gate_path.exists():
        print(f"[ERR] gate.json 不存在: {gate_path}")
        return 2
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    variants = parse_report_variants(report_text)
    print(f"[info] gate.json 已解析；report.md 变体行数={len(variants)}")

    summary = build_summary(gate, variants, report_text)
    Path(args.output).write_text(summary, encoding="utf-8")
    print(summary)
    print(f"[ok] 摘要已写出: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
