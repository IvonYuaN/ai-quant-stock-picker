#!/usr/bin/env python3
"""alpha_deficit_report.py — 双窗 73/73 vs 单窗 146 截面 alpha 赤字诊断（只读分析工具）。

把「因子是否真有显著 IC」这件事从口头结论固化成可重复跑的工件：
  - 单窗 146 截面（factor_ic_latest.json）：因子的真实统计显著性（|t|>=2 即显著）；
  - 双窗 73/73（dual_window_latest.json）：方案 B 监控口径下的采纳判据（两窗同号 + 双 |t|>=2）。

两张表拼一起，立刻能看出「货架在 146 层有没有货、被 73/73 拆半稀释成什么样」。
这是 ② alpha 方法论决策的事实底座，不改变任何打分/排序/下单逻辑（proposal-only）。

用法：
  python scripts/analysis/alpha_deficit_report.py --dual <dual_window_latest.json> \
                                                 --single <factor_ic_latest.json>
也可只给一个（--dual 或 --single），缺的那侧标 N/A。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: str | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _factors(d: dict) -> dict:
    fac = d.get("factors")
    return fac if isinstance(fac, dict) else {}


def _num(v: dict, *keys):
    for k in keys:
        if isinstance(v, dict) and v.get(k) is not None:
            try:
                return float(v[k])
            except (TypeError, ValueError):
                return None
    return None


def _fmt(v) -> str:
    return "  N/A" if v is None else f"{v:6.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dual", help="dual_window_latest.json（双窗 73/73）")
    ap.add_argument("--single", help="factor_ic_latest.json（单窗 146 截面）")
    a = ap.parse_args()

    single = _load(a.single)
    dual = _load(a.dual)
    s_fac = _factors(single)
    d_fac = _factors(dual)
    names = sorted(set(s_fac) | set(d_fac))

    print(f"{'factor':12} {'s146_t':>8} {'|t|>=2':>7} {'d_tA':>7} {'d_tB':>7} {'dual_hit':>9}")
    print("-" * 52)
    n_sig146 = 0
    n_dual = 0
    for k in names:
        sv = s_fac.get(k, {})
        dv = d_fac.get(k, {})
        st = _num(sv, "t", "t_stat", "tvalue")
        dtA = _num(dv, "t_a")
        dtB = _num(dv, "t_b")
        sig146 = st is not None and abs(st) >= 2.0
        dhit = bool(dv.get("hit"))
        if sig146:
            n_sig146 += 1
        if dhit:
            n_dual += 1
        print(
            f"{k:12} {_fmt(st):>8} {str(sig146):>7} "
            f"{_fmt(dtA):>7} {_fmt(dtB):>7} {str(dhit):>9}"
        )

    print("-" * 52)
    print(f"单窗146截面显著因子 : {n_sig146}/{len(names)}")
    print(f"双窗73/73通过因子  : {n_dual}/{len(names)}")
    if single:
        print(
            f"单窗 as_of={single.get('as_of')} "
            f"n_sections={single.get('n_sections')}"
        )
    if dual:
        print(
            f"双窗 as_of_b={dual.get('as_of_b')} "
            f"gen={dual.get('generated_at')}"
        )
    if n_sig146 > 0 and n_dual == 0:
        print(
            "\n诊断: 146 层有显著因子但 73/73 双窗 0 通过 —— "
            "差异来自截面拆半稀释(t 统计量下降)，非因子无效。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
