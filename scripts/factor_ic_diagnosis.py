#!/usr/bin/env python3
"""factor_ic_diagnosis.py — 选股因子有效性诊断（IC 检验，item 2 失败归因用）

背景：gate 主变体在 3 年窗口跑出 −17.64%，且在"全池 +11.20%、仅 12.72% 个股下跌"的
普涨窗口里 8 个变体全亏。这无法用"市场不好"解释，需要判断是**因子本身失效**还是
**参数没调对**——二者的处置完全不同，本脚本就是做这个区分的。

做什么：
  - 在与 gate 完全相同的样本（同一库、同一标的池、同一窗口）上，逐横截面计算
    momentum / triple_rise / composite 三个打分与"未来 horizon 日收益"的 Spearman IC。
  - 输出 IC 均值、IC 标准差、ICIR、t 值、IC 为正的比例，以及逐截面 IC 明细。

判读口径（业界通用）：
  - |IC 均值| < 0.02 且 t 不显著 → 因子基本无预测力，调参只是在拟合噪音。
  - IC 均值为负且显著 → 因子在该窗口**反向**有效，越按它选越亏（最危险）。
  - ICIR（= IC 均值 / IC 标准差）< 0.3 → 即使有 IC 也不稳定。

🔴 合规声明（重要）：
  本脚本**只做因子诊断，不产出任何交易信号，不进入回测/选股/上线路径**。
  IC 检验天然需要"未来 N 日收益"作为被解释变量，这与回测信号生成里的 look-ahead
  违规是两回事（后者由 AGENTS.md §3.6 红线禁止，本脚本不涉及）。

用法（runner 上，与证据脚本同环境）：
  python3 scripts/factor_ic_diagnosis.py \
      --db /opt/aqsp-runner/data/astocks_raw.db \
      --start 2023-09-09 --end 2026-09-08 \
      --symbols-file /opt/aqsp-runner/gate_run/evidence_symbols.txt \
      --lookback 60 --horizon 3 --step 10 \
      --output /opt/aqsp-runner/gate_run/factor_ic_report.md
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

from aqsp.cli import (
    WalkForwardGridVariant,
    _apply_walkforward_grid_variant,
    load_thresholds,
)
from aqsp.strategies.composite import CompositeStrategy

# 与 gate 主变体 WF-001 完全一致（mom=0.3 / tr=0.3 / lookback=60 / horizon=3 / top_n=10）
WF001 = WalkForwardGridVariant("WF-001", 0.3, 0.3, 60, 3, 10, "momentum")


def _optin_prefiltered_universe() -> None:
    """与证据脚本一致：复用 gate 已筛选的标的池，须成对 opt-in。"""
    os.environ.setdefault("PREFILTERED_SYMBOLS", "1")
    os.environ.setdefault("AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS", "1")


def _norm_symbol(ts_code: str) -> str:
    """db 里 ts_code 带后缀（000001.SZ），内部一律用纯 6 位。"""
    return str(ts_code).split(".")[0]


def load_prices(
    db: str, start: str, end: str, symbols: list[str] | None
) -> pd.DataFrame:
    """读日线，返回列：symbol / trade_date / open / high / low / close / volume。"""
    import sqlite3

    s = str(start).replace("-", "")
    e = str(end).replace("-", "")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        sql = (
            "SELECT ts_code, trade_date, open_qfq, high_qfq, low_qfq, close_qfq, volume "
            "FROM daily_qfq WHERE trade_date BETWEEN ? AND ?"
        )
        df = pd.read_sql_query(sql, con, params=(s, e))
    finally:
        con.close()

    df.columns = ["ts_code", "trade_date", "open", "high", "low", "close", "volume"]
    df["symbol"] = df["ts_code"].map(_norm_symbol)
    if symbols is not None:
        keep = set(symbols)
        df = df[df["symbol"].isin(keep)]
    df = df.drop(columns=["ts_code"])
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def _ic(scores: pd.Series, fwd: pd.Series) -> float:
    """Spearman IC（用 rank + pearson 实现，避免依赖 scipy）。"""
    joined = pd.concat([scores.rename("s"), fwd.rename("f")], axis=1).dropna()
    if len(joined) < 30:
        return float("nan")
    return float(joined["s"].rank().corr(joined["f"].rank()))


def main() -> int:
    _optin_prefiltered_universe()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument(
        "--symbols-file", default=None, help="gate 的标的池（每行一个纯 6 位代码）"
    )
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument(
        "--horizon", type=int, default=3, help="未来收益天数，与 WF-001 的 horizon 对齐"
    )
    ap.add_argument("--step", type=int, default=10, help="每隔多少个交易日取一个横截面")
    ap.add_argument(
        "--max-symbols", type=int, default=0, help=">0 时只取前 N 只（快速试跑用）"
    )
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    symbols: list[str] | None = None
    if args.symbols_file:
        symbols = [
            ln.strip()
            for ln in Path(args.symbols_file).read_text().splitlines()
            if ln.strip()
        ]
        if args.max_symbols:
            symbols = symbols[: args.max_symbols]
    print(f"[info] 标的池: {len(symbols) if symbols else '全部'} 只")

    raw = load_prices(args.db, args.start, args.end, symbols)
    print(f"[info] 读入日线 {len(raw):,} 行 / {raw['symbol'].nunique():,} 只")

    # 收盘价宽表（行=日期，列=标的），用于算未来收益
    close = raw.pivot_table(
        index="trade_date", columns="symbol", values="close", aggfunc="last"
    )
    close = close.sort_index()
    fwd_ret = close.shift(-args.horizon) / close - 1.0
    print(f"[info] 宽表: {close.shape[0]} 个交易日 × {close.shape[1]} 只")

    # 每只股票一个 DataFrame（按日期升序），供策略打分。
    # 注意：策略内部按 "date" **列** 排序（momentum.py: sort_values("date")），
    # 因此这里必须保留 date 列而不是拿它当索引。
    by_symbol: dict[str, pd.DataFrame] = {}
    for sym, g in raw.sort_values("trade_date").groupby("symbol", sort=False):
        d = g[["trade_date", "open", "high", "low", "close", "volume"]].rename(
            columns={"trade_date": "date"}
        )
        if len(d) >= args.lookback + 5:
            by_symbol[str(sym)] = d.reset_index(drop=True)
    print(f"[info] 可用于打分的标的: {len(by_symbol):,} 只")

    thresholds = load_thresholds()
    variant_thresholds = _apply_walkforward_grid_variant(thresholds, WF001)
    strategy = CompositeStrategy(thresholds=variant_thresholds)
    has_tr = strategy._has_tr()  # noqa: SLF001 - 诊断脚本读取内部开关以决定输出列

    dates = list(close.index[args.lookback :: args.step])
    print(f"[info] 横截面: {len(dates)} 个（每 {args.step} 个交易日）")

    rows: list[dict] = []
    for i, d in enumerate(dates, 1):
        # by_symbol 的 DataFrame 用 "date" **列**（不是索引），
        # 因此按列过滤到截面日 d，再取尾部窗口。
        data = {
            sym: df[df["date"] <= d].tail(args.lookback + 10)
            for sym, df in by_symbol.items()
            if (df["date"] <= d).any() and df["date"].iloc[-1] >= d
        }
        if len(data) < 50:
            continue
        mom_s = pd.Series(strategy.momentum_strategy.calculate_score(data), dtype=float)
        tr_s = (
            pd.Series(strategy.triple_rise_strategy.calculate_score(data), dtype=float)
            if has_tr
            else pd.Series(dtype=float)
        )
        comp_s = pd.Series(strategy.calculate_score(data), dtype=float)
        fwd = fwd_ret.loc[d].dropna()

        row = {
            "date": d,
            "n": int(len(fwd)),
            "ic_mom": _ic(mom_s, fwd),
            "ic_tr": _ic(tr_s, fwd) if has_tr else float("nan"),
            "ic_comp": _ic(comp_s, fwd),
        }
        rows.append(row)
        if i % 5 == 0 or i == len(dates):
            print(
                f"[ic {i}/{len(dates)}] {d} n={row['n']} "
                f"mom={row['ic_mom']:+.4f} tr={row['ic_tr']:+.4f} comp={row['ic_comp']:+.4f}",
                flush=True,
            )

    _write_report(args, rows)
    print(f"[ok] 报告已写出: {args.output}")
    return 0


def _stats(vals: list[float]) -> dict[str, float]:
    arr = np.array([v for v in vals if v == v], dtype=float)  # 去 NaN
    if len(arr) < 2:
        return {
            "mean": float("nan"),
            "std": float("nan"),
            "icir": float("nan"),
            "t": float("nan"),
            "pos_rate": float("nan"),
            "n": len(arr),
        }
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    return {
        "mean": mean,
        "std": std,
        "icir": mean / std if std > 0 else float("nan"),
        "t": mean / std * np.sqrt(len(arr)) if std > 0 else float("nan"),
        "pos_rate": float((arr > 0).mean()),
        "n": len(arr),
    }


def _fmt(x: float, nd: int = 4) -> str:
    return "N/A" if x != x else f"{x:.{nd}f}"


def _write_report(args: argparse.Namespace, rows: list[dict]) -> None:
    mom = _stats([r["ic_mom"] for r in rows])
    tr = _stats([r["ic_tr"] for r in rows])
    comp = _stats([r["ic_comp"] for r in rows])

    lines: list[str] = []
    lines.append("# 选股因子 IC 诊断（gate 失败归因）\n")
    lines.append(
        f"- 样本区间: `{args.start}` ~ `{args.end}`（与 gate 同一库、同一标的池）"
    )
    lines.append(
        f"- 口径: WF-001（mom=0.3 / tr=0.3 / lookback={args.lookback} / horizon={args.horizon} / top_n=10）"
    )
    lines.append(f"- 横截面: 每 {args.step} 个交易日一个，共 {len(rows)} 个")
    lines.append(f"- IC 定义: 打分与未来 {args.horizon} 日收益的 Spearman 相关系数\n")

    lines.append("## 汇总\n")
    lines.append("| 因子 | IC 均值 | IC 标准差 | ICIR | t 值 | IC>0 占比 | 截面数 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, s in (("momentum", mom), ("triple_rise", tr), ("composite(加权)", comp)):
        lines.append(
            f"| {name} | {_fmt(s['mean'])} | {_fmt(s['std'])} | {_fmt(s['icir'], 3)} | "
            f"{_fmt(s['t'], 2)} | {_fmt(s['pos_rate'], 2)} | {s['n']} |"
        )

    lines.append("\n## 判读\n")
    lines.append(
        "- `|IC 均值| < 0.02` 且 t 不显著 → 因子基本无预测力，调参只是拟合噪音。"
    )
    lines.append(
        "- `IC 均值为负` 且显著 → 因子在该窗口**反向**有效，越按它选越亏（最危险）。"
    )
    lines.append("- `ICIR < 0.3` → 即便有 IC 也不稳定。")
    lines.append("- ⚠️ 本脚本仅做诊断，**不产出交易信号、不进入回测/上线路径**；")
    lines.append(
        "  IC 检验以未来收益为被解释变量，与回测信号生成中的 look-ahead 违规无关。\n"
    )

    lines.append("## 逐截面 IC\n")
    lines.append("| # | 日期 | 样本数 | momentum | triple_rise | composite |")
    lines.append("|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['date']} | {r['n']} | {_fmt(r['ic_mom'])} | "
            f"{_fmt(r['ic_tr'])} | {_fmt(r['ic_comp'])} |"
        )

    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
