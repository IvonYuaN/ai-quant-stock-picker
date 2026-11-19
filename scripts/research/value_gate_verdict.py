#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""价值维度 gate 判决单渲染器（§四 五条判据，机械判读、fail-closed）。

## 为什么必须有这个
§四 五条判生死（`memory/2026-11-16.md` L648 权威定义，预注册 fail-closed）：
    ① DSR_def 过 且 ΔDSR_def > 0
    ② PBO 不恶化（基线 47.62%）
    ③ 超额 vs 同截面池等权为正（须叠 `gate_excess_report.py`）
    ④ 市值中性化后增量仍显著
    ⑤ T3 双窗同号
    任一不满足 = FAIL；gate FAIL 即否决，不回头调参（红线）。

`gate` 只出**绝对口径**（`deflated_sharpe` / `pbo` / `both_pass`），五条里有几条本轮
3y 单窗 gate **根本不产出**（④市值中性增量、⑤双窗同号，以及 ①的 ΔDSR、③的超额）。
临场手算正是 §24 两次「看起来对」的算法错误来源 ⇒ 判读必须机械、可复现、缺项显式标
NOT_PRODUCED，**绝不自己重算超额基准**（`aqsp.backtest.excess` docstring 明写同一产物
翻过 `+55pp` 与 `−6.9pp` 两个相反结论）。

## 本工具只做三件事
1. 读 gate.json（必需）→ 判 ① 绝对 DSR、② PBO（对照基线参考值）；
2. 读 excess 文件（可选，runner 用 `gate_excess_report.py` 跑出的回流数字）→ 判 ③；
   缺了显式标 NOT_PRODUCED，不冒充已判、不临场重算基准；
3. ④⑤ 本轮 3y 单窗 gate 不产出 ⇒ 恒标 NOT_PRODUCED（需另跑市值中性对照 / T3 双窗）。

**fail-closed**：任一条产出即 FAIL ⇒ 总体 REJECT；有 NOT_PRODUCED 且无 FAIL ⇒
INCOMPLETE（fail-closed，不宣布「价值维度成立」）；全 PASS（现实里因 ④⑤ 本轮不产出，
至多 INCOMPLETE）⇒ 才 HOLD。本工具**不替 gate 下「价值维度成立」的结论**——那是
alpha 判断，不是渲染器的职责。

## 用法
    python scripts/research/value_gate_verdict.py \
        --gate-json /opt/aqsp-runner/gate_run/walkforward_gate.json \
        --baseline-pbo 0.4762 \
        [--excess /tmp/value_excess.json] \
        [--baseline-gate-json /path/to/baseline_wf001.gate.json]

excess 文件 schema（任一形态，读不出即 NOT_PRODUCED）：
    {"value_excess_ann": 0.02, "value_excess_mean": 0.01, "value_excess_t": 1.5}
    {"arms": {"WF-V01": {"excess_ann": 0.02}, ...}}   # 取各臂 excess_ann 中位数
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Optional

# 门槛常量与 `src/aqsp/walkforward_gate.py` 权威值一致（单一事实源，只读不复算）：
MIN_DSR_DEFAULT = 1.0        # walkforward_gate.MIN_DSR
MAX_PBO_DEFAULT = 0.5        # walkforward_gate.MAX_PBO
BASELINE_PBO_DEFAULT = 0.4762  # §四 ② 基线 PBO（10-03 stable_plus 轮 47.62%）

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT = "NOT_PRODUCED"


def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _num(value: object) -> Optional[float]:
    """把 gate.json 里的指标取成 float；None/脏值返回 None（缺项，不冒充 0）。"""
    if value is None:
        return None
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if v != v else v  # NaN -> None


def _fmt(v: Optional[float], kind: str = "f4") -> str:
    if v is None:
        return "-"
    if kind == "pct":
        return f"{v:+.2%}"
    if kind == "pct2":
        return f"{v:.2%}"
    return f"{v:+.4f}" if kind == "f4" else f"{v:.4f}"


