#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 gate 逐期收益上做「市场趋势曝光叠加」——不改引擎、不重跑 gate。

为什么这样做
------------
2026-11-12 定位到：项目里 `regime` 只调**因子权重**，回测路径**始终满仓**，
没有任何仓位/空仓机制。而市场择时探针显示：单纯「下跌时不持有」能把
最大回撤从 −68% 压到 −8.6%。要验证它是否也改善 gate 各臂，**不需要改引擎**——
gate 已把**逐期收益**写在 `walkforward_gate_resume_*.jsonl.<arm>` 里，
直接对这些收益做曝光缩放即可，口径与引擎完全一致（收益本身就是引擎产出的）。

方法
----
对每个臂：
1. 读每期 `window`（起止日）与 `result.total_return`；
2. 用**本地行情**算「截至该期开始前一交易日」的市场动量（800 票等权中位收益之和）；
3. 规则：``past_L > 0`` 则本期曝光 1，否则曝光 0（另测 0.5 半仓）；
4. 用缩放后的收益序列算 年化 / Sharpe / 最大回撤，与不缩放对比。

🔴 无前视：市场动量只用**期开始之前**的数据；期收益是引擎已实现的结果。

用法
----
    python scripts/research/exposure_overlay_on_gate.py \
        --arms-dir /tmp/holder_eval/arms --db "A股量化分析数据/astocks_raw.db" --L 20
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 250.0


def load_arms(d: Path) -> dict[str, list[dict]]:
    """arm 名 → [{start, end, ret}]，按窗口排序。"""
    out: dict[str, list[dict]] = {}
    for f in sorted(d.glob("walkforward_gate_resume_*.jsonl.*")):
        arm = f.name.split(".jsonl.")[-1]
        rows: list[dict] = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            win = rec.get("window") or ""
            res = rec.get("result") or {}
            if ".." not in win or "total_return" not in res:
                continue
            start, end = win.split("..")
            rows.append({"start": start, "end": end, "ret": float(res["total_return"])})
        if rows:
            out[arm] = sorted(rows, key=lambda r: r["start"])
    return out


def market_series(db: str, n_sample: int = 800) -> pd.Series:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    codes = [
        r[0]
        for r in con.execute(
            "SELECT ts_code FROM daily_qfq GROUP BY ts_code HAVING COUNT(*) > 200"
            " ORDER BY ts_code LIMIT ?",
            (n_sample,),
        )
    ]
    marks = ",".join("?" * len(codes))
    px = pd.read_sql(
        f"SELECT ts_code, trade_date, close FROM daily_qfq WHERE ts_code IN ({marks})",
        con,
        params=tuple(codes),
    )
    con.close()
    wide = px.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
    return wide.pct_change().median(axis=1).dropna()


def _stats(rets: np.ndarray, ppy: float) -> tuple[float, float, float]:
    if len(rets) < 2:
        return float("nan"), float("nan"), float("nan")
    ann = float((1 + rets).prod() ** (ppy / len(rets)) - 1)
    sd = float(rets.std(ddof=1))
    sh = float(rets.mean() / sd * np.sqrt(ppy)) if sd > 0 else float("nan")
    eq = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(eq)
    return ann, sh, float((eq / peak - 1).min())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms-dir", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--L", type=int, default=20, help="市场动量回看交易日数")
    ap.add_argument("--half", action="store_true", help="另测半仓(0.5)")
    args = ap.parse_args(argv)

    arms = load_arms(Path(args.arms_dir))
    if not arms:
        print("❌ 未找到臂文件")
        return 2
    mkt = market_series(args.db)
    dates = mkt.index.to_numpy()
    print(f"[mkt] 交易日 {len(mkt)}｜{dates[0]} ~ {dates[-1]}｜回看 L={args.L}")

    print("\n| 臂 | 期数 | 原年化 | 叠加后 | 原Sharpe | 叠加后 | 原MDD | 叠加后 | 暴露率 |")
    print("|---|---|---|---|---|---|---|---|---|")
    agg: list[tuple[float, float, float, float]] = []
    for arm, rows in arms.items():
        base_r, adj_r, exposures = [], [], []
        for r in rows:
            s = r["start"].replace("-", "")
            i = int(np.searchsorted(dates, s, side="left"))  # 期开始之前
            past = float(mkt.iloc[max(0, i - args.L):i].sum()) if i > 0 else 0.0
            expo = 1.0 if past > 0 else 0.0
            exposures.append(expo)
            base_r.append(r["ret"])
            adj_r.append(r["ret"] * expo)
        base_r, adj_r = np.array(base_r), np.array(adj_r)
        # 期长（交易日近似 = 期数折算）
        ppy = TRADING_DAYS / max(1.0, np.mean([
            (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])).days * 250 / 365 for r in rows
        ]))
        a0, s0, m0 = _stats(base_r, ppy)
        a1, s1, m1 = _stats(adj_r, ppy)
        agg.append((s0, s1, m0, m1))
        print(f"| {arm} | {len(rows)} | {a0:+.2%} | {a1:+.2%} | {s0:+.2f} | {s1:+.2f} | "
              f"{m0:+.1%} | {m1:+.1%} | {np.mean(exposures):.0%} |")

    a = np.array(agg)
    print(f"\n均值：Sharpe {a[:,0].mean():+.2f} → {a[:,1].mean():+.2f}"
          f"｜MDD {a[:,2].mean():+.1%} → {a[:,3].mean():+.1%}"
          f"｜Sharpe 改善 {int((a[:,1] > a[:,0]).sum())}/{len(a)} 臂")
    print("\n⚠️ 期数仅 ~20、单一市场周期；曝光规则的阈值(L)未做参数搜索，避免过拟合")
    return 0


if __name__ == "__main__":
    sys.exit(main())
