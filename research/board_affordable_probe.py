#!/usr/bin/env python3
"""纪律探针：回测口径 vs 账本口径，买的是不是同一批票。

## 为什么要做这个检验

`paper_arena.json` 里 P1 有 27 条信号，**只有 7 条真买到**，其余 20 条落的是
「单笔¥2000买不起1手(价71.0)」这类原因。也就是说：

    账本单笔预算 = ¥1万 × 20% = ¥2000
    A 股一手 = 100 股
    ⇒ 只有股价 ≤ ¥19.8 的票才买得进

而回测的 +1.61%(首板)/+2.09%(二板) 是在**全部** B 格格子上算的
（31498 / 4220 格），里面大部分高价票账本根本买不到。

🔴 这是一个**样本选择偏差**：若「买得起」这一条件本身与收益相关，
   那么「回测均值」和「账本能拿到的均值」就不是同一个数，
   而这个差值完全由资金规模决定，跟策略逻辑无关。

## 口径

- 作业集：B 格 = 开盘没一字顶死 + 盘中摸板（`touch & opened`），按板高分组。
- 买价：涨停价（`lu_q`）—— 这是**保守**取法，真实摸板即买价 ≤ 涨停价。
- 卖价：次日开盘（`nxt_op_q`），即六模式统一用的 `t1_open`。
- 分档：按**非复权股价**（真实要付的钱）分档，边界取 ¥20 —— 正是 ¥2000/100 股的门槛。
- 成本：双边 0.15%（与 `executable_entry_probe.py` 一致）。

## 这不是调参

分档边界只有一个有先验含义的点：¥19.8（资金约束）。其余档位只是把
「贵多少」摊开看，不做最优档搜索 —— 找到某个档特别好也不能用，
因为档位不是可选参数，是资金决定的。

用法：
    python research/board_affordable_probe.py --db <db> --json out.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

# 账本真实门槛：单笔 ¥2000 / 100 股一手 = 每股 ≤ ¥20（含 1% 单边成本后约 ¥19.8）
AFFORD_PX = 20.0

BUCKETS = [
    ("≤¥10", 0.0, 10.0),
    ("¥10~20(买得起)", 10.0, AFFORD_PX),
    ("¥20~30", AFFORD_PX, 30.0),
    ("¥30~50", 30.0, 50.0),
    (">¥50", 50.0, np.inf),
]


def _load_probe():
    for c in (Path(__file__).resolve().parent / "shortline_probe.py",
              Path(__file__).resolve().parents[1] / "research" / "shortline_probe.py"):
        if c.exists():
            p = c
            break
    else:
        raise SystemExit("[fatal] 找不到 shortline_probe.py")
    spec = importlib.util.spec_from_file_location("shortline_probe", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["shortline_probe"] = m
    spec.loader.exec_module(m)
    return m


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan)
    if k > 0:
        out[k:] = a[:-k]
    else:
        out[:k] = a[-k:]
    return out


def _stat(daily, cost=0.0015):
    """按日等权序列的均值 + t 值。**不累乘**（截面中位/均值不能累乘）。"""
    a = np.asarray([d for d in daily if np.isfinite(d)], dtype=float) - cost
    if a.size < 20:
        return {"n_days": int(a.size), "note": "样本<20"}
    t = a.mean() / (a.std(ddof=1) / np.sqrt(a.size))
    return {"n_days": int(a.size), "mean_pct": round(float(a.mean()) * 100, 3),
            "median_pct": round(float(np.median(a)) * 100, 3),
            "win_pct": round(float((a > 0).mean()) * 100, 1),
            "t": round(float(t), 2)}


def _streak_prev(zt: np.ndarray) -> np.ndarray:
    T, N = zt.shape
    out = np.zeros((T, N), dtype=np.int8)
    run = np.zeros(N, dtype=np.int8)
    for t in range(T):
        out[t] = run
        run = np.where(zt[t], run + 1, 0)
    return out


def probe(db: str, start: str = "20231101") -> dict:
    sl = _load_probe()
    P = sl.load_main_board(db, start)
    close = P["close"]
    cv = close.to_numpy(dtype=float)          # 前复权：算涨停/收益
    cq = P["cqr"].to_numpy(dtype=float)       # 非复权：真实付出价
    op, hh, lo = (P[k].to_numpy(dtype=float) for k in ("opn", "high", "low"))
    T, N = cv.shape
    listing = sl._listing_mask(close, lookback=30)

    with np.errstate(invalid="ignore", divide="ignore"):
        zt = np.zeros_like(cv, dtype=bool)
        zt[1:] = cv[1:] / cv[:-1] - 1.0 >= 0.098
        ratio = cv / cq
        prev_cq = np.vstack([np.full((1, N), np.nan), cq[:-1]])
        lu = np.round(prev_cq * 1.10, 2)
    lu_q = lu * ratio
    op_q = op * ratio
    streak = _streak_prev(zt)
    nxt_op_q = _shift(op_q, -1)

    touch = np.isfinite(hh) & np.isfinite(lu) & (hh >= lu * 0.998)
    # 🔴 `opened` 必须用**盘中最低价**破板来判，不能用「收盘没封住」——
    # 后者等价于「炸板」这个事后结果，会变成拿结果筛样本（同 B 格前视一个病）。
    # 盘中开过板 ⇒ 涨停价上确实能有成交，摸板即买才可实施。
    opened = np.isfinite(lo) & (lo < lu * 0.998)
    bcell = touch & opened                                # B 格

    def daily(mask, buy, sell):
        out = []
        for t in range(1, T - 1):
            m = mask[t] & listing[t] & np.isfinite(buy[t]) & np.isfinite(sell[t]) & (buy[t] > 0)
            c = np.where(m)[0]
            if c.size:
                out.append(float(np.nanmean(sell[t, c] / buy[t, c] - 1.0)))
        return out

    res: dict = {"as_of": close.index[-1].strftime("%Y-%m-%d"),
                 "cost_round_trip": 0.003, "afford_px": AFFORD_PX,
                 "note": "买价=涨停价(保守)；卖价=次日开盘；按日等权、不累乘",
                 "heights": {}}

    px = cq                                                 # 真实股价（非复权）
    afford = np.isfinite(px) & (px <= AFFORD_PX)

    for h, lab in ((0, "首板"), (1, "2板")):
        hm = streak == h
        full = bcell & hm
        row = {"full": _stat(daily(full, lu_q, nxt_op_q)),
               "afford": _stat(daily(full & afford, lu_q, nxt_op_q)),
               "cannot": _stat(daily(full & ~afford, lu_q, nxt_op_q))}
        # 信号格数占比：账本实际能吃到的份额
        n_full = int((full & listing & np.isfinite(nxt_op_q)).sum())
        n_aff = int((full & afford & listing & np.isfinite(nxt_op_q)).sum())
        row["share_affordable"] = round(n_aff / n_full * 100, 1) if n_full else None
        row["cells"] = {"full": n_full, "afford": n_aff}

        # 分档
        buckets = {}
        for name, lo, hi in BUCKETS:
            m = full & np.isfinite(px) & (px > lo) & (px <= hi)
            buckets[name] = _stat(daily(m, lu_q, nxt_op_q))
        row["by_price"] = buckets
        res["heights"][lab] = row

    # 结论行：账本口径相对回测口径的差（pp）
    for lab in res["heights"]:
        r = res["heights"][lab]
        try:
            gap = r["afford"]["mean_pct"] - r["full"]["mean_pct"]
            res["heights"][lab]["gap_pp"] = round(gap, 3)
        except KeyError:
            res["heights"][lab]["gap_pp"] = None
    return res


def _fmt(r: dict) -> str:
    if "mean_pct" not in r:
        return "样本不足(%s)" % r.get("note", "")
    return "%+.3f%% t%-5.2f 胜%4.1f%% (n=%d)" % (
        r["mean_pct"], r["t"], r["win_pct"], r["n_days"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="20231101")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = probe(a.db, a.start)

    print("=" * 78)
    print("回测口径 vs 账本口径：买得起的票，是不是同一批？")
    print("数据截至 %s ｜ 买价=涨停价(保守) 卖价=次日开盘 ｜ 扣 %.1f%% 双边"
          % (res["as_of"], res["cost_round_trip"] * 100))
    print("=" * 78)
    for lab, r in res["heights"].items():
        print("\n【%s】可买份额 %.1f%% (%d/%d 格)"
              % (lab, r["share_affordable"] or 0,
                 r["cells"]["afford"], r["cells"]["full"]))
        print("  回测口径(全取)      : %s" % _fmt(r["full"]))
        print("  账本口径(≤¥%.0f)     : %s   ← 这才是账本能拿到的" % (AFFORD_PX, _fmt(r["afford"])))
        print("  买不到的部分(>¥%.0f) : %s" % (AFFORD_PX, _fmt(r["cannot"])))
        print("  差额                : %+.3f pp" % (r.get("gap_pp") or 0))
        print("  —— 按股价分档 ——")
        for name, b in r["by_price"].items():
            print("    %-16s %s" % (name, _fmt(b)))
    print()
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print("写出 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
