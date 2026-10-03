"""planb 触发日裁决：**基线 vs 候选**一表对照生成器（只读、零口径变更）。

## 为什么需要这个（2026-10-03 触发日预演实证）

裁决单 §三 要求「ΔSharpe/ΔDSR/ΔPBO **对照生产 yaml 基线**（0.3/0/0/0/0/0.3/0）记录不否决」，
但代码上 `_walkforward_grid_variants(profile)` **每次只返回一套变体集** ⇒ 跑
`--grid-profile planb_v*` 时**那次 run 里没有基线臂**（基线 = `stable_plus` 的第 0 条臂
`WF-001`，与 `config/thresholds.yaml` 逐位一致）。

跑批现实（复核后修正了此前「跨 run 不可比」的过强判断）：
- **drift 只在两次 run 跨日时成立**。触发日当天连跑两次（基线 + 候选）都在
  `data_update`（`0 8 * * 1-5` UTC = 北京 16:00）**之前** ⇒ **同一个 DB 快照**。
- CSCV 切分只按时间块（`combinations(range(s), s//2)`，与变体集无关）⇒ 两次 run 的
  切分**逐位相同**。
⇒ 两次 run + 本脚本合并，**既不改 PBO 语义、也不改任何门禁常量**，即可得到 §三 要的对照。

本脚本做的事：读「基线 run 的 report.md」+「候选 run 的 report.md」，输出**一张**裁决表：

| 角色 | 变体 | 7 维权重 | Sharpe | 总收益 | 暴露归一化 | ΔSharpe | Δ总收益 | DSR | PBO | 门禁 |
|---|---|---|---|---|---|---|---|---|---|---|
| 基线 | WF-001 | mom0.3+tr0.3 其余0 | 0.85 | 12% | 30% | — | — | 0.94 | 0.21 | ✅ |
| 候选 | WB-V1-10x3 | 摘mom，摊qual/val/vol | 1.02 | 18% | 45% | **+0.17** | **+6pp** | 0.94 | 0.21 | ✅ |

要点：
- **门禁判定用绝对阈值**（DSR>1.0、0<PBO<0.5 等，取自 gate sidecar），**不替人下结论**；
- **Δ 只做记录**（裁决单原文「记录不否决」），**不作为否决项**；
- 两份 report 的 PBO/DSR 是**各自变体集独立算出的**（grid 级），本脚本**不做算术混合**，
  避免污染 PBO 语义。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 7 维列名（PR #300 起 report 表新增 qual/val/vol/mr/htf 五列）
SEVEN_DIM = ("mom", "tr", "qual", "val", "vol", "mr", "htf")
METRIC_COLS = ("Sharpe", "总收益", "暴露归一化收益", "周期数")


def _num(text: str) -> float | None:
    """把报告单元格拉成 float；`-` / 空 ⇒ None。"""
    t = (text or "").strip().replace("−", "-").replace(",", "")
    if t in {"", "-"}:
        return None
    pct = t.endswith("%")
    t = t.rstrip("%").strip()
    try:
        val = float(t)
    except ValueError:
        return None
    return val / 100.0 if pct else val


def parse_grid_table(report_path: Path) -> dict[str, dict]:
    """解析 report.md 的「多变体 CSCV」逐变体表 -> {variant_id: {列名: 值}}。

    按**表头名**定位列（不按位置），故列数变化不会错位。
    """
    if not report_path.exists():
        return {}
    lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## 多变体 CSCV")
    except StopIteration:
        return {}

    header: list[str] | None = None
    out: dict[str, dict] = {}
    for ln in lines[start + 1 :]:
        s = ln.strip()
        if not s.startswith("|"):
            if header is not None and out:
                break  # 表尾
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):  # 分隔行
            continue
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells))
        vid = row.get("变体") or row.get("臂") or row.get("variant")
        if not vid:
            continue
        out[vid] = row
    return out


def parse_grid_metrics(report_path: Path) -> dict[str, float | None]:
    """取 grid 级 DSR / PBO（整组一个值，**不是逐臂**）。"""
    out: dict[str, float | None] = {"dsr": None, "pbo": None, "periods": None}
    if not report_path.exists():
        return out
    text = report_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Grid DSR：\s*([-\d.]+)", text)
    if m:
        out["dsr"] = float(m.group(1))
    m = re.search(r"Grid PBO：\s*([\d.]+)%", text)
    if m:
        out["pbo"] = float(m.group(1)) / 100.0
    m = re.search(r"对齐周期数：\s*(\d+)", text)
    if m:
        out["periods"] = float(m.group(1))
    return out


def _weights_7d(row: dict) -> str:
    """把 7 维列渲染成一行人话（如 `mom0.3+tr0.3` / `摘mom,qual/val/vol 各0.4`）。"""
    parts = []
    for col in SEVEN_DIM:
        if col not in row:
            continue
        v = _num(row[col])
        if v:
            parts.append(f"{col}{v:g}")
    return "+".join(parts) if parts else "（全 0）"


def _fmt(val: float | None, *, pct: bool = False, delta: bool = False) -> str:
    if val is None:
        return "-"
    sign = "+" if (delta and val > 0) else ""
    return f"{sign}{val * 100:.2f}%" if pct else f"{sign}{val:.2f}"


def _gate_verdict(dsr: float | None, pbo: float | None) -> str:
    """绝对门禁判定（**取自裁决单 §三 的 fail-closed 常量，不放宽**）。

    DSR>1.0、0<PBO<0.5。缺值 ⇒ 标「缺证据」而不是默认通过。
    """
    if dsr is None or pbo is None:
        return "缺证据"
    ok = dsr > 1.0 and 0.0 < pbo < 0.5
    if ok:
        return "✅ DSR>1.0 且 0<PBO<0.5"
    if dsr <= 1.0 and not (0.0 < pbo < 0.5):
        return f"❌ DSR={dsr:.2f} 且 PBO={pbo:.1%}"
    if dsr <= 1.0:
        return f"❌ DSR={dsr:.2f} ≤ 1.0"
    return f"❌ PBO={pbo:.1%} 越界"


def build_table(
    baseline_report: Path, candidate_report: Path, candidate_prefix: str = "WB-"
) -> tuple[list[str], dict]:
    """生成裁决对照表（markdown 行 + 结构化数据）。"""
    base_rows = parse_grid_table(baseline_report)
    cand_rows = parse_grid_table(candidate_report)
    base_m = parse_grid_metrics(baseline_report)
    cand_m = parse_grid_metrics(candidate_report)

    # 基线臂 = stable_plus 的 WF-001（与 config/thresholds.yaml 逐位一致）
    base = base_rows.get("WF-001")
    if base is None and base_rows:
        base = next(iter(base_rows.values()))
    if base is None:
        raise SystemExit(f"基线 report 未解析到任何变体行：{baseline_report}")

    base_sh = _num(base.get("Sharpe"))
    base_tr = _num(base.get("总收益"))

    lines = [
        "| 角色 | 变体 | 7 维权重 | Sharpe | 总收益 | 暴露归一化 | ΔSharpe | Δ总收益 | DSR | PBO | 绝对门禁 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    data = {
        "baseline": {"variant": base.get("变体"), "sharpe": base_sh, "total_return": base_tr,
                     "dsr": base_m["dsr"], "pbo": base_m["pbo"]},
        "candidates": [],
    }
    lines.append(
        f"| 基线 | {base.get('变体')} | {_weights_7d(base)} | {_fmt(base_sh)} | "
        f"{_fmt(base_tr, pct=True)} | {_fmt(_num(base.get('暴露归一化收益')), pct=True)} | "
        f"— | — | {_fmt(base_m['dsr'])} | {_fmt(base_m['pbo'], pct=True)} | "
        f"{_gate_verdict(base_m['dsr'], base_m['pbo'])} |"
    )

    for vid, row in cand_rows.items():
        if not vid.startswith(candidate_prefix):
            continue
        sh = _num(row.get("Sharpe"))
        tr = _num(row.get("总收益"))
        d_sh = None if (sh is None or base_sh is None) else sh - base_sh
        d_tr = None if (tr is None or base_tr is None) else tr - base_tr
        lines.append(
            f"| 候选 | {vid} | {_weights_7d(row)} | {_fmt(sh)} | {_fmt(tr, pct=True)} | "
            f"{_fmt(_num(row.get('暴露归一化收益')), pct=True)} | "
            f"{_fmt(d_sh, delta=True)} | {_fmt(d_tr, pct=True, delta=True)} | "
            f"{_fmt(cand_m['dsr'])} | {_fmt(cand_m['pbo'], pct=True)} | "
            f"{_gate_verdict(cand_m['dsr'], cand_m['pbo'])} |"
        )
        data["candidates"].append(
            {"variant": vid, "sharpe": sh, "total_return": tr,
             "d_sharpe": d_sh, "d_total_return": d_tr,
             "weights": _weights_7d(row)}
        )
    return lines, data


def main() -> int:
    ap = argparse.ArgumentParser(
        description="planb 触发日裁决：基线 vs 候选一表对照（只读，不改任何口径）"
    )
    ap.add_argument("--baseline-report", required=True, type=Path,
                    help="stable_plus 那次 run 的 report.md（含 WF-001 基线臂）")
    ap.add_argument("--candidate-report", required=True, type=Path,
                    help="planb_v* 那次 run 的 report.md")
    ap.add_argument("--candidate-profile", default="", help="候选 profile 名（如 planb_v1），仅标注")
    ap.add_argument("--prefix", default="WB-", help="候选变体 id 前缀（默认 WB-）")
    ap.add_argument("--out", type=Path, help="写入该 markdown 文件（默认打到 stdout）")
    args = ap.parse_args()

    lines, data = build_table(args.baseline_report, args.candidate_report, args.prefix)
    header = [
        f"# 触发日裁决对照 · {args.candidate_profile or args.candidate_report.parent.name}",
        "",
        "> 由 `compare_planb_vs_baseline.py` 生成（**只读合并两份 report，不重跑、不改口径**）。",
        "> **Δ 仅记录不否决**（裁决单 §三 原文）；门禁列是绝对阈值判定（DSR>1.0、0<PBO<0.5）。",
        "> DSR/PBO 是**各自变体集独立算出的 grid 级值**，本表不做算术混合（避免污染 PBO 语义）。",
        "> 前提：两次 run 在**同一 DB 快照**下完成（触发日当天、`data_update` 16:00 之前连跑）。",
        "",
    ]
    text = "\n".join(header + lines) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"已写出 {args.out}")
        print(f"基线 {data['baseline']['variant']} Sharpe={data['baseline']['sharpe']} "
              f"DSR={data['baseline']['dsr']} PBO={data['baseline']['pbo']}")
        for c in data["candidates"]:
            print(f"候选 {c['variant']}  ΔSharpe={c['d_sharpe']}  Δ总收益={c['d_total_return']}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
