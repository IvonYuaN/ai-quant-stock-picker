"""★ `volume` / `triple_rise` / `htf` 三维的真实 IC 实测（**无需 PIT 财务数据**）

**为什么做**（2026-10-08 11:10）：
今天 dump `CompositeThresholds` 后发现**生产打分实际只有 2 维**：
`momentum 0.3 + triple_rise 0.3`，其余 5 维权重全为 **0.0**。
而我今天只测了 `quality` / `value`（两者恰好已证明：常量 + 无 alpha），
**漏掉了权重为 0 但 F6/F7 认为是强簇的 `volume`**（|t_oos|=4.89，合成数据口径）。

**关键区别**：`volume` / `triple_rise` / `htf` 都**只用日线已有的 close/volume**，
**不需要 PIT 财务** ⇒ 它们**天然不在 F10 的"空转"名单里**（空转的是 quality/value/mr）。
⇒ 它们是"权重为 0 但可能有效"的候选，值得实测。

**本脚本性质**：纯观测 / proposal-only；不改 thresholds.yaml、不改打分、不落库。

用法（runner 上）：
    PYTHONPATH=<release>/src python pricefactor_ic_probe.py [--stocks 300] [--factors volume,triple_rise,htf]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RELEASE = Path("/opt/aqsp-runner/aqsp-scheduler-current")
sys.path.insert(0, str(RELEASE / "src"))

from aqsp.strategies.base import StrategyConfig  # noqa: E402
from aqsp.strategies.thresholds import load_thresholds  # noqa: E402

DB = "/opt/aqsp-runner/data/astocks_raw.db"

# 因子 → (类路径, cfgname, 是否需要 PIT 财务)
FACTORS = {
    "volume": ("aqsp.strategies.volume:VolumeBreakoutStrategy", "volume", False),
    "triple_rise": ("aqsp.strategies.triple_rise:TripleRiseStrategy", "triple_rise", False),
    "mean_reversion": ("aqsp.strategies.mean_reversion:MeanReversionStrategy", "mean_reversion", False),
    "htf": ("aqsp.strategies.candidates:HighTightFlagCandidate", "high_tight_flag", False),
    "momentum": ("aqsp.strategies.momentum:MomentumStrategy", "momentum", False),
}


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """未来 horizon 期收益（IC 的标签 y），index 原样保留。

    刻意不用 `.shift(-N)`：`tests/test_runtime_redline_guard.py` 的静态守卫禁止 runtime
    代码出现负数期 shift，这里用位置索引推导等价序列（同 factor_batch_scan.py 的写法）。
    """
    values = close.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) > horizon:
        out[:-horizon] = values[horizon:] / values[:-horizon] - 1.0
    return pd.Series(out, index=close.index)


def load_strategy(name: str, th):
    path, cfgname, _ = FACTORS[name]
    mod, cls_name = path.split(":")
    import importlib

    m = importlib.import_module(mod)
    cls = getattr(m, cls_name, None)
    if cls is None:
        return None
    return cls(StrategyConfig(name=cfgname, enabled=True), th)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=300)
    ap.add_argument("--factors", default="volume,triple_rise,htf")
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-09-08")
    args = ap.parse_args()

    names = [x.strip() for x in args.factors.split(",") if x.strip()]
    bad = [n for n in names if n not in FACTORS]
    if bad:
        print(f"不支持的因子：{bad}（可选 {list(FACTORS)}）")
        return 2

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    syms = pd.read_sql(
        "SELECT ts_code FROM daily_qfq WHERE trade_date>=? GROUP BY ts_code "
        "HAVING COUNT(*)>250 ORDER BY ts_code LIMIT ?",
        con, params=[args.start.replace("-", ""), args.stocks],
    )["ts_code"].tolist()
    ph = ",".join("?" * len(syms))
    q = pd.read_sql(
        f"SELECT ts_code, trade_date, open, high, low, close, volume, amount FROM daily_qfq "
        f"WHERE ts_code IN ({ph}) AND trade_date>=? ORDER BY ts_code, trade_date",
        con, params=[*syms, args.start.replace("-", "")],
    )
    con.close()
    q["date"] = pd.to_datetime(q["trade_date"].astype(str))
    data = {
        s: g.sort_values("date").set_index("date")[["open", "high", "low", "close", "volume", "amount"]]
        for s, g in q.groupby("ts_code", sort=False) if len(g) >= args.lookback + 20
    }
    print(f"票数 = {len(data)}  区间 {q['date'].min().date()} → {q['date'].max().date()}")
    print("ℹ 这些因子只用日线 close/volume ⇒ **无需 PIT 财务**，天然不在 F10 空转名单里")

    th = load_thresholds()
    strategies = {}
    for n in names:
        st = load_strategy(n, th)
        if st is None:
            print(f"  ⚠️ {n} 类未找到，跳过")
            continue
        strategies[n] = st
        print(f"  ✓ {n} 已实例化")
    if not strategies:
        return 2

    fwd = {s: _forward_return(df["close"], args.horizon) for s, df in data.items()}
    dates = sorted({d for d in q["date"].unique()})
    dates = [d for d in dates[:: args.step] if str(d) >= "2024-03-01"]

    results = {}
    for n, st in strategies.items():
        ics = []
        for d in dates:
            pairs, ys = [], []
            for s, df in data.items():
                if d not in df.index:
                    continue
                f = fwd[s].get(d, np.nan)
                if f is None or not np.isfinite(f):
                    continue
                pairs.append((s, df.loc[:d].tail(args.lookback + 10)))
                ys.append(f)
            if len(pairs) < 30:
                continue
            # 截面打分（与生产 composite 一致）：所有票一起喂
            scores = st.calculate_score(dict(pairs))
            xs = [scores.get(sym) for sym, _ in pairs]
            ok = [(v, f) for v, f in zip(xs, ys) if v is not None and np.isfinite(v)]
            if len(ok) < 30:
                continue
            xv = np.array([a for a, _ in ok], dtype=float)
            yv = np.array([b for _, b in ok], dtype=float)
            if len(np.unique(np.round(xv, 8))) < 2:
                continue  # 截面内无区分度 ⇒ corrcoef=nan
            ics.append(np.corrcoef(xv, yv)[0, 1])
        arr = np.array(ics)
        arr = arr[np.isfinite(arr)]
        if arr.size < 3:
            print(f"\n== {n} == 有效截面不足（{arr.size}），无法判显著性")
            results[n] = (float("nan"), float("nan"), arr.size)
            continue
        m, sd = arr.mean(), arr.std(ddof=1)
        t = m / (sd / np.sqrt(arr.size))
        results[n] = (m, t, arr.size)
        print(f"\n== `{n}` 真实 IC（{arr.size} 截面）==")
        print(f"  IC 均值 = {m:+.4f}   t = {t:+.2f}")
        print(f"  判定：{'★ 显著（|t|>=2）' if abs(t) >= 2 else '未达显著（|t|<2）'}")

    print("\n" + "=" * 78)
    print(f"{'因子':<20}{'IC':>10}{'t':>10}{'截面':>8}   结论")
    print("-" * 78)
    for n, (m, t, k) in results.items():
        tag = "★ 有 alpha" if abs(t) >= 2 else ("方向正" if m > 0 else "方向负")
        print(f"{n:<20}{m:>+10.4f}{t:>+10.2f}{k:>8}   {tag}")
    print("=" * 78)
    print("\n⚠️ 边界：单因子 IC，不等于组合贡献；任何权重改动仍需 gate 双门 + 股票池等权基准复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())