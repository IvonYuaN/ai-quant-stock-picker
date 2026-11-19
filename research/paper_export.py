#!/usr/bin/env python3
"""把影子账户账本导成前端可直接渲染的紧凑 JSON（runner 侧每天推给 prod）。

为什么要单独导一份：
- 账本 `paper_arena.json` 是研究侧内部结构（含 ingested 键、快照路径等），不该整份给前端；
- 前端只需要「5 行结论 + 净值两条线 + 当前持仓」，导一份 ~30KB 的 payload 就够，
  免得前端为了口径再算一遍 —— 口径必须只有一处（这里）。

用法：
  python3 paper_export.py                    # 写 paper_arena_api.json（本目录）
  python3 paper_export.py --out /path/x.json  # 推到 prod 的 data/paper_arena.json
  python3 paper_export.py --print            # 打到 stdout 看一眼

为什么 runner 侧叫 `paper_arena_api.json`、prod 侧叫 `paper_arena.json`：
runner 的 shortline/ 目录里 `paper_arena.json` 是**账本本体**，同目录同名会互相覆盖；
prod 侧 `data/paper_arena.json` 是桥接（`backend/paper_arena_bridge.py`）读的产物。
两边文件名不同是有意的，不是笔误。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paper_ledger as pl
import paper_market as pm

CST = timezone(timedelta(hours=8))
OUT = HERE / "paper_arena_api.json"
SCHEMA = "v1"


def build_payload() -> dict:
    led = pl.load()
    ov = pl.overview(led)
    sleeves = led["sleeves"]

    rows = []
    for sid in sorted(sleeves):
        s, st = sleeves[sid], ov["sleeves"][sid]
        rows.append({
            "id": sid, "name": s["name"], "hypothesis": s["hypothesis"],
            "rule": s["rules"],
            "n": st["n"], "days": st["days"],
            "exc_mean": st["exc_mean"], "exc_win": st["exc_win"],
            "t_exc": st["t_exc"], "q_exc": st.get("q_exc"),
            "n_need_days": st["n_need_exc"],
            "funded_nav": st["funded_nav"], "funded_ret": st["funded_ret"],
            "funded_n": st["funded_n"], "funded_win": st["funded_win"],
            "positions": st["positions"], "sealed_n": st.get("sealed_n", 0),
            "unfunded": st.get("unfunded") or {},
        })

    dates = sorted({r["date"] for s in sleeves.values() for r in s["nav"]})
    port, bench = [], []
    if dates:
        per_date = {}
        for s in sleeves.values():
            for r in s["nav"]:
                per_date.setdefault(r["date"], {})[s["id"]] = r["ret_pct"]
        last = {sid: 0.0 for sid in sleeves}
        for d in dates:
            for sid in sleeves:
                last[sid] = per_date[d].get(sid, last[sid])
            port.append(round(sum(last.values()) / max(len(sleeves), 1), 2))
        bs = pm.bench_series(dates, base=100.0)
        bench = [round(v - 100.0, 2) for v in bs]

    positions = []
    for sid, s in sleeves.items():
        for p in s["positions"]:
            if not p["funded"]:
                continue
            positions.append({"sleeve": sid, "code": p["code"], "name": p["name"],
                              "buy_date": p["buy_date"], "buy_price": p["buy_price"],
                              "shares": p["shares"], "note": p.get("note", "")})

    trades = []
    for sid, s in sleeves.items():
        for t in s["trades"][-8:][::-1]:
            trades.append({"sleeve": sid, "code": t["code"], "name": t["name"],
                           "sell_date": t["sell_date"], "ret_pct": t["ret_pct"],
                           "excess_pct": t.get("excess_pct"), "reason": t["reason"],
                           "funded": t["funded"]})

    return {
        "schema_version": SCHEMA,
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "as_of": dates[-1] if dates else None,
        "initial_capital": pl.INITIAL_CAPITAL,
        "total_capital": ov["total_capital"], "total_nav": ov["total_nav"],
        "total_ret_pct": ov["total_ret_pct"],
        "trading_days": max((len(s["nav"]) for s in sleeves.values()), default=0),
        "cost_round_trip_pct": pl.COST_ONE_WAY * 200,
        "snapshot_days": len(pm.snapshot_dates()),
        "sleeves": rows, "nav": {"dates": dates, "port": port, "bench": bench},
        "positions": positions, "recent_trades": trades,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--out", default="", help="输出路径；留空写本目录 paper_arena_api.json")
    args = ap.parse_args(argv)
    payload = build_payload()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.print:
        print(text[:2000])
        return 0
    out = Path(args.out).expanduser() if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("[export] %s (%d sleeves, %d 字节)" % (out, len(payload["sleeves"]), len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
