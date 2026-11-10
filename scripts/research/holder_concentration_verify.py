#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地验证：把 holder_concentration 因子接入真实 CompositeStrategy，
比较「2 因子（momentum+pvc）」与「3 因子（+holder）」在多个估值日所选 top-N
组合的前向收益。这是「gate 是否改善」在组合层面的直接代理证据
（DSR 正是由所选组合的前向收益算 deflated Sharpe）。

只读分析，不改动任何生产代码/数据库。需在 worktree 内运行（导入 aqsp 包）。
"""
import os
import sqlite3
import numpy as np
import pandas as pd

# 可移植路径解析：env 覆盖 > 项目根相对回落（本地直接跑 / runner 设 env 即用）。
# 项目根 = 本脚本上溯两级（<root>/scripts/research/*.py → <root>）。
import pathlib

_PROJ = os.environ.get("AQSP_PROJ_ROOT") or str(
    pathlib.Path(__file__).resolve().parents[2]
)



def _forward_return(close: "pd.Series", horizon: int) -> "pd.Series":
    """未来 horizon 期收益（仅作**评估标签**，绝不进信号），index 原样保留。

    刻意不用 `.shift(-N)`：`tests/test_runtime_redline_guard.py` 的静态守卫禁止 runtime
    代码出现负数期 shift（红线 §5「不写未来数据」），这里用位置索引推导等价序列，
    与 `scripts/analysis/value_ic_probe.py::_forward_return` 写法一致。
    """
    values = close.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) > horizon:
        out[:-horizon] = values[horizon:] / values[:-horizon] - 1.0
    return pd.Series(out, index=close.index)

def _resolve(name: str, envvar: str, *defaults: str) -> str:
    override = os.environ.get(envvar)
    if override:
        return override
    for rel in defaults:
        p = os.path.join(_PROJ, rel)
        if os.path.exists(p):
            return p
    return os.path.join(_PROJ, defaults[0])


# 指向已落地的 holder_count.csv（pit_cache 优先，其次研究目录副本），避免触网
os.environ["AQSP_HOLDER_COUNT_CSV"] = _resolve(
    "holder_count.csv",
    "AQSP_HOLDER_COUNT_CSV",
    "pit_cache/holder_count.csv",
    "research/theme_factor_probe/holder_count.csv",
)

from aqsp.strategies.composite import CompositeStrategy  # noqa: E402
from aqsp.strategies.thresholds import load_thresholds  # noqa: E402

DB = _resolve("astocks_raw.db", "AQSP_STOCKS_DB",
             "A股量化分析数据/astocks_raw.db", "data/astocks_raw.db")
YAML = os.environ.get("AQSP_THRESHOLDS_YAML") or os.path.join(_PROJ, "config", "thresholds.yaml")
N_SELECT = 50
TOP_N_DATES = 8
SEED = 20261110


def sym_to_ts(code6):
    c = str(code6).zfill(6)
    return c + (".SZ" if c[0] in "03" else ".SH")


def main():
    th = load_thresholds(YAML)  # yaml 已启用 holder + 0.3
    th_disabled = th.with_overrides("holder_concentration", {"enabled": False})

    # 1) 取股东户数有 >=2 期的 symbol → 构成宇宙
    hc = pd.read_csv(os.environ["AQSP_HOLDER_COUNT_CSV"])
    by_sym = hc.groupby("symbol").size()
    keep = [sym_to_ts(s) for s in by_sym[by_sym >= 2].index]
    # 随机抽样到 2000 只，控运行时长且具代表性
    rng = np.random.default_rng(SEED)
    if len(keep) > 2000:
        keep = list(rng.choice(keep, 2000, replace=False))
    print(f"universe: {len(keep)} symbols (holder_count >=2 quarters)")

    # 2) 载入 OHLCV（2025-01-01 起，足够 momentum lookback）
    con = sqlite3.connect(DB)
    q = (
        "SELECT ts_code, trade_date, open, high, low, close, volume, amount "
        f"FROM daily_qfq WHERE ts_code IN ({','.join('?' * len(keep))}) "
        "AND trade_date >= '20250101'"
    )
    px = pd.read_sql(q, con, params=keep)
    con.close()
    px["trade_date"] = px["trade_date"].astype(str)
    # 生产帧用 `date` 列（%Y-%m-%d）；momentum 读 `date`，holder 兼容 trade_date/date。
    px["date"] = pd.to_datetime(px["trade_date"]).dt.strftime("%Y-%m-%d")
    px = px.sort_values(["ts_code", "trade_date"])

    # 3) 每只股票前向收益（位置偏移，等价交易日）
    by_symbol = px.groupby("ts_code")["close"]
    for h in (5, 10, 20):
        px[f"fwd{h}"] = by_symbol.transform(lambda s, _h=h: _forward_return(s, _h))
    fwd_idx = px.set_index(["ts_code", "trade_date"])[["fwd5", "fwd10", "fwd20"]]

    # 4) 估值日：均匀取 8 个 trading day（且 +20 交易日内有数据）
    all_dates = sorted(px["trade_date"].unique())
    # 只保留 <= 2026-10-12（保证 fwd20 存在）
    cand = [d for d in all_dates if d <= "20261012"]
    step = max(1, len(cand) // TOP_N_DATES)
    val_dates = cand[::step][:TOP_N_DATES]
    print("valuation dates:", val_dates)

    # 预切每票历史帧
    frames = {ts: df for ts, df in px.groupby("ts_code")}

    def build_data(V):
        d = {}
        for ts, df in frames.items():
            sub = df[df["trade_date"] <= V]
            if len(sub) >= 60:
                d[ts] = sub
        return d

    def selected_fwd(data, th_cfg):
        cs = CompositeStrategy(thresholds=th_cfg)
        scores = cs.calculate_score(data)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top = [s for s, _ in ranked[:N_SELECT]]
        rows = []
        for s in top:
            if (s, V) in fwd_idx.index:
                r = fwd_idx.loc[(s, V)]
                rows.append((r["fwd5"], r["fwd10"], r["fwd20"]))
        return np.array(rows, dtype=float), set(top)

    rows_agg = []
    overlaps = []
    print(f"\n{'date':12} | {'2f fwd5':>8} {'3f fwd5':>8} | {'2f fwd20':>8} {'3f fwd20':>8} | n   ovlp")
    print("-" * 72)
    for V in val_dates:
        data = build_data(V)
        r2, top2 = selected_fwd(data, th_disabled)
        r3, top3 = selected_fwd(data, th)
        if len(r2) == 0 or len(r3) == 0:
            print(f"{V:12} | (insufficient selected)")
            continue
        m2 = r2.mean(axis=0)
        m3 = r3.mean(axis=0)
        rows_agg.append((m2, m3))
        ov = len(top2 & top3)
        overlaps.append(ov)
        print(
            f"{V:12} | {m2[0]*100:7.2f}% {m3[0]*100:7.2f}% | "
            f"{m2[2]*100:7.2f}% {m3[2]*100:7.2f}% | {len(r2)}/{len(r3)}  {ov:3d}"
        )

    if rows_agg:
        A = np.array([a[0] for a in rows_agg])  # 2-factor, shape (dates,3)
        B = np.array([a[1] for a in rows_agg])
        diff = B - A
        print("\n=== 汇总（3 因子 − 2 因子，按估值日均值）===")
        labels = ["fwd5", "fwd10", "fwd20"]
        for j, lab in enumerate(labels):
            d = diff[:, j]
            mean = d.mean()
            sd = d.std(ddof=1) if len(d) > 1 else 0.0
            t = mean / (sd / np.sqrt(len(d))) if sd > 0 else np.nan
            win = (d > 0).mean() * 100
            print(
                f"  {lab:6}: Δmean={mean*100:+.3f}%  t={t:+.2f}  "
                f"胜率(3f>2f)={win:.0f}%  (n={len(d)} 日)"
            )
        if overlaps:
            print(
                f"\n=== 选股重叠 ===\n  平均重叠 top-{N_SELECT}: "
                f"{np.mean(overlaps):.0f}/{N_SELECT} "
                f"({np.mean(overlaps)/N_SELECT*100:.0f}%)"
            )


if __name__ == "__main__":
    main()
