#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate 各臂的**超额收益**判读：策略 vs 等权基准，逐期对齐。

## 为什么必须有这个
`gate` 只报绝对收益。2026-11-12 实测基线 3 年 `−13.89%`，看着"亏 14%"，
但同期同池等权 `+45.40%` ⇒ **超额 ≈ −59pp**。不叠这一层，会把
"跑输大盘 59 个点"误读成"只是小亏"——这是本项目反复误判的根源。

## 口径
- 策略侧：直接读 gate 产物 `walkforward_gate_resume_*.jsonl.<arm>` 的
  `result.total_return`（引擎自己算的，不重算，避免口径漂移）；
- 基准侧：**全市场等权**——每期窗口内，所有在库且有连续价格的票等权持有，
  用「窗口内收盘价序列的中位日收益」累乘（中位数抗极值）；
- 超额 = 策略该期收益 − 基准该期收益；对逐期超额做 t 检验（配对）。

⚠️ **局限（必须在结论里写明）**：这里用**全市场等权**做基准，不是"同池等权"。
同池等权需要按 composite 逐期重建 2f 达标池（约 10 分钟/次），本工具不做。
全市场等权会把小盘股整体涨跌算进来，**若策略池偏小盘，超额会被高估或低估**。

用法
----
    python scripts/research/gate_excess_report.py \
        --arms-dir /tmp/holder_eval/arms --db "A股量化分析数据/astocks_raw.db"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def run_key(name: str) -> str:
    """从文件名取出 run 签名 ``<start>_<end>``。

    🔴 必须按 run 分组：不同窗口的臂**不可比**。2026-11-12 实测踩过——
    同一目录里放了 `2023-09-05_2026-09-18`（WF-*）与 `2023-11-07_2026-11-06`（WO-*）
    两个 run，混排进一张表后看起来像"16 个臂"，实际是两批不同窗口的结果。
    """
    body = name.split("walkforward_gate_resume_")[-1]
    return body.split(".jsonl.")[0]


def load_arms(d: Path) -> dict[str, dict[str, list[dict]]]:
    """按 run 签名分组：``{"2023-11-07_2026-11-06": {arm: rows}}``。"""
    out: dict[str, dict[str, list[dict]]] = {}
    for f in sorted(d.glob("walkforward_gate_resume_*.jsonl.*")):
        arm = f.name.split(".jsonl.")[-1]
        key = run_key(f.name)
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
            out.setdefault(key, {})[arm] = sorted(rows, key=lambda r: r["start"])
    return out


def market_curve(db: str, n_sample: int = 1200) -> pd.Series:
    """全市场等权净值曲线（index=YYYYMMDD，value=累计净值）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
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
    finally:
        con.close()
    wide = px.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
    # 🔴 等权**买入持有**：每票按首价归一再取均值。
    # 曾写成 `(1 + pct_change().median(axis=1)).cumprod()` —— 错的：
    # 截面中位日收益系统性低于组合增长率，长期复利放大误差
    # （实测同窗口：买入持有 +2.30%、中位票 −9.07%、median 累乘 −71.89%）。
    filled = wide.ffill()
    base = filled.apply(lambda col: col.dropna().iloc[0] if col.notna().any() else float("nan"))
    return filled.divide(base, axis=1).mean(axis=1, skipna=True).dropna()


def _report_run(run: str, arm_map: dict[str, list[dict]], curve: pd.Series, idx) -> None:
    """打印单个 run 的超额表。不同 run 的臂**绝不混排**。"""
    print(f"\n### run {run.split('_')[0]} ~ {run.split('_')[-1]}（{len(arm_map)} 臂）")
    print("| 臂 | 期数 | 策略年化 | 基准年化 | **超额年化** | 逐期超额均值 | t(超额) | 跑赢期数 |")
    print("|---|---|---|---|---|---|---|---|")
    summary: list[tuple[float, float, float]] = []
    for arm, rows in sorted(arm_map.items()):
        s_ret, b_ret = [], []
        for r in rows:
            s = r["start"].replace("-", "")
            e = r["end"].replace("-", "")
            i0 = int(np.searchsorted(idx, s, side="left"))
            i1 = int(np.searchsorted(idx, e, side="right"))
            if i0 >= i1 or i1 > len(curve) or i0 == 0:
                continue
            bench = float(curve.iloc[i1 - 1] / curve.iloc[i0 - 1] - 1)
            if not np.isfinite(bench):
                continue
            s_ret.append(r["ret"])
            b_ret.append(bench)
        if len(s_ret) < 3:
            continue
        s_arr, b_arr = np.array(s_ret), np.array(b_ret)
        diff = s_arr - b_arr
        n = len(s_arr)
        ppy = 250.0 / max(1.0, float(np.mean([
            (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])).days * 250 / 365 for r in rows
        ])))

        def _ann(x: np.ndarray) -> float:
            return float((1 + x).prod() ** (ppy / len(x)) - 1)

        m = float(diff.mean())
        sd = float(diff.std(ddof=1))
        t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
        summary.append((_ann(s_arr), _ann(b_arr), _ann(diff)))
        print(f"| {arm} | {n} | {_ann(s_arr):+.2%} | {_ann(b_arr):+.2%} | "
              f"**{_ann(diff):+.2%}** | {m:+.2%} | {t:+.2f} | {int((diff > 0).sum())}/{n} |")
    if summary:
        ex = np.array([x[2] for x in summary])
        print(f"  小结：策略 {np.mean([x[0] for x in summary]):+.2%}"
              f"｜基准 {np.mean([x[1] for x in summary]):+.2%}"
              f"｜**超额 {ex.mean():+.2%}**｜正超额臂 {int((ex > 0).sum())}/{len(ex)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms-dir", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--sample", type=int, default=1200)
    args = ap.parse_args(argv)

    arms = load_arms(Path(args.arms_dir))
    if not arms:
        print("❌ 未找到臂文件")
        return 2
    if len(arms) > 1:
        print(f"⚠️ 检测到 {len(arms)} 个不同 run，将**分别出表**（不同窗口的臂不可比）：")
        for k, v in arms.items():
            print(f"   - {k}: {len(v)} 臂")
    curve = market_curve(args.db, args.sample)
    idx = curve.index.to_numpy()
    print(f"\n[bench] 全市场等权（{args.sample} 票）｜{idx[0]} ~ {idx[-1]}")
    for run, arm_map in arms.items():
        _report_run(run, arm_map, curve, idx)
    print("\n⚠️ 基准是**全市场等权**，不是同池等权；若策略池偏小盘，超额会被系统性高估/低估。\n"
          "   同池等权需按 composite 逐期重建 2f 池（约 10 分钟/次），本工具不覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
