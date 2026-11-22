#!/usr/bin/env python3
"""影子账户按「时点」跑多策略扫描 —— 一个入口，五种时点。

时点（北京时间）：
  09:27  auction  竞价快照 → P4 竞价抢筹（全市场报价）
  10:00  morning  早盘确认 → P3 超跌反抽（只需昨日暴跌候选，报价少）
  14:45  late     尾盘强势 → P5 尾盘惯性（全市场报价）
  15:05  close    落当日快照 → 全部 sleeve 结算 → 记净值（唯一会产生交易记录的时刻）
  盘中   board    打板信号 → P1 二板接力 / P2 首板接力 / P6 二板·跳空top2（复用 shortline_signal 的检测结果）

用法：
  python3 paper_scan.py --phase close
  python3 paper_scan.py --phase board --detection-json _signal_20261120.json
  python3 paper_scan.py --phase morning --dry       # 只算不记账（验证用）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paper_ledger as pl  # noqa: E402  (sys.path 注入同目录后才可导入)
import paper_market as pm  # noqa: E402  (sys.path 注入同目录后才可导入)
from paper_strategies import board_signals, by_phase  # noqa: E402  (sys.path 注入同目录后才可导入)

CST = timezone(timedelta(hours=8))
PHASES = ("auction", "morning", "late", "close", "board")


def _today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _universe_codes(yest_rows: dict | None) -> list[str]:
    if yest_rows:
        return list(yest_rows.keys())
    return [u["code"] for u in pm.load_universe()]


def _candidates_for(sid: str, yest_rows: dict, phase: str) -> list[str]:
    """按策略需要缩减候选池，少打请求。"""
    if sid == "P3":
        # 只要昨日暴跌且流动性够的票，报价就打这几百只
        out = []
        for code, r in yest_rows.items():
            limit = pm.board_limit(code)
            if float(r.get("pct") or 0) <= -limit * 100 * 0.7 and \
                    float(r.get("amt") or 0) >= 5000 and float(r.get("fmv") or 0) >= 15:
                out.append(code)
        return out
    return list(yest_rows.keys())


def run_phase(phase: str, date: str | None = None, detection: dict | None = None,
              dry: bool = False, allow_stale: bool = False) -> dict:
    today = date or _today()
    led = pl.load()
    result = {"phase": phase, "date": today, "sleeves": {}, "closed": [], "note": ""}

    if phase == "close":
        yest_date, _ = pm.latest_before(today)
        snap = pm.load_snapshot(today)
        if not snap:
            path, n = pm.take_snapshot()
            snap = pm.load_snapshot(today)
            result["note"] = "落快照 %d 只" % n
        if not snap:
            result["note"] = "快照落失败，跳过结算"
            return result
        rows = snap["rows"]
        result["closed"] = pl.settle(led, rows, today)
        pl.mark_nav(led, rows, today)
        result["prev_snapshot"] = yest_date
        if not dry:
            pl.save(led)
            # 落时点标记：报告页据此显示「今日 15:05 结算 已跑」（手动跑也要留痕）
            state = HERE / "state"
            state.mkdir(exist_ok=True)
            (state / ("phase_%s_close.done" % today)).write_text(
                '{"phase": "close", "at": "%s"}' % datetime.now(CST).isoformat(),
                encoding="utf-8")
        result["overview"] = pl.overview(led)
        return result

    if phase == "board":
        if not detection:
            result["note"] = "无检测结果，跳过"
            return result
        hits = detection.get("all_hits", [])
        for sid, spec in by_phase("board"):
            # 选股口径（全取 / 跳空 top_n）由注册表声明，这里不硬编码策略 id：
            # 新增一个打板 sleeve 只需在 STRATEGIES 里写 `board`/`top_n`，不用改本文件。
            got = board_signals(spec, hits)
            r = pl.ingest(led, sid, got["signals"], today, "board")
            sv = led["sleeves"][sid]
            sv["sealed_codes"] = sorted(set(sv.get("sealed_codes", [])) | set(got["sealed_codes"]))
            result["sleeves"][sid] = {"name": spec["name"],
                                      "sealed_n": len(got["sealed_codes"]), **r}
        if not dry:
            pl.save(led)
        return result

    yest_date, yest_rows = pm.latest_before(today)
    if not yest_rows:
        result["note"] = "无昨日快照（首日）⇒ 本时点跳过，明天起正常"
        return result
    result["yest"] = yest_date

    members = by_phase(phase)
    # 一次取并集，避免同一只票反复请求
    need: set[str] = set()
    for sid, _spec in members:
        need.update(_candidates_for(sid, yest_rows, phase))
    quotes = pm.fetch_quotes(sorted(need))
    result["quoted"] = len(quotes)
    if not quotes:
        result["note"] = "报价为空，跳过"
        return result
    if not pm.quotes_fresh(quotes, today) and not allow_stale:
        result["note"] = "报价时间戳非今日（非交易日/行情未更新），跳过"
        return result

    ctx = {"phase": phase, "today": today, "yest_date": yest_date,
           "yest": yest_rows, "quotes": quotes}
    for sid, spec in members:
        scan = spec["scan"]
        if scan is None:
            continue
        sigs = scan(ctx)
        r = pl.ingest(led, sid, sigs, today, phase)
        result["sleeves"][sid] = {"name": spec["name"], **r}
    if not dry:
        pl.save(led)
    result["overview"] = pl.overview(led)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", required=True, choices=PHASES)
    ap.add_argument("--date", default=None, help="覆盖日期（回放/测试用）")
    ap.add_argument("--detection-json", default=None, help="board 时点的检测结果 JSON")
    ap.add_argument("--dry", action="store_true", help="只算不记账")
    ap.add_argument("--debug-replay", action="store_true",
                    help="放开「报价必须是今日」的前视守卫，用于非交易时段验通路（强制 dry）")
    args = ap.parse_args(argv)

    dry = args.dry or args.debug_replay
    det = None
    if args.detection_json:
        det = json.loads(Path(args.detection_json).read_text(encoding="utf-8"))
    res = run_phase(args.phase, args.date, det, dry, allow_stale=args.debug_replay)
    # 只打印关键信息，避免 cron.log 膨胀
    brief = {"phase": res["phase"], "date": res["date"], "note": res.get("note", ""),
             "closed": len(res.get("closed", [])), "sleeves": {}}
    for sid, r in res.get("sleeves", {}).items():
        brief["sleeves"][sid] = {"sig": r.get("signals"), "buy": len(r.get("bought", [])),
                                 "skip": len(r.get("skipped", []))}
    print(json.dumps(brief, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
