#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真突破探针：用 prod pit_cache 里的非行情维度（股东户数/业绩预告/龙虎榜）
构造与 OHLCV 价格反转正交的新因子，并在本地 daily_qfq 上做 IC 验证。

只读分析，不改动任何生产代码/打分/数据库。
"""
import os
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB = os.getenv("AQSP_DB", "/opt/aqsp-runner/data/astocks_raw.db")
D = str(Path(__file__).resolve().parent)

VALID_TS = set()

def sym_to_ts(code6):
    c = str(code6).zfill(6)
    cand = c + (".SZ" if c[0] in "03" else ".SH")   # 0/3→深, 6/8/4/9→沪
    if cand in VALID_TS:
        return cand
    return c + (".SZ" if c + ".SZ" in VALID_TS else ".SH")

# ---------- 1. 加载价格，预计算前向收益与 OHLCV 反转因子 ----------
print("== loading prices ==")
con = sqlite3.connect(DB)
px = pd.read_sql(
    "SELECT ts_code, trade_date, close_qfq, volume, open_qfq, high_qfq, low_qfq "
    "FROM daily_qfq", con)
con.close()
px["trade_date"] = px["trade_date"].astype(str)
px = px.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
VALID_TS = set(px["ts_code"].unique())
g = px.groupby("ts_code", sort=False)
px["fwd5"]  = g["close_qfq"].shift(-5)  / px["close_qfq"] - 1
px["fwd10"] = g["close_qfq"].shift(-10) / px["close_qfq"] - 1
px["fwd20"] = g["close_qfq"].shift(-20) / px["close_qfq"] - 1
# OHLCV 反转风格代表因子（用于正交性对照）
px["mom20"] = px["close_qfq"] / g["close_qfq"].shift(20) - 1   # 动量（现役为负IC）
px["rev5"]  = px["close_qfq"] / g["close_qfq"].shift(5)  - 1   # 短期反转
px["turn20"]= g["volume"].transform(lambda s: s / s.rolling(20, min_periods=5).mean())
print(f"   prices rows={len(px)}  maxdate={px['trade_date'].max()}")

# 快速查表: (ts_code, date) -> 行
idx = px.set_index(["ts_code", "trade_date"])

# idx 转回带列索引的表，供 merge 使用
_IDX = px[["ts_code", "trade_date", "fwd5", "fwd10", "fwd20",
           "mom20", "rev5", "turn20"]].copy()
_IDX["trade_date"] = _IDX["trade_date"].astype(str)

def lookup(df, datecol):
    """df 含 ts_code, <datecol>；返回合并后的 fwd*/mom20/rev5/turn20。"""
    left = df[["ts_code", datecol]].copy()
    left[datecol] = left[datecol].astype(str)
    merged = df.merge(_IDX, left_on=["ts_code", datecol],
                      right_on=["ts_code", "trade_date"], how="left")
    return merged.drop(columns=["trade_date"])

def spearman_per_group(df, fcol, rcol):
    """按 eval_date 分组算截面 Spearman，返回每组的 ic 序列。"""
    ics = []
    for d, gg in df.groupby("eval_date" if "eval_date" in df else df.columns[0]):
        s = gg[[fcol, rcol]].dropna()
        if len(s) >= 30:
            ic = s[fcol].corr(s[rcol], method="spearman")
            if pd.notna(ic):
                ics.append(ic)
    return np.array(ics)

def agg_ic(ics):
    if len(ics) == 0:
        return dict(n=0, ic_mean=np.nan, icir=np.nan, t=np.nan, pos=0)
    a = np.array(ics)
    n = len(a)
    ic_mean = a.mean()
    ic_std = a.std(ddof=1) if n > 1 else np.nan
    t = ic_mean / (ic_std / np.sqrt(n)) if ic_std and ic_std > 0 else np.nan
    return dict(n=n, ic_mean=ic_mean, icir=(ic_mean / ic_std if ic_std else np.nan),
                t=t, pos=float((a > 0).mean()))

# ================= 因子 A：股东户数变化（面板·筹码集中度） =================
print("\n== Factor A: holder_count 筹码集中度 ==")
hc = pd.read_csv(f"{D}/holder_count.csv")
hc["symbol"] = hc["symbol"].astype(int).astype(str).str.zfill(6)
hc["notice_date"] = pd.to_datetime(hc["notice_date"]).dt.strftime("%Y%m%d")
hc = hc.sort_values(["symbol", "notice_date"])
hc["prev"] = hc.groupby("symbol")["holder_count"].shift(1)
hc["chg"] = (hc["holder_count"] - hc["prev"]) / hc["prev"]
hc["factor"] = -hc["chg"]          # 户数减少=筹码集中=看多
hc = hc.dropna(subset=["factor"])

# 月度估值日（YYYYMMDD；覆盖 2025Q4 ~ 2026Q3，确保 +20 交易日 < 2026-11-06）
eval_dates = [f"2026{ m:02d}28" for m in range(1, 10)] + \
             [f"2025{ m:02d}28" for m in (10, 11, 12)]
snaps = []
for ed in eval_dates:
    tmp = hc[hc["notice_date"] <= ed].groupby("symbol").last().reset_index()
    tmp["eval_date"] = ed
    snaps.append(tmp[["symbol", "eval_date", "factor"]])
A = pd.concat(snaps, ignore_index=True)
A["ts_code"] = A["symbol"].map(sym_to_ts)
A = lookup(A, "eval_date")
A = A.dropna(subset=["fwd20"])
print(f"   eval points with fwd20: {A['eval_date'].nunique()}  stock-dates={len(A)}")
for h in ("fwd5", "fwd10", "fwd20"):
    ics = spearman_per_group(A, "factor", h)
    r = agg_ic(ics)
    print(f"   IC[{h:5}] ngrp={r['n']:2d} IC={r['ic_mean']:+.4f} ICIR={r['icir']:+.3f} "
          f"t={r['t']:+.2f} pos%={r['pos']*100:.0f}")
# 正交性：与 mom20 / rev5 截面相关
print("   正交性(截面Spearman, factor vs OHLCV反转):")
for oc in ("mom20", "rev5", "turn20"):
    ics = spearman_per_group(A, "factor", oc)
    r = agg_ic(ics)
    print(f"     vs {oc:6}: IC={r['ic_mean']:+.4f} (|IC|小=正交)")

# ================= 因子 B：业绩预告增速（事件·基本面） =================
print("\n== Factor B: earnings_forecast 盈利增速 ==")
ef = pd.read_csv(f"{D}/earnings_forecast.csv")
ef = ef[ef["is_latest"]]
ef["symbol"] = ef["symbol"].astype(int).astype(str).str.zfill(6)
ef["notice_date"] = pd.to_datetime(ef["notice_date"]).dt.strftime("%Y%m%d")
ef["growth"] = (ef["change_pct_lower"] + ef["change_pct_upper"]) / 2
efg = ef.groupby(["symbol", "notice_date"])["growth"].mean().reset_index()
efg["ts_code"] = efg["symbol"].map(sym_to_ts)
efg = efg.rename(columns={"notice_date": "eval_date"})
B = lookup(efg, "eval_date")
B = B.dropna(subset=["fwd20"])
print(f"   events with fwd20: {len(B)}")
for h in ("fwd5", "fwd10", "fwd20"):
    s = B[["growth", h]].dropna()
    ic = s["growth"].corr(s[h], method="spearman")
    print(f"   IC[{h:5}] pooled n={len(s)} Spearman={ic:+.4f}")
print("   正交性(截面Spearman, growth vs OHLCV反转, 按 notice_date 分组):")
for oc in ("mom20", "rev5", "turn20"):
    ics = spearman_per_group(B.rename(columns={"growth": "factor"}), "factor", oc)
    r = agg_ic(ics)
    print(f"     vs {oc:6}: IC={r['ic_mean']:+.4f}")

# ================= 因子 C：龙虎榜净买入（描述+正交，本机无前向收益） =================
print("\n== Factor C: longhubang 龙虎榜净买入（本机库终点 2026-11-06 < 事件日，无前向收益） ==")
lh = pd.read_csv(f"{D}/longhubang.csv")
lh["symbol"] = lh["symbol"].astype(int).astype(str).str.zfill(6)
lh["ts_code"] = lh["symbol"].map(sym_to_ts)
lh["trade_date"] = pd.to_datetime(lh["trade_date"]).dt.strftime("%Y%m%d")
lh = lh.rename(columns={"trade_date": "eval_date"})
C = lookup(lh, "eval_date")
print(f"   events={len(C)}  net_amount 均值={C['net_amount'].mean():.1f} 中位={C['net_amount'].median():.1f}")
print(f"   fwd20 非NaN数={C['fwd20'].notna().sum()} (应=0，因事件在库终点之后)")
# 机构买入计数
def inst_cnt(t):
    m = re.findall(r"(\d+)家机构买入", str(t))
    return int(m[0]) if m else 0
C["inst_buy"] = C["interpretation"].map(inst_cnt)
print(f"   有机构买入的事件数={ (C['inst_buy']>0).sum() } / {len(C)}")
print("   正交性(事件日截面Spearman, net_amount vs OHLCV反转):")
for oc in ("mom20", "rev5", "turn20"):
    s = C[["net_amount", oc]].dropna()
    ic = s["net_amount"].corr(s[oc], method="spearman")
    print(f"     vs {oc:6}: IC={ic:+.4f} (n={len(s)})")

print("\nDONE")
