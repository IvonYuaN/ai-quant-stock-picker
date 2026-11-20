#!/usr/bin/env python3
"""短线选股器 · 候选模式验证框架（研究侧，零生产改动，重计算放 runner）。

老大的需求
----------
每天从**主板**（60%/00%，排除创业板 30x / 科创板 68x / 北交所）里选出 **3~5 只**，
要有**不同的理解、好的逻辑与持续性**（曲线好 / 打板 / 情绪 / 其它形态），
我自主去市场挖各种模式、验证、把好用的固化。3 年数据够用（红线，不 5 年回填）。

设计原则（防过拟合 + 可解释 + 可持续）
----------------------------------------
1. **可解释**：每条模式是一个有名字的、能用人话讲清逻辑的短线 setup
   （涨停接力 / 放量突破 / 多头排列 / 回踩确认 / 强势动量），不是黑盒打分。
2. **持续性**：3 年逐截面回测该模式作为筛选器，超额是否**跨行情稳定**
   （分 regime / 分年度）；实盘用法 = 只在最近一个截面出命中票。
3. **防过拟合（预注册 fail-closed）**：候选模式是**事先定好的 5 条种子**
   （5 类 lane），不是几百条乱扫里挑最过拟合那条。
   判据门 = NW-t ≥ +2.0 且 胜率 ≥ 60% 且 分 regime 不显著为负，才进候选；
   不满足一律 fail-closed，不回头调参。
4. **口径与既有探针逐位一致**（记忆红线）：
   - 基准 = 同截面**主板池等权**收益（`fwd[t][ok].mean()`），不跨期累乘、不手抄。
   - 收益 = 每截面命中票 `close_qfq[t+h]/close_qfq[t]−1` 单期截面等权（shift(-h) 是 label）。
   - 统计 = 逐截面超额序列 + Newey-West t（lag 由自相关放大，NW 红线）。
   - 特征只用 ≤ t 数据（rolling/shift 全向后看，无前视）；listing-aware。
   - 涨停/突破阈值用前复权价（分红除权不干扰）。

用法（runner，重计算）
----------------------
    /opt/aqsp-runner/venv/bin/python \\
      research/shortline_probe.py \\
      --db /opt/aqsp-runner/data/astocks_raw.db \\
      --start 20231101 --horizons 1,5,10 --out /tmp/shortline_probe.csv
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_OBS = 60      # 截面最少有效票数（listing-aware 门）
CRIT_T = 2.0      # 候选模式判据门：NW-t ≥ +2（预注册）
MIN_WIN = 0.60    # 候选模式判据门：胜率 ≥ 60%（跑赢同截面池比例）
REGIMES = ("bear_熊", "sideways_震荡", "bull_牛")

# 主板池：60%/00% 前缀（000/001/002/003 沪深主板 + 600/601/603/605）
_MAIN_PREFIX = ("60", "00")
_EXCL_PREFIX = ("68", "43", "83", "87", "92")
# 创业板（300/301）—— 2026-11-19 老大拍板纳入，涨停幅度 20%
_GB_PREFIX = ("30",)
# 选股池（主板+创业）排除：科创板 68 + 北交 43/83/87/92；30/31 放行
_PICK_EXCL_PREFIX = ("68", "43", "83", "87", "92")


def is_main_board(ts: str) -> bool:
    p = ts[:2]
    if p in _EXCL_PREFIX:
        return False
    return p in _MAIN_PREFIX


def is_pick_universe(ts: str) -> bool:
    """主板(60/00) + 创业板(30)；排科创(68) + 北交(43/83/87/92)。"""
    p = ts[:2]
    if p in _PICK_EXCL_PREFIX:
        return False
    return p in _MAIN_PREFIX or p in _GB_PREFIX


def limit_pct(ts: str) -> float:
    """板块涨停幅度：创业板(30/31)=0.20，主板(60/00)=0.10。ST 暂不细分。"""
    p = ts[:2]
    return 0.20 if p in _GB_PREFIX else 0.10


def limit_price(prev_close: float, ts: str) -> float:
    """今涨停价 = round(昨收 * (1+幅度), 2)（按板块）。"""
    return round(prev_close * (1.0 + limit_pct(ts)), 2)



def load_main_board(db: str, start: str) -> dict:
    """读主板池 OHLCV。返回 dict P：
    - close   = close_qfq（前复权，跨日可比：fwd/MA/动量/涨停涨幅）
    - cqr     = close（非复权，与 open/high/low 同基准：日内跳空/回踩）
    - opn/high/low = 非复权（日内形态用，库只算了 close_qfq，其余 qfq 列全空）
    - vol     = volume
    """
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    sql = """
    SELECT ts_code, trade_date, open, high, low, close, close_qfq, volume
    FROM daily_qfq
    WHERE trade_date >= ? AND (ts_code LIKE '60%' OR ts_code LIKE '00%')
    ORDER BY ts_code, trade_date
    """
    df = pd.read_sql_query(sql, con, params=[start])
    con.close()
    df["ts_code"] = df["ts_code"].astype(str)
    df = df[df["ts_code"].map(is_main_board)]
    cols = {"close": "close_qfq", "cqr": "close",
            "opn": "open", "high": "high", "low": "low", "vol": "volume"}
    out = {}
    for k, c in cols.items():
        p = df.pivot(index="trade_date", columns="ts_code", values=c)
        p.index = pd.to_datetime(p.index, format="%Y%m%d")
        out[k] = p.sort_index()
    base = out["close"]
    for k in out:
        out[k] = out[k].reindex(index=base.index, columns=base.columns)
    return out


def load_pick_universe(db: str, start: str, include_chuangye: bool = True) -> dict:
    """选股池 OHLCV：主板(60/00) [+ 创业板(30)]，排科创(68)+北交(43/83/87/92)。
    返回 dict P（同 load_main_board 键），并额外带：
      - P["cols"]     : ts_code 列表（与 close 列序一致）
      - P["col_lim"]  : 每股涨停幅度数组（主板0.10 / 创业0.20），供检测器算几板/涨停价
      - P["universe"] : "主板" 或 "主板+创业"
    回测口径（shortline_probe 的 5 lane）仍用 load_main_board 的纯主板池，保持 §二 已验证数不变；
    选股池（主板+创业）只喂给实盘检测/推送。
    """
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    like = "ts_code LIKE '60%' OR ts_code LIKE '00%'"
    if include_chuangye:
        like += " OR ts_code LIKE '30%'"
    sql = """
    SELECT ts_code, trade_date, open, high, low, close, close_qfq, volume
    FROM daily_qfq
    WHERE trade_date >= ? AND (%s)
    ORDER BY ts_code, trade_date
    """ % like
    df = pd.read_sql_query(sql, con, params=[start])
    con.close()
    df["ts_code"] = df["ts_code"].astype(str)
    df = df[df["ts_code"].map(is_pick_universe)]
    cols = {"close": "close_qfq", "cqr": "close",
            "opn": "open", "high": "high", "low": "low", "vol": "volume"}
    out = {}
    for k, c in cols.items():
        p = df.pivot(index="trade_date", columns="ts_code", values=c)
        p.index = pd.to_datetime(p.index, format="%Y%m%d")
        out[k] = p.sort_index()
    base = out["close"]
    for k in out:
        out[k] = out[k].reindex(index=base.index, columns=base.columns)
    out["cols"] = [str(c) for c in base.columns]
    out["col_lim"] = np.array([limit_pct(c) for c in out["cols"]], dtype=float)
    out["universe"] = "主板+创业(30)" if include_chuangye else "主板"
    return out


def market_state(close: pd.DataFrame, win: int = 60) -> np.ndarray:
    """每截面 60 日全市场中位数收益（只用 ≤t，无前视）。"""
    cv = close.to_numpy(dtype=float)
    T = len(cv)
    out = np.full(T, np.nan, dtype=float)
    for t in range(win, T):
        r = cv[t] / cv[t - win] - 1
        r = r[np.isfinite(r)]
        if len(r) >= MIN_OBS:
            out[t] = float(np.median(r))
    return out


def regime_buckets(state: np.ndarray, snap: list[int], k: int = 3):
    vals = state[snap]
    ok = np.isfinite(vals)
    if ok.sum() < k:
        return {i: REGIMES[1] for i in snap}
    cuts = np.quantile(vals[ok], [1.0 / k, 2.0 / k])
    lab = {}
    for i, s in enumerate(vals):
        if not ok[i]:
            lab[snap[i]] = REGIMES[1]
        elif s <= cuts[0]:
            lab[snap[i]] = REGIMES[0]
        elif s <= cuts[1]:
            lab[snap[i]] = REGIMES[1]
        else:
            lab[snap[i]] = REGIMES[2]
    return lab


def _acf_lag(vals: np.ndarray, cap: int = 20) -> int:
    """Newey-West lag：按序列自相关放大（慢因子 t 虚高红线，须放大 lag）。"""
    v = vals - vals.mean()
    n = len(v)
    if n < 4:
        return 1
    denom = float((v ** 2).sum())
    if denom <= 0:
        return 1
    lag = 1
    for L in range(1, min(cap, n // 4) + 1):
        acf = float((v[L:] * v[:n - L]).sum() / denom)
        if abs(acf) > 0.5:
            lag = max(lag, L)
    return lag


def _nw_t(vals: np.ndarray, lag: int) -> float:
    """Newey-West t（均值显著性，对自相关稳健）。"""
    v = vals - vals.mean()
    n = len(v)
    if n < 2:
        return float("nan")
    var = float((v ** 2).sum()) / n
    if var <= 0:
        return float("nan")
    se = var / n
    for L in range(1, lag + 1):
        acf = float((v[L:] * v[:n - L]).sum()) / (n * var)
        se += 2.0 * (acf * var) / n
    se = math.sqrt(se)
    if se == 0:
        return float("nan")
    return float(vals.mean() / se)


# ============================================================
# 候选模式（可解释 setup，事先预注册的种子集，非乱扫）
# 统一签名 fn(P) -> (mask, strength)
#   P: load_main_board 返回的 dict
#       close = close_qfq（前复权，跨日可比：fwd/MA/动量/涨停真实涨幅）
#       cqr   = close（非复权，与 open/high/low 同日内基准：跳空/回踩/日内形态）
#       opn/high/low = 非复权 OHLC（库只算了 close_qfq，其余 qfq 列全空）
#       vol   = volume
#   mask     : (T,N) bool，某票某截面是否命中该短线 setup。
#   strength : (T,N) float，命中票「信号强度」（越大越优先），全 ≤ t（PIT，无前视）。
# 命中数 ≤ top_n 时全取；> top_n 时按 strength 截断。
# ============================================================
def pattern_zt_nextday_relay(P):
    """【打板·涨停次日高开接力】前一日涨停(跨日复权涨幅≥9.8%) 且 今日高开(非复权跳空>0)。
    逻辑：打板资金次日溢价惯性；持续性=短线情绪接力。
    strength = 今日跳空幅度（非复权 open/昨close−1，实盘可知，越大越强）。"""
    close = P["close"]
    cqr = P["cqr"]
    opn = P["opn"]
    cv = close.to_numpy(dtype=float)
    zt_qfq = close / close.shift(1) - 1.0
    is_zt_prev = zt_qfq.shift(1) >= 0.098          # 前一日涨停（跨日复权涨幅）
    with np.errstate(invalid="ignore", divide="ignore"):
        open_gap = opn / cqr.shift(1) - 1.0        # 今日跳空（非复权日内）
    mask = np.asarray(is_zt_prev & (open_gap > 0), dtype=bool).copy()
    bad = (~np.isfinite(open_gap.to_numpy(dtype=float, copy=False)) | ~np.isfinite(cv))
    mask[bad] = False
    st = open_gap.to_numpy(dtype=float).copy()
    st[~mask] = np.nan
    return mask, st


def pattern_vol_breakout_ma20(P):
    """【量价·放量突破】今日量≥1.5×前5日均量 且 收>前20日最高价（突破平台）。
    逻辑：放量创新高=资金进场确认；持续性=突破后惯性。
    strength = 量比（实盘可知，越大越强）。"""
    close = P["close"]
    vol = P["vol"]
    cv = close.to_numpy(dtype=float)
    vol5 = vol.rolling(5).mean().shift(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        vr = vol / vol5
    high20 = close.rolling(20).max().shift(1)
    mask = np.asarray((vr >= 1.5) & (close > high20), dtype=bool).copy()
    bad = (~np.isfinite(vr.to_numpy(dtype=float, copy=False)) |
           ~np.isfinite(high20.to_numpy(dtype=float, copy=False)) | ~np.isfinite(cv))
    mask[bad] = False
    st = vr.to_numpy(dtype=float).copy()
    st[~mask] = np.nan
    return mask, st


def pattern_moving_average_stack(P):
    """【曲线·多头排列】MA5>MA10>MA20>MA60（曲线向上、趋势健康）。
    逻辑：均线多头=中期趋势确认；持续性=趋势跟随。
    strength = 均线排列度 (MA5-MA60)/MA60（实盘可知，越大越强）。"""
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    mask = np.asarray((ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60), dtype=bool).copy()
    bad = ~np.isfinite(ma60.to_numpy(dtype=float, copy=False)) | ~np.isfinite(cv)
    mask[bad] = False
    with np.errstate(invalid="ignore", divide="ignore"):
        st_arr = ((ma5 - ma60) / ma60).to_numpy(dtype=float).copy()
    st_arr[~mask] = np.nan
    return mask, st_arr


def pattern_pullback_ma20(P):
    """【形态·回踩确认】近 5 日最低价触及 MA20(±3%) 但今日收>MA20（回踩企稳）。
    日内形态统一用非复权基准（cqr/low）；逻辑：强势回调到 20 日线企稳=买点。
    strength = 站回 MA20 程度 (close-MA20)/MA20（非复权，实盘可知，越大越强）。"""
    cqr = P["cqr"]
    low = P["low"]
    cv = cqr.to_numpy(dtype=float)
    ma20 = cqr.rolling(20).mean()
    low5 = low.rolling(5).min()
    with np.errstate(invalid="ignore", divide="ignore"):
        near = ((low5 - ma20).abs() / ma20) <= 0.03     # 触及 ±3%（放宽，回踩带）
        st_arr = ((cqr - ma20) / ma20).to_numpy(dtype=float).copy()
    mask = np.asarray(near & (cqr > ma20), dtype=bool).copy()
    bad = (~np.isfinite(ma20.to_numpy(dtype=float, copy=False)) | ~np.isfinite(cv))
    mask[bad] = False
    st_arr[~mask] = np.nan
    return mask, st_arr


def pattern_high_momentum_20d(P):
    """【动量·20日强势前20%】20 日涨幅列同截面前 20%（强势延续）。
    逻辑：强势票短线续涨概率高；持续性=动量延续。
    strength = 20 日涨幅（实盘可知，越大越强）。"""
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    r20 = close / close.shift(20) - 1.0
    q80 = r20.quantile(0.80, axis=1)            # 每截面 80 分位（Series, index=dates）
    mask = np.asarray(r20 >= q80.to_numpy().reshape(-1, 1), dtype=bool).copy()
    mask[~np.isfinite(cv)] = False
    st = r20.to_numpy(dtype=float).copy()
    st[~mask] = np.nan
    return mask, st


PATTERNS = {
    "打板_涨停次日高开接力": pattern_zt_nextday_relay,
    "量价_放量1.5倍突破20日新高": pattern_vol_breakout_ma20,
    "曲线_MA5_10_20_60多头排列": pattern_moving_average_stack,
    "形态_回踩MA20企稳确认": pattern_pullback_ma20,
    "动量_20日强势前20%": pattern_high_momentum_20d,
}


def _compute_pattern_masks(args):
    P = load_main_board(args.db, args.start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)
    masks = {}      # name -> mask
    strengths = {}  # name -> strength (PIT, 命中票越大越优先)
    for name, fn in PATTERNS.items():
        try:
            m, s = fn(P)
            m = np.asarray(m, dtype=bool)
            s = np.asarray(s, dtype=float)
            if m.shape != cv.shape:
                m = np.zeros_like(cv, dtype=bool)
                s = np.full_like(cv, np.nan, dtype=float)
            masks[name] = m
            strengths[name] = s
        except Exception as exc:  # noqa: BLE001 - 单模式失败不挡其它
            print(f"[warn] pattern {name} failed: {exc!r}", flush=True)
            masks[name] = np.zeros_like(cv, dtype=bool)
            strengths[name] = np.full_like(cv, np.nan, dtype=float)
    return close, cv, masks, strengths


def _listing_mask(close: pd.DataFrame, lookback: int = 30) -> np.ndarray:
    """listing-aware：某票在 t 日须已有 ≥ lookback 个有效历史价才参与（红线）。"""
    cv = close.to_numpy(dtype=float)
    valid = np.isfinite(cv)
    cum = np.cumsum(valid, axis=0)
    T, N = cv.shape
    mask = np.zeros((T, N), dtype=bool)
    for t in range(lookback, T):
        mask[t] = cum[t] >= lookback
    return mask


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="20231101")
    ap.add_argument("--horizons", default="1,5,10")
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--mstate-win", type=int, default=60)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    close, cv, masks, strengths = _compute_pattern_masks(args)
    dates = close.index
    T = cv.shape[0]
    fwd = {h: (close.shift(-h) / close - 1.0).to_numpy(dtype=float) for h in horizons}
    listing = _listing_mask(close, lookback=30)
    mstate = market_state(close, args.mstate_win)
    print(f"[load] main-board T={T} N={cv.shape[1]} patterns={len(masks)}", flush=True)

    records = []
    for h in horizons:
        f = fwd[h]
        snap = list(range(260, T - h, args.step))
        cap = max(1, len(snap) // 4)
        lab = regime_buckets(mstate, snap)
        snap_years = {i: dates[snap[i]].year for i in range(len(snap))}
        years = sorted(set(snap_years.values()))
        print(f"[h={h}] {len(snap)} 截面 / top_n={args.top_n} / 年份 {years}", flush=True)

        for pname, pmask in masks.items():
            pstr = strengths[pname]
            series = []
            hit_days = 0
            for i, t in enumerate(snap):
                ok = listing[t] & np.isfinite(cv[t])
                hit = pmask[t] & ok
                if hit.sum() < 3:      # 该截面命中太少，跳过（无统计意义）
                    continue
                fr = f[t]
                ok2 = ok & np.isfinite(fr)
                hit2 = hit & ok2
                if hit2.sum() < 3:
                    continue
                hit_days += 1
                ret = fr[ok2]
                bench = float(ret.mean())
                if not np.isfinite(bench):
                    continue
                # 命中票按 PIT 信号强度取 top_n（实盘可知；不用未来收益 fr 排序 = 无前视）
                col = np.where(ok2)[0]
                st_row = pstr[t, col]
                st_hit = st_row[hit2[ok2]]
                valid = np.isfinite(st_hit)
                if valid.sum() < 1:
                    continue
                k = min(args.top_n, int(hit2.sum()))
                # 取强度最高的前 k 只；强度相同/无效时退回全取
                rank = np.argsort(st_hit[valid])[::-1]
                chosen = np.where(valid)[0][rank[:k]]
                sel_ret = ret[hit2[ok2]][chosen]
                exc = float(sel_ret.mean()) - bench
                series.append((exc, lab[t], i))
            if len(series) < 20:
                print(f"    {pname}: 命中截面 {hit_days} <20，跳过", flush=True)
                continue
            vals = np.asarray([e for (e, _, _) in series])
            lag = _acf_lag(vals, cap)
            t_nw = _nw_t(vals, lag)
            win = float((vals > 0).mean())
            reg_t = {}
            for R in REGIMES:
                rv = np.asarray([e for (e, rg, _) in series if rg == R])
                if len(rv) >= 10:
                    reg_t[R] = _nw_t(rv, _acf_lag(rv, cap))
            year_t = {}
            for Y in years:
                yv = np.asarray([e for (e, rg, i) in series if snap_years[i] == Y])
                if len(yv) >= 10:
                    year_t[Y] = _nw_t(yv, _acf_lag(yv, cap))
            sig_reg_neg = sum(1 for v in reg_t.values() if v < -CRIT_T)
            viable = (
                t_nw >= CRIT_T
                and win >= MIN_WIN
                and sig_reg_neg == 0
            )
            records.append({
                "horizon": h, "pattern": pname, "excess_mean": float(vals.mean()),
                "excess_t_nw": t_nw, "win": win, "n_sections": len(series),
                "regime_t": json.dumps(reg_t, ensure_ascii=False),
                "year_t": json.dumps({str(kk): vv for kk, vv in year_t.items()}),
                "viable": viable,
            })
            print(f"    {pname[:24]:24s}: 超额 {vals.mean():+.4%} t={t_nw:+.2f} "
                  f"胜率 {win:.1%} n={len(series)} regime_neg={sig_reg_neg} "
                  f"{'✅ 候选' if viable else '❌ 未过门'}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(out, index=False)
    print(f"\n[written] {out}（{len(records)} 行）", flush=True)

    if args.json_out:
        art = {
            "as_of": dates[-1].strftime("%Y-%m-%d"),
            "universe": "沪深主板(60%/00%, 排创业/科创/北交)",
            "top_n": args.top_n,
            "patterns": records,
            "criterion": f"NW-t≥{CRIT_T} 且 胜率≥{MIN_WIN:.0%} 且 分regime不显著为负（预注册 fail-closed）",
        }
        Path(args.json_out).write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[written] {args.json_out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
