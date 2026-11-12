# -*- coding: utf-8 -*-
"""策略 vs 等权基准的**超额收益**计算——gate 判读的唯一实现。

## 为什么必须集中在一处
`gate` 只报绝对收益。同一份产物，2026-11-12 我先后算出过 `超额 +55pp` 和
`超额 −6.9pp` 两个相反结论，差别只在基准算法。重复实现一次就会重复一次错误，
所以基准与超额只在这里实现。

## 基准口径（**唯一正确写法**）
**等权买入持有**：每只票以窗口首个有效价归一到 1，再按票取均值。

🔴 **不能**用 ``(1 + pct_change().median(axis=1)).cumprod()``：
截面「中位日收益」系统性低于组合增长率，长期复利把误差放大到几十个点。
实测同一窗口（2023-11-07 → 2026-11-06）：

| 口径 | 结果 |
|---|---|
| 等权买入持有（实际价格） | **+2.30%** |
| 中位票 | −9.07% |
| median 日收益累乘 | **−71.89%**（荒谬） |

## 局限
这里用**全市场等权**，不是「同池等权」。同池等权需按 composite 逐期重建 2f 池
（约 10 分钟/次），本模块不做。若策略池偏小盘，超额会被系统性高估或低估——
结论里必须写明这一点。

另有一条**必须坦白的不稳定性**：等权是「各票比值的算术平均」，会被极端赢家拉动，
故基准本身随样本量变化 —— 实测同一窗口 sample=400/800/1200 分别得到
`+6.50% / +6.46% / +1.86%`。因此**只能看超额的方向与量级，不能抠小数点**；
若某次结论的正负号在样本量变动下就翻转，说明它本来就不成立。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 250.0

__all__ = [
    "ArmExcess",
    "equal_weight_curve",
    "load_arm_periods",
    "excess_for_arm",
    "render_markdown",
]


@dataclass(frozen=True)
class ArmExcess:
    """单个臂相对等权基准的超额表现。"""

    arm: str
    periods: int
    strategy_ann: float
    benchmark_ann: float
    excess_ann: float
    excess_mean: float
    excess_t: float
    beat_periods: int


def equal_weight_curve(db_path: str, *, sample: int = 1200) -> pd.Series:
    """全市场等权**买入持有**净值曲线（index=YYYYMMDD，value=归一净值）。

    从全市场按代码顺序**等距抽样** ``sample`` 只票（跨板块，避免 ``LIMIT N`` 的偏置）。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        universe = [
            r[0]
            for r in con.execute(
                "SELECT ts_code FROM daily_qfq GROUP BY ts_code HAVING COUNT(*) > 200"
                " ORDER BY ts_code"
            )
        ]
        # 🔴 必须**等距抽样**，不能 `ORDER BY ts_code LIMIT N`：
        # 后者只取代码最前面的 N 只（全挤在 000xxx/001xxx），板块严重偏置。
        # 实测同一窗口：800 只取前 N ⇒ 基准 +1.85%；400 只取前 N ⇒ +0.14%，
        # 基准一变，"超额"结论就跟着翻。等距抽样跨全代码空间，样本量变化影响小。
        step = max(1, len(universe) // max(1, sample))
        codes = universe[::step][:sample]
        if not codes:
            return pd.Series(dtype=float)
        marks = ",".join("?" * len(codes))
        px = pd.read_sql(
            f"SELECT ts_code, trade_date, close FROM daily_qfq WHERE ts_code IN ({marks})",
            con,
            params=tuple(codes),
        )
    finally:
        con.close()
    wide = px.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
    filled = wide.ffill()
    base = filled.apply(
        lambda col: col.dropna().iloc[0] if col.notna().any() else float("nan")
    )
    return filled.divide(base, axis=1).mean(axis=1, skipna=True).dropna()


def load_arm_periods(arms_dir: str | Path) -> dict[str, dict[str, list[dict]]]:
    """按 run 窗口分组读 gate 逐期记录：``{run: {arm: [{start,end,ret}]}}``。

    🔴 必须分组：不同窗口的臂**不可比**。曾把 ``2023-09-05_2026-09-18``（WF-*）与
    ``2023-11-07_2026-11-06``（WO-*）混排成一张"16 个臂"的表，结论全错。
    """
    out: dict[str, dict[str, list[dict]]] = {}
    for f in sorted(Path(arms_dir).glob("walkforward_gate_resume_*.jsonl.*")):
        body = f.name.split("walkforward_gate_resume_")[-1]
        run = body.split(".jsonl.")[0]
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
            out.setdefault(run, {})[arm] = sorted(rows, key=lambda r: r["start"])
    return out


def excess_for_arm(rows: list[dict], curve: pd.Series) -> ArmExcess | None:
    """逐期对齐策略收益与基准收益，返回超额统计；有效期数不足 3 返回 ``None``。"""
    if curve.empty or len(rows) < 3:
        return None
    idx = curve.index.to_numpy()
    s_ret: list[float] = []
    b_ret: list[float] = []
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
        return None

    s_arr, b_arr = np.array(s_ret), np.array(b_ret)
    diff = s_arr - b_arr
    n = len(s_arr)
    ppy = TRADING_DAYS / max(
        1.0,
        float(
            np.mean(
                [
                    (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])).days
                    * TRADING_DAYS
                    / 365
                    for r in rows
                ]
            )
        ),
    )

    def _ann(x: np.ndarray) -> float:
        return float((1 + x).prod() ** (ppy / len(x)) - 1)

    m = float(diff.mean())
    sd = float(diff.std(ddof=1))
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    return ArmExcess(
        arm="",
        periods=n,
        strategy_ann=_ann(s_arr),
        benchmark_ann=_ann(b_arr),
        excess_ann=_ann(diff),
        excess_mean=m,
        excess_t=t,
        beat_periods=int((diff > 0).sum()),
    )


def render_markdown(arms: dict[str, dict[str, list[dict]]], curve: pd.Series) -> str:
    """渲染按 run 分组的超额表（markdown）。"""
    lines: list[str] = []
    if len(arms) > 1:
        lines.append(
            f"> ⚠️ 检测到 {len(arms)} 个不同 run，**分别出表**（不同窗口的臂不可比）。"
        )
    for run, arm_map in arms.items():
        lines.append(f"\n### 超额收益 · run {run.replace('_', ' ~ ')}\n")
        lines.append("| 臂 | 期数 | 策略年化 | 基准年化 | **超额年化** | 逐期超额 | t | 跑赢 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        got: list[float] = []
        for arm, rows in sorted(arm_map.items()):
            st = excess_for_arm(rows, curve)
            if st is None:
                continue
            got.append(st.excess_ann)
            lines.append(
                f"| {arm} | {st.periods} | {st.strategy_ann:+.2%} | {st.benchmark_ann:+.2%} | "
                f"**{st.excess_ann:+.2%}** | {st.excess_mean:+.2%} | {st.excess_t:+.2f} | "
                f"{st.beat_periods}/{st.periods} |"
            )
        if got:
            ex = np.array(got)
            lines.append(
                f"\n小结：**超额均值 {ex.mean():+.2%}**｜正超额臂 {int((ex > 0).sum())}/{len(ex)}"
            )
    lines.append(
        "\n⚠️ 基准为**全市场等权买入持有**（非「同池等权」），且对样本量敏感"
        "（实测 sample 400/800/1200 ⇒ +6.50%/+6.46%/+1.86%）；"
        "**只看方向与量级，不抠小数点**。"
    )
    return "\n".join(lines)