def _load_excess(path: Optional[Path]) -> Optional[dict]:
    """读 excess 回流数字，返回 {'ann','mean','t'}；缺/脏 => None（NOT_PRODUCED）。"""
    if path is None:
        return None
    d = _load_json(path)
    if not isinstance(d, dict):
        return None
    # 形态一：扁平 value_excess_*
    if "value_excess_ann" in d or "value_excess_mean" in d:
        return {
            "ann": _num(d.get("value_excess_ann")),
            "mean": _num(d.get("value_excess_mean")),
            "t": _num(d.get("value_excess_t")),
        }
    # 形态二：arms dict，取各臂 excess_ann 的中位数
    arms = d.get("arms")
    if isinstance(arms, dict):
        vals = [_num(a.get("excess_ann")) for a in arms.values() if isinstance(a, dict)]
        vals = [v for v in vals if v is not None]
        if vals:
            return {"ann": float(statistics.median(vals)), "mean": None, "t": None,
                    "n_arms": len(vals)}
    return None


def judge(gate: dict, base: Optional[dict], excess: Optional[dict],
          baseline_pbo: float, min_dsr: float, max_pbo: float) -> tuple[list[dict], list[str]]:
    """逐条判五条，返回 (rows, ctx)：rows=[{'no','name','status','evidence'}]，ctx 为口径脚注。"""
    dsr = _num(gate.get("deflated_sharpe"))
    pbo = _num(gate.get("pbo"))
    dsr_pass = gate.get("dsr_pass")
    pbo_pass = gate.get("pbo_pass")
    cscv_ok = gate.get("cscv_reliable")
    n_var = gate.get("n_variants")
    grid = gate.get("grid_profile", "?")

    rows: list[dict] = []

    # ① DSR_def 过 且 ΔDSR_def > 0
    abs_ok = (dsr_pass is True) or (dsr is not None and dsr > min_dsr)
    base_dsr = _num(base.get("deflated_sharpe")) if base else None
    delta = (dsr - base_dsr) if (dsr is not None and base_dsr is not None) else None
    if delta is not None:
        c1 = STATUS_PASS if (abs_ok and delta > 0) else STATUS_FAIL
        c1_ev = (f"DSR={_fmt(dsr)}({'过' if abs_ok else '不过'}); ΔDSR={delta:+.4f}"
                 f"(候选 vs 基线 {_fmt(base_dsr)})")
    elif not abs_ok:
        # 绝对 DSR 不过 ⇒ 一票否决（无需 Δ 对照即可判 FAIL）
        c1 = STATUS_FAIL
        c1_ev = f"绝对DSR={_fmt(dsr)}(≤门槛 {_fmt(min_dsr, 'f2')}，gate dsr_pass=False) ⇒ 一票否决"
    else:
        # 绝对过了但缺 Δ 对照 ⇒ fail-closed，不冒充已判
        c1 = STATUS_NOT
        c1_ev = (f"绝对DSR={_fmt(dsr)}(过); "
                 f"ΔDSR=NOT_PRODUCED（需 --baseline-gate-json 对照 WF-001）")
    rows.append({"no": "①", "name": "DSR_def 过且ΔDSR>0", "status": c1, "evidence": c1_ev})

    # ② PBO 不恶化（基线 47.62%）且 gate 自身 pbo_pass
    if pbo is not None:
        not_worse = pbo <= baseline_pbo
        gate_ok = (pbo_pass is True) or (pbo_pass is None and pbo < max_pbo)
        c2 = STATUS_PASS if (not_worse and gate_ok) else STATUS_FAIL
        gate_label = "PASS" if pbo_pass is True else ("FAIL" if pbo_pass is False else "n/a")
        trend = "未恶化" if not_worse else "恶化"
        c2_ev = (f"PBO={_fmt(pbo, 'pct2')}(gate {gate_label}) "
                 f"vs 基线 {_fmt(baseline_pbo, 'pct2')} → {trend}")
    else:
        c2, c2_ev = STATUS_NOT, "PBO=NOT_PRODUCED（gate.json 缺 pbo/pbo_valid）"
    rows.append({"no": "②", "name": "PBO不恶化(≤基线47.62%)", "status": c2, "evidence": c2_ev})

    # ③ 超额 vs 同截面池等权为正
    if excess is not None and excess.get("ann") is not None:
        ea = excess["ann"]
        em = excess.get("mean")
        et = excess.get("t")
        c3 = STATUS_PASS if ea > 0 else STATUS_FAIL
        c3_ev = (f"超额年化中位={_fmt(ea,'pct')}"
                  + (f" / 逐期均值={_fmt(em,'pct')}" if em is not None else "")
                  + (f" / t={et:+.2f}" if et is not None else "")
                  + ("（全市场等权基准，方向与量级、不抠小数点）"))
    else:
        c3 = STATUS_NOT
        c3_ev = "超额=NOT_PRODUCED（需 runner gate_excess_report.py 回流 --excess）"
    rows.append({"no": "③", "name": "超额vs池等权为正", "status": c3, "evidence": c3_ev})

    # ④ 市值中性化后增量仍显著 —— 本轮 3y 单窗 gate 不产出（恒 NOT）
    rows.append({
        "no": "④", "name": "市值中性化后增量仍显著", "status": STATUS_NOT,
        "evidence": "NOT_PRODUCED（本轮 3y 单窗 planb_value gate 不含市值中性对照；"
                    "需另跑市值中性化增量检验）",
    })

    # ⑤ T3 双窗同号 —— 本轮 3y 单窗 gate 不产出（恒 NOT）
    rows.append({
        "no": "⑤", "name": "T3双窗同号", "status": STATUS_NOT,
        "evidence": "NOT_PRODUCED（本轮 3y 单窗；需另跑 T3 双窗 3y+5y 同号对照）",
    })

    # 上下文脚注（不判生死，只交代口径）
    ctx = [f"grid_profile={grid}"]
    if cscv_ok is not None:
        ctx.append(f"cscv_reliable={'是' if cscv_ok else '否'}(n_variants={n_var})")
    return rows, ctx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="价值维度 gate 五条判据判决单（机械、fail-closed）")
    ap.add_argument("--gate-json", required=True, type=Path,
                   help="本轮 planb_value 的 walkforward_gate.json")
    ap.add_argument("--excess", type=Path, default=None,
                   help="excess 回流 JSON（runner gate_excess_report.py 产出）；缺则判据③ NOT")
    ap.add_argument("--baseline-gate-json", type=Path, default=None,
                   help="基线 WF-001 的 gate.json（算 ΔDSR）；缺则判据① Δ 部分 NOT")
    ap.add_argument("--baseline-pbo", type=float, default=BASELINE_PBO_DEFAULT,
                   help=f"基线 PBO 参考值（默认 {BASELINE_PBO_DEFAULT}，判据②不恶化对照）")
    ap.add_argument("--min-dsr", type=float, default=MIN_DSR_DEFAULT,
                   help=f"DSR 绝对门槛（默认 {MIN_DSR_DEFAULT}，同 walkforward_gate.MIN_DSR）")
    ap.add_argument("--max-pbo", type=float, default=MAX_PBO_DEFAULT,
                   help=f"PBO 绝对上限（默认 {MAX_PBO_DEFAULT}，同 walkforward_gate.MAX_PBO）")
    args = ap.parse_args(argv)

    gate = _load_json(args.gate_json)
    if not isinstance(gate, dict):
        print(f"❌ gate.json 读不出或缺失: {args.gate_json}", file=sys.stderr)
        return 2
    base = _load_json(args.baseline_gate_json) if args.baseline_gate_json else None
    if args.baseline_gate_json and not isinstance(base, dict):
        print(f"⚠️ baseline gate.json 读不出，判据① ΔDSR 将 NOT: {args.baseline_gate_json}",
              file=sys.stderr)
        base = None
    excess = _load_excess(args.excess)

    rows, ctx = judge(gate, base, excess, args.baseline_pbo, args.min_dsr, args.max_pbo)

    # 总体判定（fail-closed）
    statuses = [r["status"] for r in rows]
    if STATUS_FAIL in statuses:
        overall = "REJECT（否决，不回头调参）"
        overall_note = "任一条产出即 FAIL ⇒ 一票否决"
    elif STATUS_NOT in statuses:
        overall = "INCOMPLETE（fail-closed）"
        overall_note = "有判据本轮 3y 单窗不产出（④⑤等）⇒ 不得宣布「价值维度成立」，" \
                       "需补齐缺失判据再定生死"
    else:
        overall = "HOLD（五条全 PASS）"
        overall_note = "全产出且全过（现实里因 ④⑤ 本轮不产出，至多 INCOMPLETE）"

    print("# 价值维度 gate 五条判据判决单（机械判读 / fail-closed）")
    print("\n".join(f"  · {c}" for c in ctx))
    print()
    for r in rows:
        print(f"  {r['no']} {r['name']:<20} {r['status']:<12} {r['evidence']}")
    print()
    print(f"总体判定: {overall}")
    print(f"  {overall_note}")
    missing = [r["no"] + " " + r["name"] for r in rows if r["status"] == STATUS_NOT]
    if missing:
        print("未产出/需另跑的判据: " + "; ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
