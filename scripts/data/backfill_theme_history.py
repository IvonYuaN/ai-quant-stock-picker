"""theme_history 冷启动回补（#406）—— push2his 板块日 K → change_pct 列。

一次性任务（**不进** preload_event_data.sh 的每日 SOURCES）：拉东财 bkzj
板块全集 + 逐板块 push2his 日 K，自算逐日涨跌幅，写入 theme_history.csv
（模块层 :func:`aqsp.data.theme_history.append_backfill_rows` 只补空洞、
三截面字段写 NaN）。

口径（2026-10-17 对 22 板块逐一验证）：
- K 线行 8 字段 = ``date,open,close,high,low,volume,amount,amplitude``
  （末列是**振幅**不是涨跌幅，涨跌幅必须自算）；
- 自算涨跌幅与 bkzj 当日快照 ``f3`` 口径一致（f3 = 涨跌幅 × 100）；
- 前复权（fqt=1）日涨跌幅序列与不复权一致（复权因子为乘性缩放，
  相邻日比值不变）。

频控与出口：
- push2his 需 **3s+ 间隔**（铁律，连发触发限流）；
- 东财画像随出口不同 ⇒ **在 runner 上代跑**（ssh 执行 + rsync 回流），
  勿在 Mac 直连后把结论搬去 prod。

退出码：0 = 成功；1 = 硬失败；2 = 数据已落盘但带 schema 漂移告警
（板块全集异常收缩 / 大面积空 K 线——有效数据仍回流）。
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from aqsp.data.theme_history import append_backfill_rows

# --- 网络依赖：延迟导入，dry-run 与测试不需要 requests ---------------------

BKZJ_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
BKZJ_PARAMS = {"key": "f3,f62,f184,f104,f105", "code": "m:90+t:3"}
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
KLINE_PARAMS = {
    "fields1": "f1,f2,f3,f4,f5,f6",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
    "klt": "101",
    "fqt": "1",
}
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/bkzj/gn.html",
}

DEFAULT_BEG = "20260102"  # 2026 年首个交易日
DEFAULT_END = "20500101"  # 到「最近」（东财按 end 截断）
DEFAULT_INTERVAL = 3.0  # push2his 请求间隔秒（铁律下限）
DEFAULT_TIMEOUT = 30

#: 板块全集低于此数 ⇒ 视为 bkzj 异常收缩（漂移告警，数据仍落盘）
MIN_UNIVERSE = 100
#: 空 K 线板块占比高于此值 ⇒ 漂移告警（抓取大面失败，数据仍落盘）
MAX_EMPTY_RATIO = 0.2

KLINE_FIELDS = 8  # date,open,close,high,low,volume,amount,amplitude


def _session():
    """禁环境代理的 Session（与 fetch_kaipanla_theme 同款：trust_env=False）。

    东财画像随出口不同；runner 直连无代理，Mac 上禁代理避免画像漂移
    被误当成端点行为。
    """
    import requests

    session = requests.Session()
    session.trust_env = False
    return session


def fetch_board_universe(session, *, timeout: int = DEFAULT_TIMEOUT) -> dict[str, str]:
    """拉板块全集：``{board_code: board_name}``（bkzj 单请求返回全部概念板块）。"""
    resp = session.get(BKZJ_URL, params=BKZJ_PARAMS, headers=_EM_HEADERS, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    diff = (payload.get("data") or {}).get("diff") or []
    universe: dict[str, str] = {}
    for raw in diff:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("f12") or "").strip()
        name = str(raw.get("f14") or "").strip()
        if code and name:
            universe[code] = name
    return universe


def fetch_kline(
    session,
    board_code: str,
    *,
    beg: str,
    end: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[tuple[str, float]]:
    """拉单板块日 K：``[(trade_date, close), ...]``（按日期升序）。

    K 线行 ``date,open,close,...``；畸形行（字段数不足 / 收盘不可解析）跳过。
    """
    params = dict(KLINE_PARAMS)
    params["secid"] = f"90.{board_code}"
    params["beg"] = beg
    params["end"] = end
    resp = session.get(KLINE_URL, params=params, headers=_EM_HEADERS, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    klines = ((payload.get("data") or {}).get("klines")) or []
    out: list[tuple[str, float]] = []
    for row in klines:
        parts = str(row).split(",")
        if len(parts) < KLINE_FIELDS:
            continue
        date = parts[0].strip()
        try:
            close = float(parts[2])
        except (TypeError, ValueError):
            continue
        if len(date) == 8 and date.isdigit():
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        out.append((date, close))
    return out


def build_backfill_rows(
    universe: dict[str, str],
    klines_by_code: dict[str, list[tuple[str, float]]],
) -> list[tuple[str, str, float]]:
    """K 线收盘序列 → ``(trade_date, board_name, change_pct)`` 行集。

    每个板块的**首日**（无前收）跳过；``change_pct`` 为百分数（与
    theme_history 当日快照口径一致）。
    """
    rows: list[tuple[str, str, float]] = []
    for code, name in universe.items():
        series = klines_by_code.get(code) or []
        for prev, cur in zip(series, series[1:]):
            prev_date, prev_close = prev
            cur_date, cur_close = cur
            if prev_close == 0:
                continue
            pct = (cur_close - prev_close) / prev_close * 100.0
            rows.append((cur_date, name, pct))
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="theme_history 冷启动回补（#406）")
    parser.add_argument("--beg", default=DEFAULT_BEG, help="回补起点 YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="回补终点 YYYYMMDD")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL, help="请求间隔秒（默认 3.0）"
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="单请求超时秒")
    parser.add_argument("--out", default=None, help="theme_history.csv 路径（默认 runtime 落点）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    parser.add_argument("--force", action="store_true", help="绕过板块数门槛")
    args = parser.parse_args(argv)

    session = _session()
    try:
        universe = fetch_board_universe(session, timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[backfill] 板块全集抓取失败：{exc}", file=sys.stderr)
        return 1
    print(f"[backfill] 板块全集：{len(universe)} 个")

    drift: list[str] = []
    if len(universe) < MIN_UNIVERSE:
        drift.append(f"板块全集仅 {len(universe)} 个（< {MIN_UNIVERSE}）")

    klines_by_code: dict[str, list[tuple[str, float]]] = {}
    empty = 0
    total = len(universe)
    for idx, (code, name) in enumerate(sorted(universe.items()), start=1):
        try:
            series = fetch_kline(
                session, code, beg=args.beg, end=args.end, timeout=args.timeout
            )
        except Exception as exc:  # noqa: BLE001 - 单板块失败不阻断整批
            print(f"[backfill] {code} {name} 抓取失败：{exc}", file=sys.stderr)
            empty += 1
            series = []
        if not series:
            empty += 1
        klines_by_code[code] = series
        if idx % 50 == 0 or idx == total:
            print(f"[backfill] 进度 {idx}/{total}（空 {empty}）")
        if idx < total:
            time.sleep(args.interval)

    empty_ratio = empty / total if total else 1.0
    if empty_ratio > MAX_EMPTY_RATIO:
        drift.append(f"空 K 线板块占比 {empty_ratio:.0%}（> {MAX_EMPTY_RATIO:.0%}）")

    rows = build_backfill_rows(universe, klines_by_code)
    print(f"[backfill] 回补行：{len(rows)}（板块 {total - empty}/{total} 有 K 线）")
    if not rows:
        print("[backfill] 无回补行，退出", file=sys.stderr)
        return 1

    if args.dry_run:
        print("[backfill] dry-run：不落盘")
        if drift:
            print(f"[backfill] 漂移告警（dry-run 不阻断）：{'；'.join(drift)}", file=sys.stderr)
        return 2 if drift else 0

    try:
        written = append_backfill_rows(
            rows, path=args.out, force=args.force
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[backfill] 写入失败：{exc}", file=sys.stderr)
        return 1
    print(f"[backfill] 已写入 {written} 行" + ("（全部为空洞已填，幂等跳过）" if not written else ""))

    if drift:
        print(
            f"[backfill] ⚠️ 漂移告警：{'；'.join(drift)}——有效数据已落盘，退出码 2",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
