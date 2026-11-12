#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填台账中缺失的 `excess_return_pct`（只填 NULL，**绝不覆盖已有值**）。

## 为什么需要
超额是 2026-06 才加进结算逻辑的。prod 台账实测：
- 有超额：23 条，exit_date `2026-06-09 ~ 2026-11-09`
- 无超额：195 条，exit_date `2026-04-03 ~ 2026-07-15`
⇒ 不是数据缺失，是**功能上线时间差**。不补则「超额」指标覆盖率只有 11%，
聚合均值代表的根本不是全部信号。

## 口径（与 `aqsp.ledger.base._benchmark_return` 完全一致）
- 基准 = `benchmark_symbol`（台账里是 `000300`，库内 `000300.SH`）
- 入场价 = 基准在 **首个 ≥ entry_date** 的 `open`
- 出场价 = 基准在 **最后 ≤ exit_date** 的 `close`
- `excess_return_pct = return_pct − (exit/entry − 1) × 100`

## 安全
- 默认 **dry-run**，只打印将改动多少条、以及前几条样例；
- `--apply` 才写，且**先备份**为 `<ledger>.bak-<时间戳>`，再原子替换；
- 只碰 `excess_return_pct`（以及顺带补 `benchmark_return_pct`）这一个派生字段，
  不动价格、状态、收益等原始记录。

用法：
    python scripts/data/backfill_ledger_excess.py --ledger data/predictions.jsonl \\
        --db /opt/market-data/astocks_raw.db            # 预览
    ... --apply                                          # 真正写入
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _bench(con: sqlite3.Connection, symbol: str, entry_date: str, exit_date: str):
    rows = con.execute(
        "SELECT trade_date, open, close FROM daily_qfq"
        " WHERE ts_code LIKE ? AND trade_date >= ? AND trade_date <= ?"
        " ORDER BY trade_date",
        (f"{symbol}%", entry_date, exit_date),
    ).fetchall()
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预览）")
    args = ap.parse_args(argv)

    ledger = Path(args.ledger)
    if not ledger.exists():
        print(f"[ERR] 台账不存在: {ledger}", file=sys.stderr)
        return 2

    lines = ledger.read_text(encoding="utf-8").splitlines()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    filled = 0
    skipped_no_bench = 0
    skipped_no_price = 0
    examples: list[str] = []
    out_lines: list[str] = []

    try:
        for line in lines:
            raw = line.strip()
            if not raw:
                out_lines.append(line)
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue

            if str(row.get("status")) != "validated" or row.get("excess_return_pct") is not None:
                out_lines.append(line)
                continue

            symbol = str(row.get("benchmark_symbol") or "").strip()
            entry_date = str(row.get("entry_date") or "").replace("-", "")
            exit_date = str(row.get("exit_date") or "").replace("-", "")
            ret = row.get("return_pct")
            if not symbol or not entry_date or not exit_date or ret is None:
                skipped_no_bench += 1
                out_lines.append(line)
                continue

            rows = _bench(con, symbol, entry_date, exit_date)
            if len(rows) < 2:
                skipped_no_price += 1
                out_lines.append(line)
                continue

            entry_px = float(rows[0][1])
            exit_px = float(rows[-1][2])
            if entry_px <= 0:
                skipped_no_price += 1
                out_lines.append(line)
                continue

            bench_ret = (exit_px - entry_px) / entry_px * 100
            excess = float(ret) - bench_ret
            row["benchmark_return_pct"] = round(bench_ret, 4)
            row["excess_return_pct"] = round(excess, 4)
            filled += 1
            if len(examples) < 5:
                examples.append(
                    f"  {row.get('id', '?')[:8]} {row.get('symbol', '')} "
                    f"{entry_date}~{exit_date} ret={float(ret):+.2f}% "
                    f"bench={bench_ret:+.2f}% excess={excess:+.2f}%"
                )
            out_lines.append(json.dumps(row, ensure_ascii=False))
    finally:
        con.close()

    print(f"待回填 {filled} 条｜跳过(无基准符号/日期) {skipped_no_bench}｜跳过(基准行情不足) {skipped_no_price}")
    if examples:
        print("样例：")
        print("\n".join(examples))
    if not filled:
        print("无需改动。")
        return 0
    if not args.apply:
        print("\n[dry-run] 未写入。确认无误后加 --apply。")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = ledger.with_suffix(ledger.suffix + f".bak-{stamp}")
    shutil.copy2(ledger, backup)
    tmp = ledger.with_suffix(ledger.suffix + ".tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(ledger)
    print(f"[ok] 已写入 {filled} 条；备份: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
