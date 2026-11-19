#!/usr/bin/env python3
"""每日选股筛选清单生成器（筛选器产品化，11-17 路线①）。

定位：把 AQSP 已判定的「可行筛选器」（双反向 2f 生产口径）落成**可复跑的
每日选股清单 + 理由 + 风险标注**，作为筛选工具的可交付物。

PIT 纪律：``--as-of`` 严格切片（每只票只取 ``date <= as_of``），**绝不取未来**。
不追 alpha、不塞下单逻辑：输出只含「筛选 + 解释 + 风险标注」。

用法（本机小 universe 轻量验证）::

    PYTHONPATH=src python scripts/research/daily_screener.py \
      --db "A股量化分析数据/astocks_raw.db" --as-of 20261106 \
      --max-symbols 300 --top-n 20 \
      --out-json outputs/screener/snap.json --out-md outputs/screener/snap.md

universe 三种取法（优先级从高到低）：
1. ``--symbols-file`` 预筛好的 ts_code 列表（一行一个）；
2. ``--max-symbols N`` 取流动性前 N 只（``get_liquid_symbols``，轻量）；
3. 缺省：全市场 available（**重活，建议放 runner 跑**）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.strategies.screener import build_screener_snapshot, render_markdown
from aqsp.strategies.thresholds import load_thresholds


def _parse_asof(raw: str) -> date:
    raw = raw.strip().replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        raise SystemExit(f"--as-of 需 8 位 YYYYMMDD（或 YYYY-MM-DD）: {raw}")
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))


def _resolve_universe(
    source: SqliteDbSource,
    symbols_file: str | None,
    max_symbols: int | None,
    benchmark: str | None,
) -> list[str]:
    if symbols_file:
        codes = [
            ln.strip()
            for ln in Path(symbols_file).read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        # 去 benchmark（打分不选基准）+ 去重
        out: list[str] = []
        seen: set[str] = set()
        for c in codes:
            c = c.split(".")[0].zfill(6)
            if c == (benchmark or "").split(".")[0] or c in seen:
                continue
            seen.add(c)
            out.append(c)
        return out
    # 缺省全市场（重活）⇒ 建议放 runner
    if max_symbols and max_symbols > 0:
        return source.get_liquid_symbols(limit=max_symbols, min_amount=0.0)
    return [s for s in source.get_available_symbols()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="每日选股筛选清单生成器（PIT、只读、确定性）")
    ap.add_argument("--db", required=True, help="astocks_raw.db 路径")
    ap.add_argument("--as-of", required=True, help="快照日 YYYYMMDD / YYYY-MM-DD")
    ap.add_argument("--top-n", type=int, default=20, help="清单长度上限")
    ap.add_argument("--max-symbols", type=int, default=None, help="universe 取流动性前 N")
    ap.add_argument("--symbols-file", default=None, help="预筛 universe 文件（一行一个 code）")
    ap.add_argument("--regime", default="unknown", help="市场状态（默认 unknown=中性权重）")
    ap.add_argument("--lookback-days", type=int, default=120, help="PIT 切片回看窗（交易日）")
    ap.add_argument("--benchmark", default="000300", help="基准（从打分剔除）")
    ap.add_argument("--out-json", default=None, help="JSON 输出路径")
    ap.add_argument("--out-md", default=None, help="Markdown 输出路径")
    args = ap.parse_args(argv)

    as_of = _parse_asof(args.as_of)
    # PIT 切片：end=as_of（绝不取未来），start 给足够回看（120 交易日 ≈ 6 个月自然日）
    start = as_of - timedelta(days=max(90, args.lookback_days * 2))

    source = SqliteDbSource(db_path=args.db)
    universe = _resolve_universe(
        source, args.symbols_file, args.max_symbols, args.benchmark
    )
    if not universe:
        print("[warn] universe 为空，无清单可生成", file=sys.stderr)
        return 0

    # PIT 切片加载（fetch_daily 严格 end=as_of；缺覆盖标的自动跳过）
    frames = source.fetch_daily(universe, start=start, end=as_of, adjust="")
    data = {s: df for s, df in frames.items() if df is not None and not df.empty}
    th = load_thresholds()

    snapshot = build_screener_snapshot(
        data, regime=args.regime, thresholds=th, top_n=args.top_n, as_of=args.as_of
    )

    out_json = args.out_json
    out_md = args.out_md
    if out_json:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if out_md:
        Path(out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(out_md).write_text(render_markdown(snapshot), encoding="utf-8")

    # stdout 摘要（供 cron/自动化抓取）
    print(
        f"[screener] as_of={args.as_of} regime={args.regime} "
        f"universe={snapshot['universe_count']} selected={snapshot['selected_count']} "
        f"top={snapshot['selected'][0]['symbol'] if snapshot['selected'] else '-'} "
        f"hash={snapshot['snapshot_hash'][:16]}"
    )
    if out_json:
        print(f"[written] {out_json}")
    if out_md:
        print(f"[written] {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
