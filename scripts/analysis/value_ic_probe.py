"""★ `value` 因子的真实 IC 实测（baostock 供 pe/pb，免 token，proposal-only）

**为什么做这个**（2026-10-08 决策点 6）：
- 生产 gate 走 `--streaming`，而 `cli.py:3691` 强制 `--skip-pit-financials`
  ⇒ `quality`/`value` 恒为常量（7 维里 3 维空转）。
- 实测 baostock **免 token** 即可提供 `peTTM`/`pbMRQ`，且 `value` 需要的 2 列**全部可得**
  ⇒ 值得先问「value 到底有没有 alpha」，再决定要不要投入做 gate 侧的有界 PIT 加载。

**本脚本的性质**：
- **纯观测、离线、只读**；不改 `thresholds.yaml`、不改打分、不落库、不改任何生产配置。
- 用**生产代码的 `ValueStrategy`**，避免"自己实现一遍"的偏差。
- 口径与生产 IC 一致：`lookback=60`、`horizon=5` 前瞻、逐截面 Pearson IC。

用法（在 runner 上）：
    PYTHONPATH=<release>/src python value_ic_probe.py [--stocks N] [--days 90]
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
from aqsp.strategies.value import ValueStrategy  # noqa: E402

DB = "/opt/aqsp-runner/data/astocks_raw.db"


def to_bs_code(ts_code: str) -> str:
    """`600519` / `000001.SZ` → baostock 的 `sh.600519` / `sz.000001`。"""
    code = ts_code.split(".")[0]
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sz.{code}"


def fetch_valuation(bs_mod, code: str, start: str, end: str) -> pd.DataFrame | None:
    """拉单票 peTTM/pbMRQ 逐日序列；失败返回 None（不抛，避免一只票拖垮整批）。"""
    rs = bs_mod.query_history_k_data_plus(
        code, "date,peTTM,pbMRQ", start_date=start, end_date=end,
        frequency="d", adjustflag="3",
    )
    # baostock 日期格式非法时会直接返回 None（而非带 error_code 的对象）
    if rs is None:
        return None
    if rs.error_code != "0":
        return None
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=rs.fields)
    df["date"] = pd.to_datetime(df["date"])
    for c in ("peTTM", "pbMRQ"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["date", "peTTM", "pbMRQ"]].rename(columns={"peTTM": "pe", "pbMRQ": "pb"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=150, help="取多少只票（baostock 逐票查询，越慢越少）")
    ap.add_argument("--lookback", type=int, default=60, help="打分回看窗口（生产同口径）")
    ap.add_argument("--horizon", type=int, default=5, help="前瞻天数（生产同口径）")
    ap.add_argument("--step", type=int, default=5, help="截面采样步长")
    ap.add_argument("--start", default="2025-09-01")
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
        for s, g in q.groupby("ts_code", sort=False) if len(g) >= args.lookback + 20
    }
    print(f"票数 = {len(data)}  区间 {q['date'].min().date()} → {q['date'].max().date()}")

    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock login 失败: {lg.error_msg}")
        return 1
    print("baostock 登录成功（免 token）")

    # ⚠️ baostock 的 k_data 接口要的是 **YYYY-MM-DD**（带连字符）；
    # 实测传 YYYYMMDD 反而报「日期格式不正确」并返回 0 行。
    # （第一次我以为它要 YYYYMMDD、把这里"修"成了无连字符 —— 又一次凭猜测改错。）
    start_c, end_c = args.start, args.end
    ok = miss = 0
    for s in data:
        v = fetch_valuation(bs, to_bs_code(s), start_c, end_c)
        if v is None or v.empty or v["pe"].notna().sum() == 0:
            miss += 1
            continue
        idx = data[s].index
        vv = v.set_index("date").reindex(idx)
        data[s] = data[s].assign(pe=vv["pe"].to_numpy(), pb=vv["pb"].to_numpy())
        ok += 1
    bs.logout()
    print(f"baostock 供数成功 = {ok}  失败 = {miss}  ⇒ 覆盖率 = {ok / max(ok + miss, 1):.1%}")
    if ok < 30:
        print("覆盖不足 30 只，无法算 IC（每截面至少 30 只才有意义）")
        return 2

    strategy = ValueStrategy(StrategyConfig(name="value", enabled=True), load_thresholds())

    # 先验证「列真的进去了」—— 避免又是一次"看起来在动其实没变"的实验
    probe = [v["pe"].nunique() for v in data.values() if "pe" in v]
    print(f"pe 列唯一值中位数 = {np.median(probe):.1f}（>1 ⇒ 列有效，非全 NaN/常量）")

    fwd = {s: df["close"].shift(-args.horizon) / df["close"] - 1 for s, df in data.items()}
    dates = sorted({d for d in q["date"].unique()})
    dates = [d for d in dates[:: args.step] if str(d) >= "2025-10-01"]

    ics = []
    for d in dates:
        xs, ys = [], []
        for s, df in data.items():
            if d not in df.index:
                continue
            f = fwd[s].get(d, np.nan)
            if f is None or not np.isfinite(f):
                continue
            sub = df.loc[:d].tail(args.lookback + 10)
            sc = strategy.calculate_score({s: sub}).get(s)
            if sc is not None and np.isfinite(sc):
                xs.append(sc)
                ys.append(f)
        if len(xs) >= 30:
            ics.append(np.corrcoef(xs, ys)[0, 1])

    ics = np.array(ics)
    # 🔍 nan 诊断：区分「截面数不足」/「每截面内 xs 或 ys 全相同（corrcoef 返回 nan）」
    raw_n = len(ics)
    ics = ics[np.isfinite(ics)]
    print(f"\n[诊断] 原始截面 {raw_n} 个，有限 IC {ics.size} 个，nan {raw_n - ics.size} 个")
    if ics.size == 0 and raw_n > 0:
        print("[诊断] ⇒ 全部为 nan ⇒ 说明每个截面内 xs 或 ys 是**常量**（corrcoef(常量,·) = nan）")
        print("[诊断]    最可能：ValueStrategy 在 lookback=%d 的窗口内返回常量（如 pe 全缺/全同）" % args.lookback)
        probe2 = [len({round(v, 8) for v in strategy.calculate_score({s: data[s].tail(args.lookback + 10)}).values()}) for s in list(data)[:5]]
        print(f"[诊断]    直接抽查 5 票在最后一个截面的 score 唯一值个数：{probe2}")
        print("[诊断]    ⇒ 1 表示该票 score 是常量 ⇒ value 仍在退 0.5（列没真正进窗口）")
    if ics.size < 3:
        print(f"有效截面仅 {ics.size} 个，不足以判显著性")
        return 2
    m, sd = ics.mean(), ics.std(ddof=1)
    t = m / (sd / np.sqrt(ics.size))
    print(f"\n== `value` 因子真实 IC（生产 ValueStrategy，{ics.size} 个截面）==")
    print(f"  IC 均值 = {m:+.4f}   t = {t:+.2f}")
    verdict = (
        "★ 有预测力（|t|>=2）" if abs(t) >= 2
        else ("方向为正但未达显著（|t|<2）" if m > 0 else "方向为负且未达显著")
    )
    print(f"  判定：{verdict}")
    print("\n⚠️ 本结果只回答「value 有没有 alpha」，不回答「接进 gate 后组合会不会赚」。")
    print("   后者还需 gate 双门 + 股票池等权基准复核（gate 从不计算超额）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
