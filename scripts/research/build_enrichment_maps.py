#!/usr/bin/env python3
"""路线② 富化映射生成器：从本地 astocks_raw.db 批量算「年化波动 + 近期日均量」。

定位：路线② 组合风险切片（:mod:`aqsp.portfolio.risk_overlay`）需要逐票的
``annual_vol`` / ``turnover`` / ``market_cap`` / ``sector`` 富化映射。其中：

- **年化波动**（``annual_vols.json``）：近 ``--vol-days`` 交易日 close 日收益率的
  年化 std（复权价算收益率比不复权更合理）。DB 可自给。
- **近期日均量**（``avg_volume.json``）：近 ``--vol-days`` 日均成交量（股），
  作为**流动性/换手代理**（DB 无流通股本，算不出真换手率，量级即代理）。
- **市值 / 行业**：``stocks`` 表只有 code+name，**无源** ⇒ 本脚本**不出**这两类，
  它们走 :func:`apply_risk_overlay` 的中性 fail-soft（或另接估值 PIT 采集）。

PIT 纪律：``--as-of`` 严格切片（只取 ``trade_date <= as_of``），**绝不取未来**。
重计算放 runner：全市场 5836 票请放 runner；本机仅小 universe 轻量验证。

用法（本机小 universe 轻量验证）::

    PYTHONPATH=src python scripts/research/build_enrichment_maps.py \
      --db "A股量化分析数据/astocks_raw.db" --as-of 20261106 \
      --max-symbols 300 --vol-days 60 \
      --out-vol outputs/enrich/annual_vols.json \
      --out-volm outputs/enrich/avg_volume.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from aqsp.data.sqlite_db_source import SqliteDbSource


def _parse_asof(raw: str) -> date:
    raw = raw.strip().replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        raise SystemExit(f"--as-of 需 8 位 YYYYMMDD（或 YYYY-MM-DD）: {raw}")
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))


def _annual_vol(close: np.ndarray) -> float:
    """close 序列 → 日收益率年化 std（<2 点 ⇒ NaN，调用方 fail-soft 跳过）。"""
    if close is None or len(close) < 2:
        return float("nan")
    ret = np.diff(close) / close[:-1]
    if len(ret) < 2:
        return float("nan")
    return float(np.std(ret) * np.sqrt(252.0))


def _avg_volume(volume: np.ndarray) -> float:
    if volume is None or len(volume) == 0:
        return float("nan")
    v = volume[~np.isnan(volume)]
    return float(v.mean()) if v.size else float("nan")


def build_maps(
    source: SqliteDbSource,
    universe: list[str],
    start: date,
    end: date,
    *,
    vol_days: int,
) -> tuple[dict[str, float], dict[str, float]]:
    """PIT 切片加载后，逐票算 年化波动 / 日均量（纯计算、确定性）。

    - 返回 ``{ts_code: value}``，缺数据/过短的票**不写入**（调用方按缺失走中性）。
    - 只读 ``close``（qfq 表 close 列）+ ``volume``；不碰生产、不写库。
    """
    frames = source.fetch_daily(universe, start=start, end=end, adjust="")
    vols: dict[str, float] = {}
    avgs: dict[str, float] = {}
    for code, df in frames.items():
        if df is None or getattr(df, "empty", True):
            continue
        close = np.asarray(df.get("close"), dtype=float)
        volume = np.asarray(df.get("volume"), dtype=float)
        if close.ndim != 1 or volume.ndim != 1:
            continue
        # PIT：fetch_daily 已 end<=as_of；再截近 vol_days 根做窗口统计
        a = _annual_vol(close[-vol_days:] if len(close) > vol_days else close)
        if not np.isnan(a):
            vols[code] = round(a, 6)
        b = _avg_volume(volume[-vol_days:] if len(volume) > vol_days else volume)
        if not np.isnan(b):
            avgs[code] = round(b, 3)
    return vols, avgs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="路线② 富化映射生成器（年化波动 + 日均量；PIT、只读、确定性）"
    )
    ap.add_argument("--db", required=True, help="astocks_raw.db 路径")
    ap.add_argument("--as-of", required=True, help="切片日 YYYYMMDD / YYYY-MM-DD")
    ap.add_argument("--vol-days", type=int, default=60, help="波动/量统计窗（交易日）")
    ap.add_argument("--max-symbols", type=int, default=None, help="universe 取流动性前 N（缺省全市场）")
    ap.add_argument("--symbols-file", default=None, help="预筛 universe（一行一个 ts_code）")
    ap.add_argument("--out-vol", default=None, help="年化波动 JSON {ts_code: value}")
    ap.add_argument("--out-volm", default=None, help="日均量 JSON {ts_code: value}")
    args = ap.parse_args(argv)

    end = _parse_asof(args.as_of)
    start = end - timedelta(days=max(90, args.vol_days * 2))

    source = SqliteDbSource(db_path=args.db)
    if args.symbols_file:
        universe = [
            ln.strip() for ln in Path(args.symbols_file).read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
    elif args.max_symbols and args.max_symbols > 0:
        universe = source.get_liquid_symbols(limit=args.max_symbols, min_amount=0.0)
    else:
        universe = [s for s in source.get_available_symbols()]
    if not universe:
        print("[warn] universe 为空，无映射可生成", file=sys.stderr)
        return 0

    vols, avgs = build_maps(source, universe, start, end, vol_days=args.vol_days)

    if args.out_vol:
        Path(args.out_vol).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_vol).write_text(json.dumps(vols, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if args.out_volm:
        Path(args.out_volm).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_volm).write_text(json.dumps(avgs, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"[enrich] as_of={args.as_of} universe={len(universe)} "
        f"with_vol={len(vols)} with_avgvol={len(avgs)} vol_days={args.vol_days}"
    )
    if args.out_vol:
        print(f"[written] {args.out_vol}")
    if args.out_volm:
        print(f"[written] {args.out_volm}")
    print("[note] 市值/行业 DB 无源（stocks 仅 code+name），这两类走中性 fail-soft 或另接估值 PIT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
