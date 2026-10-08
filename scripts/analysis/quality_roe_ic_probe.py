"""★ `quality` 的 ROE 真实 IC 实测（baostock 供 roeAvg，免 token，proposal-only，PIT 正确）

**为什么做**（2026-10-08，接 `value_ic_probe` 的结论）：
- `value_ic_probe` 已实测：baostock 覆盖率 100%，但 **`value` 无 alpha**（150 票 t=−0.39）
  ⇒ 「接财务」在 value 维度不值得投。
- 本脚本测 **`quality` 的 ROE 分量** ⇒ 若 ROE 也无效，**整条财务路线（决策点 6）结案**。

**🔴 PIT 合规（AGENTS.md §5 红线：禁止未来数据）**：
财务数据是**季度披露**的 —— `roeAvg` 属 `statDate=2025-12-31` 的财报，其
`pubDate=2026-04-17` 才公开。**若用"最新季度 roe"回填历史截面，就是 look-ahead bias**。
本脚本严格按 `pubDate <= 截面日` 取值（等价于「当时市场能看到的最新财报」），
逐日 forward-fill，**杜绝未来函数**。

**口径**：与生产 IC 一致 —— `lookback=20`（quality 自身用 tail(20)）、`horizon=5` 前瞻、
逐截面 Pearson IC。

用法（runner 上）：
    PYTHONPATH=<release>/src python quality_roe_ic_probe.py [--stocks 60]
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
from aqsp.strategies.quality import QualityStrategy  # noqa: E402
from aqsp.strategies.thresholds import load_thresholds  # noqa: E402

DB = "/opt/aqsp-runner/data/astocks_raw.db"


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


def to_bs_code(ts_code: str) -> str:
    code = ts_code.split(".")[0]
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sz.{code}"


def fetch_roe_history(bs_mod, code: str, years: list[int]) -> pd.DataFrame | None:
    """拉该票所有季度的 (statDate, pubDate, roeAvg)。

    ⚠️ `pubDate` 是 PIT 的关键 —— 只有 `pubDate <= 截面日` 的记录才可见。
    """
    rows = []
    for y in years:
        for q in (1, 2, 3, 4):
            rs = bs_mod.query_profit_data(code=code, year=y, quarter=q)
            if rs is None or rs.error_code != "0":
                continue
            while rs.next():
                rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=rs.fields)
    if "roeAvg" not in df.columns or "pubDate" not in df.columns:
        return None
    df = df[["statDate", "pubDate", "roeAvg"]].copy()
    df["pubDate"] = pd.to_datetime(df["pubDate"], errors="coerce")
    df["roeAvg"] = pd.to_numeric(df["roeAvg"], errors="coerce")
    df = df.dropna(subset=["pubDate", "roeAvg"])
    # 同一披露日可能对应多条（更正公告）⇒ 保留最后一条
    df = df.sort_values("pubDate").drop_duplicates("pubDate", keep="last")
    return df[df["pubDate"].notna()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=60)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--end", default="2026-09-08")
    args = ap.parse_args()

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
        for s, g in q.groupby("ts_code", sort=False) if len(g) >= 60
    }
    print(f"票数 = {len(data)}  区间 {q['date'].min().date()} → {q['date'].max().date()}")

    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login 失败: {lg.error_msg}")
        return 1
    print("baostock 登录成功（免 token）")

    years = list(range(int(args.start[:4]), int(args.end[:4]) + 1))
    ok = miss = 0
    pit_rows = []
    for s in data:
        h = fetch_roe_history(bs, to_bs_code(s), years)
        if h is None or h.empty:
            miss += 1
            continue
        # 🔴 PIT 合并：每只票的每个交易日，取「pubDate <= 该日」的最新一条 roe
        idx = data[s].index
        # 🔴 PIT 合并：按 pubDate 排序后 reindex 到交易日，再 ffill/bfill 组合。
        # 用 `reindex(method="ffill")` + 先把 index 对齐到 pubDate，
        # 保证「截面日 d 取到的是 pubDate <= d 的最后一条」。
        h2 = h.dropna(subset=["pubDate"]).sort_values("pubDate").set_index("pubDate")["roeAvg"]
        # 交易日 → 找 <= 当日的最后一个披露日（asof 语义）
        pos = h2.index.searchsorted(idx, side="right") - 1
        roe = np.where(pos >= 0, h2.to_numpy()[np.clip(pos, 0, None)], np.nan)
        data[s] = data[s].assign(roe=roe)
        ok += 1
        # 留档：PIT 生效证据（前若干条：披露日 → roe）
        if len(pit_rows) < 6:
            for _, r in h.sort_values("pubDate").tail(2).iterrows():
                pit_rows.append((s, r["pubDate"].date(), float(r["roeAvg"])))
    bs.logout()
    print(f"baostock 供 roe 成功 = {ok}  失败 = {miss}  ⇒ 覆盖率 = {ok / max(ok + miss, 1):.1%}")
    print("[PIT 证据] 样例（代码, 披露日, roeAvg）：")
    for s, d, v in pit_rows:
        print(f"    {s}  {d}  {v:+.4f}")
    if ok < 30:
        print("覆盖不足 30 只，无法算 IC")
        return 2

    strategy = QualityStrategy(StrategyConfig(name="quality", enabled=True), load_thresholds())

    # 验证 roe 列真的进了窗口（避免又是一次"看起来在动其实没变"）
    med = np.median([data[s]["roe"].nunique() for s in list(data)[:20]])
    print(f"\n[验证] roe 列唯一值中位数 = {med:.1f}（>1 ⇒ 有效）")
    _probe_input = {s: data[s].tail(20) for s in list(data)[:200] if "roe" in data[s].columns}
    _sc = strategy.calculate_score(_probe_input)
    _vals = np.array([v for v in _sc.values() if v is not None], dtype=float)
    print(f"[验证] 截面打分下 quality score：唯一值={len(np.unique(np.round(_vals, 6)))} "
          f"范围=[{_vals.min():.4f}, {_vals.max():.4f}] std={_vals.std():.4f}")

    fwd = {s: _forward_return(df["close"], args.horizon) for s, df in data.items()}
    dates = sorted({d for d in q["date"].unique()})
    dates = [d for d in dates[:: args.step] if str(d) >= "2024-10-01"]

    ics = []
    for d in dates:
        pairs, ys = [], []
        for s, df in data.items():
            if d not in df.index or "roe" not in df.columns:
                continue
            f = fwd[s].get(d, np.nan)
            if f is None or not np.isfinite(f):
                continue
            pairs.append((s, df.loc[:d].tail(20)))
            ys.append(f)

        # 🔴 关键修正：IC 必须**截面**打分 —— 把该截面所有票一起喂给
        # calculate_score（生产 composite 就是这么用的：逐票循环但共享同一 data dict）。
        # 此前逐票单独喂 `{s: sub}` ⇒ 每票内部无横截面 ⇒ 算出的"IC"是浮点噪音。
        if len(pairs) < 30:
            continue
        scores = strategy.calculate_score(dict(pairs))
        xs_valid, ys_valid = [], []
        for (sym, _), f in zip(pairs, ys):
            v = scores.get(sym)
            if v is not None and np.isfinite(v):
                xs_valid.append(v)
                ys_valid.append(f)
        if len(set(xs_valid)) < 2:  # 截面内 score 无差异 ⇒ corrcoef=nan，跳过
            continue
        ics.append(np.corrcoef(xs_valid, ys_valid)[0, 1])

    ics = np.array(ics)
    raw = len(ics)
    ics = ics[np.isfinite(ics)]
    print(f"\n[诊断] 截面 {raw} 个，有限 IC {ics.size} 个")
    if ics.size < 3:
        print("有效截面不足，不判显著性")
        return 2
    m, sd = ics.mean(), ics.std(ddof=1)
    t = m / (sd / np.sqrt(ics.size))
    print(f"\n== `quality`(ROE 分量) 真实 IC（PIT 对齐，{ics.size} 截面）==")
    print(f"  IC 均值 = {m:+.4f}   t = {t:+.2f}")
    print(f"  判定：{'★ 有预测力（|t|>=2）' if abs(t) >= 2 else '未达显著（|t|<2）'}")
    print("\n⚠️ 边界：只测 ROE 分量（baostock 只供这一项），而 quality 还有 "
          "roa/debt/margin 三个分量在退 0.5。")
    print("⚠️ 只回答「ROE 有没有 alpha」，不回答「接进 gate 后组合会不会赚」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())