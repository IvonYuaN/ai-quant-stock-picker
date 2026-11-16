#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重结算台账中「物理不可能」脏行（return_pct 已写错，需重走修复后的出场语义）。

## 为什么
`backfill_ledger_excess.py` 只补 **NULL** 的超额，对 `return_pct` **已写错**的脏行
无能为力（它遇到已有 return_pct 的行直接跳过）。实测 19/218 行属「当日了结 +
|收益|>30%」，全是 stop_loss，exit/entry 呈 ×2、×1.3 这类复权基准不一致特征。

根因（commit `2ad9f975` 实证）：`_resolve_exit` 用行内**绝对价** stop_loss /
take_profit 出场。若它与本次结算的价格序列**标度不同**（复权基准变更 / 数据源
切换），止损价可能**高于市价** ⇒ `low <= stop_loss` 恒成立 ⇒ 当日立即"止损"出场，
算出物理不可能的收益（最大 +317%：301312 库内 122.70，行内出场 257.40）。

`2ad9f975` 已把出场语义修为：止损仅在 `0 < stop_loss < entry_price` 可信、
止盈仅在 `take_profit > entry_price` 可信，否则**忽略该触发条件**，按持有到期结算。
本脚本把该修复**回放到历史脏行**：重取库内行情重算 entry/exit，修正 `return_pct`
及派生字段（exit_price / exit_reason / benchmark_return_pct / excess_return_pct / win）。

## 脏行判据（与 backend/performance_bridge._excess_summary 逐位一致）
`status == "validated"` 且 非模拟 且 当日了结（entry_date == exit_date）
且 |return_pct| > 30。只碰这些行，不擅自扩大范围（AGENTS.md §8）。

## 口径单一事实源
出场/收益算法 = `aqsp.ledger.base._resolve_exit`（`2ad9f975` 语义校验版）+
`_benchmark_return`。本脚本为**自包含维护工具**（对齐 `backfill_ledger_excess.py`
范式：纯 SQL、零生产依赖，便于离线在 runner/prod 跑），语义校验 4 行内联与其**逐字
一致**；若 `base._resolve_exit` 的语义再变更，须同步本脚本 `_exit`（见其 docstring）。
对拍由 `tests/test_resettle_ledger_dirty.py` 承担（可导入 aqsp 时断言 max|diff|=0）。

## 安全
- 默认 **dry-run**，只打印将改多少条 + 前后对照样例；
- `--apply` 才写：先备份 `<ledger>.bak-<时间戳>`，再原子替换；
- **幂等**：重算后脏行 |return| 必 < 30（语义校验忽略脏止损 ⇒ 回到持有到期），
  第二次跑 0 条命中 ⇒ 不重复改写；
- 只覆盖 6 个派生字段，不动 status / symbol / signal_date / entry_date 等原始记录。

用法：
    # 预览（默认 dry-run）
    python scripts/data/resettle_ledger_dirty.py \\
        --ledger data/predictions.jsonl --db /opt/market-data/astocks_raw.db
    # 真正写入
    python scripts/data/resettle_ledger_dirty.py ... --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# A股单日涨跌幅物理上限（北交所 30%）⇒ 当日了结 + |收益|>30% 必为结算口径错误。
# 与 backend/performance_bridge._excess_summary 的剔除判据**逐位一致**（勿单方面改）。
IMPLAUSIBLE_SAME_DAY_RET = 30.0


def _norm_date(raw) -> str:
    """把台账/库里的日期归一成 YYYYMMDD 便于跨源比较（保留原值另存，写回复原格式）。"""
    return str(raw or "").strip().replace("-", "")


def _fmt_from_template(template: str) -> str:
    """由已有 entry_date 的格式决定写回的日期格式（8 位 / 10 位），避免破坏台账一致性。"""
    return "long" if len(_norm_date(template)) and len(str(template)) == 10 else "short"


def _to_date_str(stamp: str, long_form: bool) -> str:
    s = _norm_date(stamp)
    if long_form:
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s
    return s


