#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性全因子 IC 扫描（日线可得的一切），替代逐个试因子的低效做法。

## 数据口径（踩过的坑，勿删）
- runner 库 `daily_qfq.close_qfq` 与 `close` **逐值完全相同**（实测 same=1.0），
  **不是真复权**（真复权在 `aqsp/data/adjust.py:56` 用 PIT 因子现算）。
  直接用 close 算前向收益会把除权跳空当成暴跌：实测 933 日内 298 个 |日收益|>30%
  （288 个是 <-30%，最小 -96.4%）。故 `_clean_fwd()` 把跨越异常跳空的 fwd 置 NaN。
- `pct_change()` 在 pandas 2.3 默认 `fill_method='pad'`、3.0 已改默认 ⇒ **必须显式指定**，
  否则同一脚本跨版本结果不一致。
- 每 5 日一截面、horizon=20 ⇒ 标签窗口 4 倍重叠，IC 序列自相关使朴素 t 系统性虚高。
  故同时报 **Newey-West 修正 t**，FDR 一律基于 NW t。
- 面板约 30% 缺失（未上市/退市/停牌），由 `min_obs` 与 NaN 掩码处理，不做插值。

## 三个域（holder 教训：全市场 IC 好看 ≠ 作业面有效）
1. **全市场**：所有当日有价票；
2. **可交易域**：当日 amount 排名前 60%；
3. **生产池近似**：2f 反向（`mom_20` + `pv_corr_20` 各 0.5，取截面 top 10%）
   —— 近似 `min_total_score=0.70` 的通过率。**注意**：该池由这两个因子构造，
   故 `mom_20`/`pv_corr_20` 在池内属于自评，判读时剔除。

## 统计纪律
- 一次输出**全部**因子，不做事后筛选；
- 检验次数 = 因子数 × 域数 × horizon 数，同时给 **BH-FDR q**，只有 `q<=0.05` 才算候选；
- **37 个因子高度共线**（动量/波动/位置都是同一反转-低波风格的不同投影），
  「32 个显著」不等于「32 个独立 alpha」；报告末尾按因子簇归并。
- FDR 候选 ≠ alpha：仍须做 λ/换手敏感性，才允许进 gate。

用法：
    python scripts/research/factor_sweep.py --db <path> --start 20230101 \
        --horizons 10,20 --out /tmp/factor_sweep_result.csv
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys

import numpy as np
import pandas as pd

MIN_OBS = 200
MIN_SECTIONS = 5
JUMP_LIMIT = 0.22  # 超过涨跌停上限(20%)+缓冲 ⇒ 判为除权/异常，不是真实收益
POOL_PCT = 0.10
SELF_EVAL = {"mom_20", "pv_corr_20"}

CLUSTERS = {
    "动量/反转": ["mom_5", "mom_10", "mom_20", "mom_60", "mom_120", "mom_250",
                "rev_1", "rev_3", "mom_quality_20"],
    "波动": ["vol_20", "vol_60", "atr_20", "atr_60", "downvol_60", "idio_vol_60"],
    "量能": ["vratio_5_20", "vratio_20_60", "logvol_20", "amtratio_5_20",
           "turnover_proxy_20", "amihud_20"],
    "位置": ["ma_dist_20", "ma_dist_60", "ma_dist_120", "dist_high_250", "dist_low_250"],
    "形态": ["amp_20", "gap_20", "up_ratio_20", "up_ratio_60", "skew_60",
           "kurt_60", "max_ret_20", "min_ret_20"],
    "微观结构": ["beta_60", "pv_corr_20", "pv_corr_60"],
}


def _pct(df: pd.DataFrame) -> pd.DataFrame:
    """跨 pandas 版本一致的 pct_change（禁用隐式 pad 填充）。"""
    try:
        return df.pct_change(fill_method=None)
    except TypeError:  # pragma: no cover - pandas 未来版本可能移除该参数
        return df.pct_change()


