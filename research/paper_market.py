#!/usr/bin/env python3
"""影子账户数据底座：全市场 universe + 每日收盘快照。

为什么需要它：
- 短线策略要「昨天」的涨跌幅/成交额/换手做参照，而 runner 的 astocks_raw.db 停在 11-13（不自动更新）。
- 腾讯批量报价一次能拿 100+ 只票的 现价/开/高/低/涨跌幅/成交额/换手/量比/涨跌停价/流通市值，
  全市场约 47 个请求、零 K 线 ⇒ 不碰 ifzq 的 WAF（只有 K 线端点会被拦）。

数据落点：research/snapshots/YYYY-MM-DD.json.gz（约 300KB/日，纯研究侧，不碰生产库）

用法：
  python3 paper_market.py --snapshot               # 收盘后落今日快照
  python3 paper_market.py --inspect 2026-11-19     # 看某日快照摘要
  python3 paper_market.py --fetch-quotes           # 只打一份实时报价（调试）
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP_DIR = HERE / "snapshots"
CST = timezone(timedelta(hours=8))
# 默认指向 runner 的库（体量小、更新及时）；本机跑用 AQSP_DB 覆盖。
DB = Path(os.getenv("AQSP_DB", "/opt/aqsp-runner/data/astocks_raw.db"))
QUOTE_URL = "http://qt.gtimg.cn/q="
CHUNK = 120


# ---------------- universe ----------------

def tencent_id(code: str) -> str:
    """6 位代码 → 腾讯前缀 id。"""
    if code.startswith(("60", "68", "9")):
        return "sh" + code
    if code.startswith(("00", "30", "20")):
        return "sz" + code
    return "bj" + code


def board_limit(code: str) -> float:
    """涨跌停幅度：创业/科创 20%，北交 30%，其余 10%。"""
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8")):
        return 0.30
    return 0.10


def is_main_or_growth(code: str) -> bool:
    """主板+创业板+科创（排北交 4/8、排 B 股 2/9）。"""
    return code.startswith(("60", "00", "30", "68"))


def load_universe(db_path: Path = DB) -> list[dict]:
    """从库里的 stocks 表取 universe，排 ST/退市/北交/B股。"""
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute("SELECT ts_code, name FROM stocks").fetchall()
    finally:
        con.close()
    out = []
    for ts_code, name in rows:
        code = str(ts_code)[:6]
        nm = name or ""
        if not is_main_or_growth(code):
            continue
        if "ST" in nm.upper() or "退" in nm:
            continue
        out.append({"code": code, "name": nm, "limit": board_limit(code)})
    return out


# ---------------- quotes ----------------

def fetch_quotes(codes: list[str], chunk: int = CHUNK, timeout: int = 10) -> dict:
    """批量报价（腾讯 qt.gtimg.cn）。返回 {code: row}，row 见 parse 注释。"""
    out: dict = {}
    for i in range(0, len(codes), chunk):
        batch = codes[i:i + chunk]
        url = QUOTE_URL + ",".join(tencent_id(c) for c in batch)
        try:
            raw = urllib.request.urlopen(url, timeout=timeout).read().decode("gbk", "ignore")
        except OSError:
            continue
        out.update(parse_quote_text(raw))
    return out


def _f(parts: list[str], i: int, default: float = 0.0) -> float:
    try:
        v = parts[i]
        return float(v) if v not in ("", "-") else default
    except (IndexError, ValueError):
        return default


def parse_quote_text(raw: str) -> dict:
    """解析腾讯报价文本。

    字段索引（实测 88 段）：1 名称 3 现价 4 昨收 5 今开 30 时间戳 32 涨跌幅%
    33 最高 34 最低 36 成交量(手) 37 成交额(万) 38 换手% 43 振幅% 44 流通市值(亿)
    45 总市值(亿) 47 涨停价 48 跌停价 49 量比 9 买一价 10 买一量 19 卖一价 20 卖一量
    """
    out: dict = {}
    for m in re.finditer(r'v_([a-z]{2})(\d{6})="([^"]+)"', raw):
        parts = m.group(3).split("~")
        if len(parts) < 50:
            continue
        code = m.group(2)
        price = _f(parts, 3)
        if price <= 0:
            continue
        out[code] = {
            "code": code,
            "name": parts[1],
            "price": price,
            "prev_close": _f(parts, 4),
            "open": _f(parts, 5),
            "high": _f(parts, 33),
            "low": _f(parts, 34),
            "vol": _f(parts, 36),          # 手
            "amount": _f(parts, 37),       # 万元
            "turnover": _f(parts, 38),     # %
            "amplitude": _f(parts, 43),    # %
            "float_mv": _f(parts, 44),     # 亿
            "limit_up": _f(parts, 47),
            "limit_down": _f(parts, 48),
            "vol_ratio": _f(parts, 49),
            "pct": _f(parts, 32),
            "bid1_px": _f(parts, 9),
            "bid1_vol": _f(parts, 10),
            "ask1_px": _f(parts, 19),
            "ask1_vol": _f(parts, 20),
            "ts": parts[30],
        }
    return out


# ---------------- snapshot ----------------

def _compact(q: dict) -> dict:
    """压缩字段名（快照 5500 只 × 18 字段，gz 后约 300KB）。"""
    return {"n": q["name"], "p": q["price"], "o": q["open"], "h": q["high"], "l": q["low"],
            "pc": q["prev_close"], "pct": q["pct"], "amt": q["amount"], "to": q["turnover"],
            "amp": q["amplitude"], "fmv": q["float_mv"], "lu": q["limit_up"],
            "ld": q["limit_down"], "vr": q["vol_ratio"], "v": q["vol"]}


def save_snapshot(rows: dict, date: str) -> Path:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAP_DIR / ("%s.json.gz" % date)
    payload = {"date": date, "count": len(rows),
               "built_at": datetime.now(CST).isoformat(),
               "rows": {c: _compact(q) for c, q in rows.items()}}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return path


def load_snapshot(date: str) -> dict | None:
    path = SNAP_DIR / ("%s.json.gz" % date)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def snapshot_dates() -> list[str]:
    if not SNAP_DIR.exists():
        return []
    # 注意：Path.stem 只会去掉最后一个后缀（2026-11-19.json.gz → 2026-11-19.json），必须用 name
    return sorted(p.name.replace(".json.gz", "") for p in SNAP_DIR.glob("*.json.gz"))


def latest_before(date: str) -> tuple[str, dict] | tuple[None, None]:
    """严格早于 date 的最近快照（防前视）。"""
    cands = [d for d in snapshot_dates() if d < date]
    if not cands:
        return None, None
    d = cands[-1]
    snap = load_snapshot(d)
    return (d, snap["rows"]) if snap else (None, None)


# ---------------- 基准（这一步不做，账本就会把 beta 当 alpha 骗自己）----------------

_BENCH_CACHE: dict[str, float | None] = {}


def daily_bench(date: str) -> float | None:
    """某交易日全市场等权收益（小数）。

    等权组合的日收益 = 成分股当日涨跌幅的**均值**（不是中位数！中位数不能累乘，
    这是项目踩过的坑）。所以直接取快照 pct 均值即可。
    """
    if date in _BENCH_CACHE:
        return _BENCH_CACHE[date]
    snap = load_snapshot(date)
    if not snap or not snap.get("rows"):
        return None
    vals = [float(r.get("pct") or 0.0) for r in snap["rows"].values()]
    if not vals:
        return None
    r = sum(vals) / len(vals) / 100.0
    _BENCH_CACHE[date] = r
    return r


def bench_between(d1: str, d2: str) -> float | None:
    """d1 收盘 → d2 收盘 的全市场等权收益（小数）。缺任一日快照就返回 None，不猜。"""
    if not d1 or not d2:
        return None
    if d1 == d2:
        return 0.0
    dates = [x for x in snapshot_dates() if d1 < x <= d2]
    if not dates:
        return None
    acc = 1.0
    for d in dates:
        r = daily_bench(d)
        if r is None:
            return None
        acc *= (1.0 + r)
    return acc - 1.0


def bench_series(dates: list[str], base: float = 10_000.0) -> list[float]:
    """把一串交易日映射成基准净值（起点 base），用于和策略净值画在同一条轴上。"""
    out = []
    acc = 1.0
    for d in dates:
        r = daily_bench(d)
        if r is not None:
            acc *= (1.0 + r)
        out.append(round(base * acc, 2))
    return out


def snapshot_gaps() -> list[tuple[str, str]]:
    """快照日期缺口（相邻两天自然日差 > 4 就标出来，长假另说）。

    用途：如果某天收盘快照没落成，次日「昨日」就会取到更早的日子而不报错 ——
    那会让 P3/P4 拿着错误基准做判断。这里把缺口显式暴露出来。
    """
    ds = snapshot_dates()
    gaps = []
    for a, b in zip(ds, ds[1:]):
        delta = (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days
        if delta > 4:
            gaps.append((a, b))
    return gaps


def quotes_fresh(quotes: dict, date: str) -> bool:
    """报价时间戳是否属于 date（YYYY-MM-DD）。

    非交易日（节假日 cron 照跑）与「早盘前取到昨日收盘」都会让时间戳停在旧日期——
    这种情况必须拒收，否则会把昨天的行情当成今天记进账本（等于偷看未来）。
    """
    tag = date.replace("-", "")
    if not quotes:
        return False
    fresh = sum(1 for q in quotes.values() if str(q.get("ts", ""))[:8] == tag)
    return fresh >= max(3, int(len(quotes) * 0.3))


def take_snapshot(db_path: Path = DB) -> tuple[str, int]:
    uni = load_universe(db_path)
    if not uni:
        raise RuntimeError("universe 为空：库不存在或 stocks 表没数据")
    quotes = fetch_quotes([u["code"] for u in uni])
    if len(quotes) < 1000:
        raise RuntimeError("报价只拿到 %d 只，疑似接口异常，不落快照" % len(quotes))
    today = datetime.now(CST).strftime("%Y-%m-%d")
    if not quotes_fresh(quotes, today):
        raise RuntimeError("报价时间戳非今日（非交易日或行情未更新），不落快照")
    path = save_snapshot(quotes, today)
    return str(path), len(quotes)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--inspect", default=None)
    ap.add_argument("--fetch-quotes", action="store_true")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args(argv)

    if args.snapshot:
        path, n = take_snapshot(Path(args.db))
        print("[market] 快照已落 %s（%d 只）" % (path, n))
        return 0
    if args.inspect:
        snap = load_snapshot(args.inspect)
        if not snap:
            print("无此快照:", args.inspect)
            return 1
        rows = snap["rows"]
        up = sum(1 for r in rows.values() if r["pct"] > 0)
        down = sum(1 for r in rows.values() if r["pct"] < 0)
        print("%s  %d 只  涨%d/跌%d  均涨幅%.2f%%" % (
            args.inspect, len(rows), up, down,
            sum(r["pct"] for r in rows.values()) / max(len(rows), 1)))
        return 0
    if args.fetch_quotes:
        uni = load_universe(Path(args.db))
        q = fetch_quotes([u["code"] for u in uni][:10])
        print(json.dumps(q, ensure_ascii=False, indent=2)[:1200])
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