def _fetch_bars(con: sqlite3.Connection, ts_code_prefix: str, since: str) -> list[tuple]:
    """取该票 since（YYYYMMDD）起的全部日线。ts_code 前缀匹配（000300 ⇒ 000300.SH）。"""
    rows = con.execute(
        "SELECT trade_date, open, high, low, close FROM daily_qfq"
        " WHERE ts_code LIKE ? AND trade_date >= ? ORDER BY trade_date",
        (f"{ts_code_prefix}%", since),
    ).fetchall()
    return rows


def _entry_bars(con: sqlite3.Connection, ts_code_prefix: str, signal_date: str) -> list[tuple]:
    """future[date > signal_date] 的全部 K 线（信号日之后的交易日，含信号日次日开仓）。"""
    return _fetch_bars(con, ts_code_prefix, signal_date)


def _next_day(stamp: str) -> str:
    """YYYYMMDD + 1 个自然日（用于把 signal_date 之后的 K 线截出来；非交易日由
    「取第一条」自然落到次日首个交易日，与 validate 的 future.iloc[0] 同语义）。"""
    import datetime as _dt

    d = _dt.date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
    return (d + _dt.timedelta(days=1)).strftime("%Y%m%d")


def _exit(
    window: list[tuple], row: dict, entry_price: float
) -> tuple[tuple, float, str]:
    """内联 `aqsp.ledger.base._resolve_exit`（2ad9f975 语义校验版），返回 (出场 bar, 价, 原因)。

    window = [(trade_date, open, high, low, close), ...]，即 eval_window（future 前 horizon 根）。
    语义校验与其逐字一致：止损仅在 0<stop_loss<entry_price 可信、止盈仅在
    take_profit>entry_price 可信，否则忽略触发按持有到期（宁可少一个触发，不要假成交）。
    若 base._resolve_exit 语义再变更，**必须同步本函数**（对拍测试会抓）。
    """
    stop_loss = float(row.get("stop_loss") or 0)
    take_profit = float(row.get("take_profit") or 0)
    if entry_price is not None and entry_price > 0:
        if not (0 < stop_loss < entry_price):
            stop_loss = 0.0
        if take_profit <= entry_price:
            take_profit = 0.0
    slippage = float(row.get("slippage_bps") or 0) / 10000
    for bar in window:
        trade_date, open_p, high_p, low_p, close_p = bar
        bar_close = float(close_p)
        low = float(low_p) if low_p is not None else bar_close
        high = float(high_p) if high_p is not None else bar_close
        if stop_loss > 0 and low <= stop_loss:
            return bar, stop_loss * (1 - slippage), "stop_loss"
        if take_profit > 0 and high >= take_profit:
            return bar, take_profit * (1 - slippage), "take_profit"
    last = window[-1]
    return last, float(last[4]) * (1 - slippage), "horizon_close"


def _benchmark_return(
    con: sqlite3.Connection,
    bench_prefix: str,
    entry_bar: tuple,
    exit_bar: tuple,
) -> float | None:
    """内联 `aqsp.ledger.base._benchmark_return`：基准在首个 ≥ entry_date 的 open →
    最后 ≤ exit_date 的 close。缺基准 / 缺价 / entry<=0 一律 None（不回填 0）。"""
    if not bench_prefix:
        return None
    entry_stamp = _norm_date(entry_bar[0])
    exit_stamp = _norm_date(exit_bar[0])
    rows = con.execute(
        "SELECT trade_date, open, close FROM daily_qfq"
        " WHERE ts_code LIKE ? AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
        (f"{bench_prefix}%", entry_stamp, exit_stamp),
    ).fetchall()
    if not rows:
        return None
    entry = float(rows[0][1])
    exit_price = float(rows[-1][2])
    if entry <= 0:
        return None
    return (exit_price - entry) / entry * 100


def is_dirty_row(row: dict) -> bool:
    """脏行判据（与 performance_bridge._excess_summary 一致）。"""
    if str(row.get("status") or "") != "validated":
        return False
    sim = row.get("is_simulated", False)
    if isinstance(sim, str):
        sim = sim.strip().lower() in {"true", "1", "yes", "y"}
    if bool(sim):
        return False
    ret = row.get("return_pct")
    if ret is None:
        return False
    same_day = _norm_date(row.get("entry_date")) == _norm_date(row.get("exit_date"))
    return same_day and abs(float(ret)) > IMPLAUSIBLE_SAME_DAY_RET


