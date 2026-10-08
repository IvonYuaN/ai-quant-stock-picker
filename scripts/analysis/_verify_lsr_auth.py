"""聚焦复核：lower_shadow_ratio 的【权威】公式 IC（对齐 auto_factor_mining.calculate_factor_value）。

权威公式（auto_factor_mining.py:254-255）：
    (min(open, close) - low) / close
（单日，不除以区间幅度、不做 10 日平滑）

扫描脚本 factor_batch_scan.py:73 用的是另一套
    rolling10_mean((min(open,close)-low)/(high-low))
两者不等价 ⇒ 必须重算权威版真实 IC，不能挪用扫描 CSV 的 +3.90 等数字。

复用扫描脚本的数据加载 + 截面 IC 方法，只算权威这一列。
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd

DB = "A股量化分析数据/astocks_raw.db"
BOARDS = ("深主板", "沪主板", "创业板", "科创板")
HORIZON = 5
PER_BOARD = 500


def board_of(code: str) -> str:
    x = code.split(".")[0]
    if x.startswith("60"): return "沪主板"
    if x.startswith("68"): return "科创板"
    if x.startswith("00"): return "深主板"
    if x.startswith("30"): return "创业板"
    return "北交所"


def lsr_auth(df: pd.DataFrame) -> pd.Series:
    """权威 lower_shadow_ratio = (min(open,close) - low)/close（单日）。"""
    o, c, l = df["open"], df["close"], df["low"]
    return (np.minimum(o, c) - l) / c.where(c > 0)


def ic(g: pd.DataFrame) -> float:
    if len(g) < 30 or g["val"].nunique() < 2:
        return np.nan
    return float(np.corrcoef(g["val"].to_numpy(), g["fwd"].to_numpy())[0, 1])


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    raw = pd.read_sql(
        "SELECT ts_code, trade_date, open, high, low, close, volume, amount "
        "FROM daily_qfq WHERE trade_date>='20240101'", con)
    con.close()
    raw["date"] = pd.to_datetime(raw["trade_date"].astype(str))
    raw["board"] = raw["ts_code"].map(board_of)

    rows = []
    for b in BOARDS:
        sub_all = raw[raw["board"] == b]
        if sub_all.empty:
            continue
        cnt = sub_all.groupby("ts_code").size()
        keep = cnt[cnt > 250].index.tolist()[:PER_BOARD]
        sub = sub_all[sub_all["ts_code"].isin(keep)].sort_values(["ts_code", "date"])

        parts = []
        for code, g in sub.groupby("ts_code", sort=False):
            d = g.set_index("date")[["open", "high", "low", "close", "volume", "amount"]]
            fdf = pd.DataFrame({"val": lsr_auth(d)})
            c = d["close"].to_numpy(dtype=float)
            h = HORIZON
            fwd = np.full(len(c), np.nan)
            if len(c) > h:
                fwd[:-h] = c[h:] / c[:-h] - 1.0
            fdf["fwd"] = fwd
            fdf["ts_code"] = code
            parts.append(fdf.reset_index()[["date", "ts_code", "val", "fwd"]])
        long = pd.concat(parts, ignore_index=True).dropna(subset=["val", "fwd"])
        res = long.groupby("date", observed=True).apply(ic, include_groups=False).dropna()
        n = len(res)
        if n < 3:
            print(f"  {b}: 样本不足")
            continue
        icm = float(res.mean())
        sd = float(res.std())
        t = icm / (sd / np.sqrt(n)) if sd > 0 else np.nan
        rows.append((b, icm, n, t))
        print(f"  {b}: IC={icm:+.4f}  t={t:+.2f}  n={n}  {'★' if abs(t) >= 2 else ''}")

    print("\n=== lower_shadow_ratio 权威公式 (min(open,close)-low)/close 截面 IC ===")
    if not rows:
        print("无有效结果")
        return 2
    sig = sum(1 for _, _, _, t in rows if abs(t) >= 2)
    print(f"稳健板块数（|t|>=2）：{sig}/4")
    print("结论：", "3/4 以上稳健 ✅ 可纳入" if sig >= 3 else (
        "2/4 临界 ⚠️ 仅深创" if sig >= 2 else "不稳健 ❌ 放弃"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