def _panel(db: str, start: str) -> dict[str, pd.DataFrame]:
    """读全市场日线；以 close 为基准把所有字段对齐到同一 index/columns。

    pivot_table 默认 dropna=True：某字段整列缺失（例如科创板回补后 amount 全 NULL）
    会让该面板少一列，后续与 fwd 做 `&` 时直接 shape mismatch。故统一 reindex。
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        df = pd.read_sql(
            "SELECT ts_code, trade_date, open, high, low, close, volume, amount"
            " FROM daily_qfq WHERE trade_date >= ?",
            con,
            params=(start,),
        )
    finally:
        con.close()

    close = df.pivot_table(
        index="trade_date", columns="ts_code", values="close"
    ).sort_index()
    idx, cols = close.index, close.columns
    out: dict[str, pd.DataFrame] = {"close": close}
    for col in ("open", "high", "low", "volume", "amount"):
        out[col] = (
            df.pivot_table(index="trade_date", columns="ts_code", values=col)
            .sort_index()
            .reindex(index=idx, columns=cols)
        )

    shapes = {k: v.shape for k, v in out.items()}
    if len(set(shapes.values())) != 1:
        raise RuntimeError(f"panel shape mismatch: {shapes}")
    return out


def _rolling_corr(x: pd.DataFrame, y: pd.DataFrame, w: int, mp: int) -> pd.DataFrame:
    """逐列滚动 Pearson 相关，纯 DataFrame 运算（不用 rolling.corr，避免 pairwise 歧义）。"""
    mx = x.rolling(w, min_periods=mp).mean()
    my = y.rolling(w, min_periods=mp).mean()
    mxy = x.mul(y).rolling(w, min_periods=mp).mean()
    cov = mxy - mx.mul(my)
    sx = x.rolling(w, min_periods=mp).std()
    sy = y.rolling(w, min_periods=mp).std()
    return cov / (sx * sy).replace(0, np.nan)


def _factors(p: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """全部因子（index=date, columns=symbol）。"""
    close, open_, high, low = p["close"], p["open"], p["high"], p["low"]
    vol, amt = p["volume"], p["amount"]
    ret = _pct(close)
    dv = _pct(vol)
    mkt = ret.median(axis=1)
    f: dict[str, pd.DataFrame] = {}

    # --- 动量 / 反转 ---
    for w in (5, 10, 20, 60, 120, 250):
        f[f"mom_{w}"] = close / close.shift(w) - 1
    f["rev_1"] = -ret
    f["rev_3"] = -(close / close.shift(3) - 1)

    # --- 波动 ---
    for w in (20, 60):
        f[f"vol_{w}"] = ret.rolling(w, min_periods=w // 2).std()
        f[f"atr_{w}"] = (high - low).rolling(w, min_periods=w // 2).mean() / close
    f["downvol_60"] = ret.where(ret < 0).rolling(60, min_periods=20).std()

    # --- 量能 ---
    f["vratio_5_20"] = vol.rolling(5).mean() / vol.rolling(20).mean()
    f["vratio_20_60"] = vol.rolling(20).mean() / vol.rolling(60).mean()
    f["logvol_20"] = np.log1p(vol.rolling(20).mean())
    f["amtratio_5_20"] = amt.rolling(5).mean() / amt.rolling(20).mean()

    # --- 位置 ---
    for w in (20, 60, 120):
        f[f"ma_dist_{w}"] = close / close.rolling(w, min_periods=w // 2).mean() - 1
    f["dist_high_250"] = close / close.rolling(250, min_periods=200).max() - 1
    f["dist_low_250"] = close / close.rolling(250, min_periods=200).min() - 1

    # --- 形态 ---
    f["amp_20"] = ((high - low) / close).rolling(20, min_periods=10).mean()
    f["gap_20"] = (open_ / close.shift(1) - 1).rolling(20, min_periods=10).mean()
    f["up_ratio_20"] = (ret > 0).rolling(20, min_periods=10).mean()
    f["up_ratio_60"] = (ret > 0).rolling(60, min_periods=40).mean()
    f["skew_60"] = ret.rolling(60, min_periods=40).skew()
    f["kurt_60"] = ret.rolling(60, min_periods=40).kurt()
    f["max_ret_20"] = ret.rolling(20, min_periods=10).max()
    f["min_ret_20"] = ret.rolling(20, min_periods=10).min()

    # --- 流动性 / 微观结构 ---
    f["amihud_20"] = (
        (ret.abs() / amt.replace(0, np.nan)).rolling(20, min_periods=10).mean() * 1e9
    )
    f["turnover_proxy_20"] = vol.rolling(20, min_periods=10).mean()
    f["idio_vol_60"] = ret.sub(mkt, axis=0).rolling(60, min_periods=40).std()

    w, mp = 60, 40
    rm = ret.rolling(w, min_periods=mp).mean()
    mm = mkt.rolling(w, min_periods=mp).mean()
    prod = ret.mul(mkt, axis=0).rolling(w, min_periods=mp).mean()
    cov = prod.sub(rm.mul(mm, axis=0))
    var = (mkt * mkt).rolling(w, min_periods=mp).mean() - mm * mm
    f["beta_60"] = cov.div(var.replace(0, np.nan), axis=0)

    f["mom_quality_20"] = (
        (close / close.shift(20) - 1)
        / (ret.rolling(20, min_periods=10).std() * np.sqrt(20))
    )

    # --- 生产基线 2f 对照 ---
    f["pv_corr_20"] = _rolling_corr(ret, dv, 20, 15)
    f["pv_corr_60"] = _rolling_corr(ret, dv, 60, 40)

    for name in list(f):
        f[name] = f[name].reindex(index=close.index, columns=close.columns)
    shapes = {k: v.shape for k, v in f.items()}
    if len(set(shapes.values())) != 1:
        raise RuntimeError(f"factor shape mismatch: {shapes}")
    return f


def _clean_fwd(close: pd.DataFrame, h: int) -> tuple[np.ndarray, int]:
    """前向收益；把跨越除权/异常跳空的窗口置 NaN（库里无真复权列）。"""
    cv = close.to_numpy(dtype=float)
    ret = np.full_like(cv, np.nan, dtype=float)
    ret[1:] = cv[1:] / cv[:-1] - 1
    bad = np.zeros(cv.shape, dtype=np.int8)
    bad[np.isfinite(ret) & (np.abs(ret) > JUMP_LIMIT)] = 1
    cs = np.cumsum(bad, axis=0)
    fwd = np.full_like(cv, np.nan, dtype=float)
    fwd[:-h] = cv[h:] / cv[:-h] - 1  # 位置索引，非 shift(-N)
    span = np.zeros(cv.shape, dtype=np.int32)
    span[:-h] = cs[h:] - cs[:-h]  # (t, t+h] 区间内的异常跳空数
    n_cleaned = int((span > 0).sum())
    fwd[span > 0] = np.nan
    return fwd, n_cleaned


def _pool_mask(
    facs: dict[str, pd.DataFrame], snap: list[int], shape: tuple, min_obs: int
) -> np.ndarray:
    """生产池近似：2f 反向综合分取每截面 top POOL_PCT。

    池严格取 top POOL_PCT；若样本太少导致池小于 min_obs，宁可跳过该截面，
    不用 max(min_obs, ...) 把池悄悄放大成「大半个市场」（那会破坏作业面语义）。
    """
    mask = np.zeros(shape, dtype=bool)
    a = np.asarray(facs["mom_20"].values, dtype=float)
    b = np.asarray(facs["pv_corr_20"].values, dtype=float)
    for t in snap:
        x, y = a[t], b[t]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < min_obs:
            continue
        rx = pd.Series(x).rank(pct=True).to_numpy()
        ry = pd.Series(y).rank(pct=True).to_numpy()
        score = 0.5 * ((1.0 - rx) + (1.0 - ry))  # 反向：动量低、价量相关低者得高分
        vals = np.where(ok, score, -np.inf)
        finite = vals[np.isfinite(vals)]
        k = int(len(finite) * POOL_PCT)
        if k < min_obs or len(finite) < k:
            continue
        thr = float(np.partition(finite, -k)[-k])
        mask[t] = vals >= thr
    return mask


def _nw_t(ics: list[float], lag: int) -> float:
    """Newey-West 修正的 t（重叠标签 ⇒ IC 序列自相关）。"""
    arr = np.asarray(ics, dtype=float)
    n = len(arr)
    m = float(arr.mean())
    x = arr - m
    var = float(np.dot(x, x) / n)
    for lag_i in range(1, min(lag, n - 1) + 1):
        gamma = float(np.dot(x[lag_i:], x[:-lag_i]) / n)
        var += 2.0 * (1.0 - lag_i / (lag + 1)) * gamma
    se = math.sqrt(max(var, 1e-18) / n)
    return m / se if se > 0 else float("nan")


def _ic_table(
    fac: pd.DataFrame,
    fwd: np.ndarray,
    snap_idx: list[int],
    mask: np.ndarray | None,
    min_obs: int,
    nw_lag: int,
) -> dict:
    ics: list[float] = []
    values = np.asarray(fac.values, dtype=float)
    for t in snap_idx:
        col, fut = values[t], fwd[t]
        ok = np.isfinite(col) & np.isfinite(fut)
        if mask is not None:
            ok &= mask[t]
        if ok.sum() < min_obs:
            continue
        a = pd.Series(col[ok]).rank().to_numpy()
        b = pd.Series(fut[ok]).rank().to_numpy()
        if a.std() == 0 or b.std() == 0:
            continue
        ics.append(float(np.corrcoef(a, b)[0, 1]))
    if len(ics) < MIN_SECTIONS:
        return {}
    arr = np.asarray(ics)
    m, sd = float(arr.mean()), float(arr.std(ddof=1))
    t_naive = m / (sd / np.sqrt(len(arr))) if sd > 0 else float("nan")
    return {
        "ic": m,
        "icir": m / sd if sd > 0 else float("nan"),
        "t": t_naive,
        "t_nw": _nw_t(ics, nw_lag),
        "pos": float((arr > 0).mean()),
        "n": len(arr),
    }


def _bh_q(pvals: list[float]) -> list[float]:
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [1.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, pvals[i] * m / rank)
        q[i] = prev
    return q


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--horizons", default="10,20")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--min-obs", type=int, default=MIN_OBS)
    ap.add_argument("--nw-lag", type=int, default=0, help="0=按 horizon/step 自动")
    ap.add_argument("--out", default="/tmp/factor_sweep_result.csv")
    args = ap.parse_args(argv)

    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    if not horizons or any(h <= 0 for h in horizons):
        raise SystemExit("--horizons 必须是正整数列表")

    print("[load] 读面板…", flush=True)
    p = _panel(args.db, args.start)
    close = p["close"]
    dates = close.index.to_numpy()
    amt_rank = p["amount"].rank(axis=1, pct=True).to_numpy()
    tradable = amt_rank >= 0.4

    print("[factor] 计算因子矩阵…", flush=True)
    facs = _factors(p)
    cv = close.to_numpy(dtype=float)
    print(
        f"[plan] 因子 {len(facs)}｜日期 {len(dates)} ({dates[0]}~{dates[-1]})"
        f"｜样本 {cv.shape[1]} 票｜horizons={horizons}｜min_obs={args.min_obs}"
        f"｜跳空阈值 {JUMP_LIMIT:.0%}",
        flush=True,
    )

    records: list[dict] = []
    for h in horizons:
        fwd, n_cleaned = _clean_fwd(close, h)
        snap = list(range(260, len(dates) - h, args.step))
        if len(snap) < MIN_SECTIONS:
            raise RuntimeError(f"horizon={h} 截面不足：{len(snap)}")
        lag = args.nw_lag or max(1, math.ceil(h / args.step))
        pool = _pool_mask(facs, snap, cv.shape, args.min_obs)
        counts = {
            "全市场": int(np.median([np.isfinite(fwd[t]).sum() for t in snap])),
            "可交易域": int(np.median([(tradable[t] & np.isfinite(fwd[t])).sum() for t in snap])),
            "生产池近似": int(np.median([pool[t].sum() for t in snap])),
        }
        print(
            f"[h={h}] 截面 {len(snap)}｜NW lag={lag}｜清洗掉跨跳空 fwd {n_cleaned}"
            f"｜每截面中位票数 {counts}",
            flush=True,
        )
        for domain, mask in (
            ("全市场", None),
            ("可交易域", tradable),
            ("生产池近似", pool),
        ):
            for name, fac in facs.items():
                st = _ic_table(fac, fwd, snap, mask, args.min_obs, lag)
                records.append(
                    {
                        "horizon": h,
                        "domain": domain,
                        "factor": name,
                        "ic": st.get("ic"),
                        "icir": st.get("icir"),
                        "t": st.get("t"),
                        "t_nw": st.get("t_nw"),
                        "pos": st.get("pos"),
                        "n": st.get("n", 0),
                        "valid": bool(st),
                        "self_eval": domain == "生产池近似" and name in SELF_EVAL,
                    }
                )
            print(f"[done] horizon={h} domain={domain}", flush=True)

    # BH-FDR 一律基于 NW t；池内自评因子不参与候选判定
    valid_idx = [
        i for i, r in enumerate(records)
        if r["valid"] and math.isfinite(r["t_nw"]) and not r["self_eval"]
    ]
    pvals = [math.erfc(abs(records[i]["t_nw"]) / math.sqrt(2)) for i in valid_idx]
    qs = _bh_q(pvals)
    for j, i in enumerate(valid_idx):
        records[i]["p"] = pvals[j]
        records[i]["q"] = qs[j]
    for r in records:
        r.setdefault("p", None)
        r.setdefault("q", None)

    n_tests = len(valid_idx)
    invalid = [f"{r['domain']}/h{r['horizon']}/{r['factor']}" for r in records if not r["valid"]]
    print(
        f"\n[stats] 有效检验 {n_tests} 次（已剔除池内自评）｜朴素 t 因窗口重叠虚高，"
        "判候选一律看 NW-t + BH-FDR q≤0.05",
        flush=True,
    )
    if invalid:
        print(f"[stats] 无效因子：{len(invalid)} 个 ⇒ 前 12：{invalid[:12]}", flush=True)

    for h in horizons:
        for domain in ("全市场", "可交易域", "生产池近似"):
            rows = [r for r in records if r["horizon"] == h and r["domain"] == domain]
            rows.sort(key=lambda r: -(abs(r["ic"]) if r["valid"] else -1))
            print(f"\n### {domain} · horizon={h}（全部 {len(rows)} 个因子，按 |IC| 排序）")
            print("| 因子 | 平均IC | ICIR | 朴素t | NW-t | 正IC占比 | 截面 | BH-q | FDR过 |")
            print("|---|---|---|---|---|---|---|---|---|")
            for r in rows:
                if not r["valid"]:
                    print(f"| {r['factor']} | 无效 | — | — | — | — | {r['n']} | — | — |")
                    continue
                if r["self_eval"]:
                    mark = "自评剔除"
                elif r["q"] is not None and r["q"] <= 0.05:
                    mark = "✅"
                else:
                    mark = ""
                print(
                    f"| {r['factor']} | {r['ic']:+.4f} | {r['icir']:+.3f} | "
                    f"{r['t']:+.2f} | {r['t_nw']:+.2f} | {r['pos']:.1%} | {r['n']} | "
                    f"{r['q'] if r['q'] is not None else '—'} | {mark} |"
                )
            sig = [
                r["factor"] for r in rows
                if r["valid"] and not r["self_eval"] and r["q"] is not None and r["q"] <= 0.05
            ]
            naive = [r["factor"] for r in rows if r["valid"] and abs(r["t"]) >= 2.0]
            print(f"\n**朴素 |t|≥2：{len(naive)} 个｜NW-t + FDR q≤0.05 真候选：{len(sig)} 个**")
            print(f"候选 ⇒ {sig}")

    # 因子簇归并：避免把共线因子当成多个独立发现
    print("\n### 因子簇（共线归并，取簇内 |IC| 最大者为代表）")
    print("| 域 | horizon | 簇 | 代表因子 | 代表IC | 簇内平均IC | 簇数 |")
    print("|---|---|---|---|---|---|---|")
    for h in horizons:
        for domain in ("全市场", "可交易域", "生产池近似"):
            sub = [r for r in records if r["horizon"] == h and r["domain"] == domain and r["valid"]]
            for cluster, names in CLUSTERS.items():
                members = [r for r in sub if r["factor"] in names]
                if not members:
                    continue
                rep = max(members, key=lambda r: abs(r["ic"]))
                avg = float(np.mean([r["ic"] for r in members]))
                print(
                    f"| {domain} | {h} | {cluster} | {rep['factor']} | "
                    f"{rep['ic']:+.4f} | {avg:+.4f} | {len(members)} |"
                )

    frame = pd.DataFrame.from_records(records)
    frame.to_csv(args.out, index=False)
    print(f"\n[out] 全量结果已写 {args.out}（{len(frame)} 行）", flush=True)
    print(
        "[next] FDR 候选 ≠ alpha：仍须做 λ/换手敏感性、生产池内真实选股口径复核，"
        "才允许进 gate。直接进 gate = 重复 holder 的坑。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