def resettle_row(con: sqlite3.Connection, row: dict) -> dict | None:
    """对单条脏行重走出场语义，返回修正后的行副本；无法重算（行情缺失/窗口不足）返回 None。"""
    symbol = str(row.get("symbol") or "").strip()
    bench = str(row.get("benchmark_symbol") or "").strip()
    signal_date = _norm_date(row.get("signal_date") or row.get("pick_date"))
    if not symbol or not signal_date:
        return None
    future = _entry_bars(con, symbol, signal_date)
    future = [b for b in future if _norm_date(b[0]) >= _next_day(signal_date)]
    if not future:
        return None
    entry_bar = future[0]
    horizon = int(row.get("horizon_days") or 1)
    window = future[:horizon]
    if len(window) < max(1, horizon):
        return None
    entry_price = float(entry_bar[1]) * (1 + float(row.get("slippage_bps") or 0) / 10000)
    if entry_price <= 0:
        return None
    fee_pct = float(row.get("fee_bps") or 0) / 100
    exit_bar, exit_price, exit_reason = _exit(window, row, entry_price)
    ret = (exit_price - entry_price) / entry_price * 100 - fee_pct
    bench_ret = _benchmark_return(con, bench, entry_bar, exit_bar)
    excess = ret - bench_ret if bench_ret is not None else None
    long_form = _fmt_from_template(row.get("entry_date"))
    out = dict(row)
    out["entry_price"] = round(entry_price, 4)
    out["entry_date"] = _to_date_str(entry_bar[0], long_form)
    out["exit_date"] = _to_date_str(exit_bar[0], long_form)
    out["exit_price"] = round(exit_price, 4)
    out["exit_reason"] = exit_reason
    out["return_pct"] = round(ret, 4)
    out["benchmark_return_pct"] = round(bench_ret, 4) if bench_ret is not None else None
    out["excess_return_pct"] = round(excess, 4) if excess is not None else None
    out["win"] = ret > 0
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run 只预览）")
    args = ap.parse_args(argv)

    ledger = Path(args.ledger)
    if not ledger.exists():
        print(f"[ERR] 台账不存在: {ledger}", file=sys.stderr)
        return 2

    lines = ledger.read_text(encoding="utf-8").splitlines()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    out_lines: list[str] = []
    fixed = 0
    skipped: list[str] = []
    examples: list[str] = []
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
            if not is_dirty_row(row):
                out_lines.append(line)
                continue
            new = resettle_row(con, row)
            if new is None:
                skipped.append(f"  {str(row.get('id', '?'))[:8]} {row.get('symbol', '')} "
                               f"（行情缺失/窗口不足，保留原值）")
                out_lines.append(line)
                continue
            if len(examples) < 5:
                examples.append(
                    f"  {str(row.get('id', '?'))[:8]} {row.get('symbol', '')} "
                    f"{row.get('exit_reason', '')} {row.get('return_pct')} ⇒ "
                    f"{new['exit_reason']} {new['return_pct']}"
                )
            out_lines.append(json.dumps(new, ensure_ascii=False))
            fixed += 1
    finally:
        con.close()

    print(f"[dry-run] 待重结算 {fixed} 条｜无法重算（保留原值）{len(skipped)} 条")
    if examples:
        print("前后对照（exit_reason return_pct ⇒ exit_reason return_pct）：")
        print("\n".join(examples))
    if skipped:
        print("跳过（保留原值）：")
        print("\n".join(skipped))
    if not fixed:
        print("无需改动。")
        return 0
    if not args.apply:
        print("\n[dry-run] 未写入。确认无误后加 --apply。")
        return 0

    # 备份文件名用 time.strftime（本脚本是维护工具，非 aqsp runtime；不参与业务时钟判断）。
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = ledger.with_suffix(ledger.suffix + f".bak-{stamp}")
    shutil.copy2(ledger, backup)
    tmp = ledger.with_suffix(ledger.suffix + ".tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(ledger)
    print(f"[ok] 已重结算 {fixed} 条；备份: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
