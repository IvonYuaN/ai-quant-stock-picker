#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate 各臂的**超额收益**判读（策略 vs 全市场等权买入持有）。

实现全部在 `aqsp.backtest.excess`（唯一事实源），本脚本只负责取参 + 打印。

## 为什么必须有这个
`gate` 只报绝对收益。同一份产物曾算出 `超额 +55pp` 与 `超额 −6.9pp` 两个**相反**
结论，差别只在基准算法（截面中位日收益累乘 vs 等权买入持有）。不叠这一层，
"跑输等权 7pp/年"会被误读成"只是小亏"。

## 用法
    python scripts/research/gate_excess_report.py \
        --arms-dir /opt/aqsp/gate_run --db "A股量化分析数据/astocks_raw.db" --sample 1200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aqsp.backtest.excess import (  # noqa: E402
    equal_weight_curve,
    load_arm_periods,
    render_markdown,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms-dir", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--sample", type=int, default=1200)
    args = ap.parse_args(argv)

    arms = load_arm_periods(args.arms_dir)
    if not arms:
        print("❌ 未找到逐期记录（walkforward_gate_resume_*.jsonl.<arm>）")
        return 2

    curve = equal_weight_curve(args.db, sample=args.sample)
    if curve.empty:
        print("❌ 行情库无可用数据，无法构建等权基准")
        return 2

    print(f"[bench] 全市场等权买入持有（{args.sample} 票）｜{curve.index[0]} ~ {curve.index[-1]}")
    print(render_markdown(arms, curve))
    return 0


if __name__ == "__main__":
    sys.exit(main())
