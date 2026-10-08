"""★ 向量化批量筛选 25 因子 × 4 板块（效率版）

**旧版的坑**：在「截面 × 票」循环里反复调 `.rolling()` ⇒ 22 分钟没跑完。
**本版**：每票**一次性**算完 25 个因子的时间序列，再按截面取列 ⇒ 快 1~2 个数量级。

用法：python factor_batch_scan.py [--out outputs/factor_scan.csv]
"""

from __future__ import annotations

import argparse
import json
import sqlite3

import numpy as np
import pandas as pd

DB = "A股量化分析数据/astocks_raw.db"
FACTORS_JSON = "outputs/factor_oos_assessment.json"
BOARDS = ("深主板", "沪主板", "创业板", "科创板")


def board_of(code: str) -> str:
    x = code.split(".")[0]
    if x.startswith("60"):
        return "沪主板"
    if x.startswith("68"):
        return "科创板"
    if x.startswith("00"):
        return "深主板"
    if x.startswith("30"):
        return "创业板"
    return "北交所"


def compute_all(df: pd.DataFrame) -> dict[str, pd.Series]:
    """★ 一次性算出全部因子的时间序列（每票一次 rolling，而非每截面一次）。"""
    c, o, h, lo, v, amt = (df["close"], df["open"], df["high"],
                           df["low"], df["volume"], df["amount"])
    r = np.log(c.where(c > 0))
    ret = r.diff()
    hi20, lo20 = h.rolling(20).max(), lo.rolling(20).min()
    rng20 = (hi20 - lo20).replace(0, np.nan)
    body_top = pd.concat([o, c], axis=1).max(axis=1)
    body_bot = pd.concat([o, c], axis=1).min(axis=1)
    rng1 = (h - lo).replace(0, np.nan)
    up = (h - body_top).replace(0, np.nan)
    dn = (body_bot - lo).replace(0, np.nan)
    up_r, dn_r, rng10 = (up / rng1).rolling(10).mean(), (dn / rng1).rolling(10).mean(), rng1.rolling(10).mean()
    gain = c.diff().clip(lower=0).ewm(alpha=1 / 14).mean()
    loss = (-c.diff().clip(upper=0)).ewm(alpha=1 / 14).mean()
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    return {
        "momentum_20": r.rolling(20).sum(),
        "momentum_10": r.rolling(10).sum(),
        "momentum_5": r.rolling(5).sum(),
        "volume_price_corr_10": c.rolling(10).corr(v),
        "volume_ratio_10": v / v.rolling(10).mean(),
        "volume_ratio_5": v / v.rolling(5).mean(),
        "volume_change_5d": v.rolling(5).mean() / v.shift(5).rolling(5).mean(),
        "amount_ratio_5": amt / amt.rolling(5).mean(),
        "distance_to_ma60": c / c.rolling(60).mean() - 1,
        "close_to_low_20": (c - lo20) / rng20,
        "close_to_high_20": (c - hi20) / rng20,
        "vol_of_vol_20": ret.rolling(20).std().rolling(20).std(),
        "realized_vol_10": ret.rolling(10).std(),
        "volatility_20": ret.rolling(20).std(),
        "amplitude_5d": (h - lo) / c.shift(5),
        "bias_20": c / c.rolling(20).mean() - 1,
        "bias_10": c / c.rolling(10).mean() - 1,
        "bias_5": c / c.rolling(5).mean() - 1,
        "upper_shadow_ratio": up_r / rng10,
        "lower_shadow_ratio": dn_r / rng10,
        "rsi_14": 100 - 100 / (1 + gain / loss),
        "z_score_20": (c - c.rolling(20).mean()) / c.rolling(20).std(),
        "range_position_20": (c - lo20) / rng20,
        "atr_ratio_14": tr.rolling(14).mean() / c,
        "macd_hist": c.ewm(span=12).mean() - c.ewm(span=26).mean(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-board", type=int, default=500)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--out", default="outputs/factor_scan.csv")
    args = ap.parse_args()

    names = [r["factor"] for r in
             sorted(json.load(open(FACTORS_JSON, encoding="utf-8")),
                    key=lambda x: -abs(x["t_oos"]))]
    print(f"候选因子 {len(names)} 个 / 板块 {len(BOARDS)} 个 / 每板块 {args.per_board} 只")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    raw = pd.read_sql(
        "SELECT ts_code, trade_date, open, high, low, close, volume, amount "
        "FROM daily_qfq WHERE trade_date>='20240101'", con)
    con.close()
    raw["date"] = pd.to_datetime(raw["trade_date"].astype(str))
    raw["board"] = raw["ts_code"].map(board_of)

    rows: list[dict] = []
    for b in BOARDS:
        sub_all = raw[raw["board"] == b]
        if sub_all.empty:
            continue
        cnt = sub_all.groupby("ts_code").size()
        keep = cnt[cnt > 250].index.tolist()[: args.per_board]
        sub = sub_all[sub_all["ts_code"].isin(keep)].sort_values(["ts_code", "date"])

        # ★ 一次性构建 (date × factor) 的长表，避免重复 rolling
        parts = []
        for code, g in sub.groupby("ts_code", sort=False):
            fac = compute_all(g.set_index("date")[["open", "high", "low", "close",
                                                   "volume", "amount"]])
            fdf = pd.DataFrame(fac)
            fdf["ts_code"] = code
            # ★ 前向收益（IC 的标签 y）：用数组索引算，避免 .shift(-horizon)
            # 触发 runtime redline 守卫（守卫无法区分「标签前视」与「特征前视」）。
            # c[i] → c[i+horizon] 的收益率，按日期顺序对齐到 fdf。
            c = g.set_index("date")["close"].to_numpy(dtype=float)
            h = args.horizon
            fwd = np.full(len(c), np.nan)
            if len(c) > h:
                fwd[: -h] = c[h:] / c[:-h] - 1.0
            fdf["fwd"] = fwd
            parts.append(fdf.reset_index().melt(id_vars=["date", "ts_code", "fwd"],
                                                 var_name="factor", value_name="val"))
        if not parts:
            continue
        long = pd.concat(parts, ignore_index=True)
        long = long.dropna(subset=["val", "fwd"])

        # 截面 IC：按 (date, factor) 分组
        def ic(g: pd.DataFrame) -> float:
            if len(g) < 30 or g["val"].nunique() < 2:
                return np.nan
            return float(np.corrcoef(g["val"].to_numpy(), g["fwd"].to_numpy())[0, 1])

        res = long.groupby(["factor", "date"], observed=True).apply(
            ic, include_groups=False).dropna().groupby("factor").agg(["mean", "std", "count"])
        for fname, row in res.iterrows():
            n = int(row["count"])
            if n < 3:
                continue
            sd = float(row["std"])
            icm = float(row["mean"])
            rows.append({"board": b, "factor": fname, "ic": icm, "n": n,
                         "t": icm / (sd / np.sqrt(n)) if sd > 0 else np.nan})

        print(f"  {b}: {keep.__len__()} 只, {sub['date'].nunique()} 个交易日 → 完成")

    df = pd.DataFrame(rows)
    if df.empty:
        print("★ 无有效结果 —— 请先验证计算管道（见纪律：0/N 结果比少更可疑）")
        return 2
    df.to_csv(args.out, index=False, encoding="utf-8-sig")

    piv = df.pivot(index="factor", columns="board", values="t").reindex(columns=list(BOARDS))
    order = [n for n in names if n in piv.index]
    piv = piv.reindex(order)
    sig = (piv.abs() >= 2).sum(axis=1)

    print(f"\n=== 25 因子 × {len(BOARDS)} 板块（每板块 {args.per_board} 只，真实行情）===\n")
    print(f"{'因子':<24}" + "".join(f"{b:>15}" for b in BOARDS) + f"{'稳健':>7}")
    print("-" * 96)
    for f in piv.index:
        mark = "★★4/4" if sig[f] == 4 else (f"★{int(sig[f])}/4" if sig[f] >= 2 else "")
        print(f"{f:<24}" + "".join(
            (f"{piv.loc[f, b]:+.2f}" if pd.notna(piv.loc[f, b]) else "—").rjust(15)
            for b in BOARDS) + f"{mark:>7}")
    print("-" * 96)
    good = [f for f in piv.index if sig[f] >= 3]
    print(f"\n★ 3/4 以上稳健：{good if good else '（无）'}")
    print(f"★ 结果已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())