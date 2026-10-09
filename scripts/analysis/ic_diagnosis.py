#!/usr/bin/env python3
"""ic_diagnosis.py — 滚动窗口因子 IC 诊断（日常调度版，runner 侧执行）。

区别于 factor_ic_diagnosis.py（gate 失败归因、3 年长窗、一次性）：
  - as-of = 库内 MAX(trade_date)，rolling 窗口（默认近 90 交易日）周级持续产出，
    窗口右端由库本身截断，**绝不读未来数据**；
  - 标的池默认 = 近 20 日 avg(amount) top-N（与 daily 可成交池口径一致），
    也可 --symbols-file 指定 gate 证据池；
  - 产物 = factor_ic_latest.json（收评/研究链路消费）+ report.md + ic_history.jsonl；
  - 体量红线：单进程有界样本（top-N × step 截面）分钟级，只跑 runner（4C）不碰 prod 2C。

🔴 合规：纯诊断，不产出信号、不进回测/选股/上线，不写回打分/排序/下单。
   IC 以未来收益为目标变量（离线评估），与回测 look-ahead 违规无关（AGENTS.md §3.6）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd

from aqsp.core.time import now_shanghai
from scripts.factor_ic_diagnosis import (
    WF001,
    _ic,
    _optin_prefiltered_universe,
    _stats,
    load_prices,
)

# 基础三因子与 gate WF-001 同口径；extra 为其追加项（默认空=省 CPU）。
_BASE_FACTORS = ("momentum", "triple_rise", "composite")
_EXTRA_CHOICES = ("htf", "mr", "volume", "rps", "volume_surge", "price_volume_corr")
_REC_SPAN = 20  # 近 20 截面的短期口径（因子翻向侦测）


def _as_of(db: str) -> str:
    """库内 MAX(trade_date)（YYYYMMDD → YYYY-MM-DD）。窗口右端由它截断，永不写未来。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        (m,) = con.execute("SELECT MAX(trade_date) FROM daily_qfq").fetchone()
    finally:
        con.close()
    m = str(m)
    return f"{m[:4]}-{m[4:6]}-{m[6:8]}"


def _distinct_dates(
    db: str, as_of: str, limit: int, lo: str | None = None
) -> list[str]:
    """≤as_of（可选且 ≥lo）的最近 limit 个交易日的升序日期（YYYY-MM-DD）。"""
    sql = "SELECT DISTINCT trade_date FROM daily_qfq WHERE trade_date <= ? "
    if lo:
        sql += "AND trade_date >= ? "
    sql += "ORDER BY trade_date DESC LIMIT ?"
    params = (as_of.replace("-", ""),)
    if lo:
        params = (lo, as_of.replace("-", ""))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        days = [r[0] for r in con.execute(sql, params + (limit,))]
    finally:
        con.close()
    days.reverse()
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days]


def _top_universe(db: str, as_of: str, top_n: int, min_avg_amount: float) -> list[str]:
    """近 20 交易日 avg(amount) top-N 流动性池（与 daily 可成交口径一致）。"""
    recent = _distinct_dates(db, as_of, 20)
    if not recent:
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ts_code FROM daily_qfq WHERE trade_date BETWEEN ? AND ? AND amount > 0 "
            "GROUP BY ts_code HAVING AVG(amount) >= ? ORDER BY AVG(amount) DESC LIMIT ?",
            (recent[0].replace("-", ""), as_of.replace("-", ""), min_avg_amount, top_n),
        ).fetchall()
    finally:
        con.close()
    return [str(ts).split(".")[0] for (ts,) in rows]


def _window_dates(db: str, as_of: str, window_days: int, lookback: int) -> list[str]:
    """as_of 起、向前 (window_days + lookback + 缓冲) 个交易日的升序日期列表。"""
    return _distinct_dates(db, as_of, window_days + lookback + 30)


def _planb_profile_variant(profile: str) -> "object":
    """planb profile → 代表臂（8 臂权重向量逐臂一致，仅 top_n×horizon 互异；
    IC 侧 horizon/lookback 由 run() 外部控制，故取第一臂即可）。

    非 planb profile 抛 ValueError（本入口只开放 planb_v1/v2/v3，WF001 走缺省路径）。
    """
    from aqsp.cli import _PLANB_VARIANTS

    if profile not in _PLANB_VARIANTS:
        raise ValueError(
            f"未知 --planb-profile {profile!r}（可选: {', '.join(_PLANB_VARIANTS)} 或省略=WF001）"
        )
    return _PLANB_VARIANTS[profile][0]


def _build_factor_objs(
    extra: list[str], lookback: int = 60, variant: object | None = None
) -> tuple[list[str], dict[str, object]]:
    """实例化打分对象。基础三因子走 WF-001 变体；extra 用 enabled 派生副本。

    ``variant``（方案 B 注入）：缺省 None = 钉 WF001（现状零漂移）；给定 planb
    变体时 composite/基础三因子全部按该变体 thresholds 打分（7 维权重经
    ``_apply_walkforward_grid_variant`` planb 分支注入），extra 因子随其
    enabled 状态派生——改后权重的 composite 因子 IC 由此可复现。
    """
    from aqsp.cli import _apply_walkforward_grid_variant, load_thresholds
    from aqsp.strategies.base import StrategyConfig
    from aqsp.strategies.candidates import HighTightFlagCandidate, RpsCandidate
    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.mean_reversion import MeanReversionStrategy
    from aqsp.strategies.price_volume_corr import PriceVolumeLevelCorrelation
    from aqsp.strategies.volume import VolumeBreakoutStrategy
    from aqsp.strategies.volume_surge import VolumeSurge

    # WF001 由模块顶层 import（scripts.factor_ic_diagnosis）提供，此处不再重复 import。
    thresholds = load_thresholds()
    active_variant = variant if variant is not None else WF001
    vth = _apply_walkforward_grid_variant(thresholds, active_variant)
    strategy = CompositeStrategy(thresholds=vth)
    diag = vth.with_overrides("mean_reversion", {"enabled": True}).with_overrides(
        "volume", {"enabled": True}
    )
    extra_cls = {
        "mr": (MeanReversionStrategy, "mean_reversion"),
        "volume": (VolumeBreakoutStrategy, "volume"),
        "rps": (RpsCandidate, "rps"),
        "htf": (HighTightFlagCandidate, "high_tight_flag"),
        "volume_surge": (VolumeSurge, "volume_surge"),
        "price_volume_corr": (PriceVolumeLevelCorrelation, "price_volume_corr"),
    }
    # volume_surge / price_volume_corr **常诊**（2026-10-09，监控盲区修复）：
    # 「三源是否启用」（抉择单 v2 D2）需要这两个维度的 IC 历史作依据，而 cron 的
    # EXTRA_FACTORS 不含它们；在代码层常诊可免改 runner crontab（cron 变更须仓主
    # 授权）。raw 口径（invert_signal=False，与 momentum 诊断一致）；二者 enabled
    # 开关与类实现无关（composite.py 本就以 enabled=True 实例化）。
    extra = list(extra or [])
    for _always in ("volume_surge", "price_volume_corr"):
        if _always not in extra:
            extra.append(_always)
    objs: dict[str, object] = {
        "momentum": strategy.momentum_strategy,
        "triple_rise": strategy.triple_rise_strategy,
        "composite": strategy,
    }
    order = list(_BASE_FACTORS)
    for key, (cls, cfgname) in extra_cls.items():
        if key in extra and key not in order:
            # rps 的默认 rps_period=120 远超本诊断每截面实际喂入的窗口
            # （下方 `df[df["date"] <= d].tail(lookback + 10)`，lookback=60 ⇒ 70 行）
            # ⇒ `len(p) < k+1` 恒成立 ⇒ 全票 NaN ⇒ IC 恒报 nan（2026-09-28 实测：
            #   730 日/step5/146 截面跑批里 rps 四项指标全 nan）。
            # 显式对齐到 lookback：既保证可计算，又保持「仅用截面日及之前数据」的 PIT 语义。
            params = {"rps_period": int(lookback)} if key == "rps" else {}
            try:
                objs[key] = cls(
                    StrategyConfig(name=cfgname, enabled=True, params=params), diag
                )
                order.append(key)
            except Exception as exc:  # 单因子不可用不阻断整体诊断
                print(f"[warn] {key} 无法实例化，跳过: {exc}")
    return order, objs


def run(
    db: str,
    *,
    window_days: int = 90,
    as_of: str | None = None,
    lookback: int = 60,
    horizon: int = 3,
    step: int = 10,
    top_n: int = 300,
    min_avg_amount: float = 0.0,
    symbols_file: str | None = None,
    extra_factors: list[str] | None = None,
    out_dir: str | Path = ".",
    write_ready: bool = True,
    planb_profile: str | None = None,
) -> dict:
    """滚动 IC 诊断主流程，返回写入 JSON 的结构（供测试直接断言）。
    write_ready 时落 IC_READY 标记（同 runner_gate 的 RESULT_READY 契约）。
    planb_profile（方案 B 注入）：缺省 None = 钉 WF001（现状零漂移）；给定
    planb_v1/v2/v3 时 composite/基础三因子按该 profile 代表臂 7 维权重打分
    （主判据「改后权重双窗 IC 对照」的可执行入口，不改 thresholds.yaml）。"""
    variant = _planb_profile_variant(planb_profile) if planb_profile else None
    _optin_prefiltered_universe()
    # 显式 as_of 时以其为窗口右端（可复现的历史窗口 / 两窗对照）；缺省取库内 MAX。
    as_of_source = "显式 --as-of" if as_of else "库内 MAX"
    as_of = as_of or _as_of(db)
    dates_all = _window_dates(db, as_of, window_days, lookback)
    if len(dates_all) < lookback + window_days + horizon:
        raise ValueError(f"库内 {as_of} 之前交易日仅 {len(dates_all)} 天，不足窗口")

    universe_note = f"top{top_n}-liquidity(avg amount>= {min_avg_amount:.0f})"
    if symbols_file:
        symbols = [
            ln.strip()
            for ln in Path(symbols_file).read_text().splitlines()
            if ln.strip()
        ]
        universe_note = f"symbols-file({len(symbols)})"
    else:
        symbols = _top_universe(db, as_of, top_n, min_avg_amount)

    raw = load_prices(db, dates_all[0], as_of, symbols)
    # close 保留完整序列（lookback 前缀 + 缓冲仅供打分回溯，不截断索引布局）
    close = raw.pivot_table(
        index="trade_date", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    vals = close.to_numpy(dtype=float)
    fwd_ret = pd.DataFrame(
        np.vstack(
            [
                vals[horizon:] / vals[:-horizon] - 1.0,
                np.full((horizon, vals.shape[1]), np.nan),
            ]
        ),
        index=close.index,
        columns=close.columns,
    )
    factor_order, factor_objs = _build_factor_objs(
        extra_factors or [], lookback, variant=variant
    )
    by_symbol: dict[str, pd.DataFrame] = {}
    for sym, g in raw.sort_values("trade_date").groupby("symbol", sort=False):
        d = g[["trade_date", "open", "high", "low", "close", "volume"]].rename(
            columns={"trade_date": "date"}
        )
        if len(d) >= lookback + 5:
            by_symbol[str(sym)] = d.reset_index(drop=True)

    # 截面窗 = 最近 window_days 交易日（lookback 前缀不越界，尾部 horizon 天剔除）
    total = len(close.index)
    start_i = max(lookback, total - window_days)
    dates = [close.index[i] for i in range(start_i, total - horizon, step)]
    ic_series: dict[str, list[float]] = {n: [] for n in factor_order}
    for d in dates:
        data = {
            sym: df[df["date"] <= d].tail(lookback + 10)
            for sym, df in by_symbol.items()
            if (df["date"] <= d).any() and df["date"].iloc[-1] >= d
        }
        if len(data) < 50:
            continue
        fwd = fwd_ret.loc[d].dropna()
        for name in factor_order:
            try:
                scores = pd.Series(factor_objs[name].calculate_score(data), dtype=float)
            except Exception:  # 单截面单因子失败不影响其它（同 factor_ic_diagnosis）
                scores = pd.Series(dtype=float)
            ic_series[name].append(_ic(scores, fwd))

    factors_out: dict[str, dict] = {}
    for name in factor_order:
        overall = _stats(ic_series[name])
        recent = _stats(ic_series[name][-_REC_SPAN:])
        factors_out[name] = {**overall, "recent": {**recent, "span": _REC_SPAN}}

    result = {
        "as_of": as_of,
        "as_of_source": as_of_source,
        "window_days": window_days,
        "horizon": horizon,
        "step": step,
        "lookback": lookback,
        "n_sections": len(dates),
        "universe": {"note": universe_note, "n_symbols": len(by_symbol)},
        # 注入来源观测（proposal-only，不写回打分/排序/下单）：缺省 wf001，planb 跑 = 该 profile。
        "ic_profile": planb_profile or "wf001",
        # §3.4 全项目禁裸 datetime.now()：统一走项目时钟 now_shanghai()，再归一 UTC。
        "generated_at": now_shanghai().astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "factors": factors_out,
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "factor_ic_latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    hist = {
        "as_of": as_of,
        "run_at": result["generated_at"],
        "factors": {n: v["mean"] for n, v in factors_out.items()},
        # 口径B 触发巡检（planb_b_trigger.py）逐日 |t|≥2 主判需要的 per-day t
        # （旧 factors 均值字段语义不动，向后兼容）。
        "t": {n: v.get("t") for n, v in factors_out.items()},
    }
    with (out / "ic_history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(hist, ensure_ascii=False) + "\n")
    _write_report(out / "report.md", result)
    if write_ready:
        (out / "IC_READY").write_text(result["generated_at"], encoding="utf-8")
    print(f"[ok] IC 诊断完成: as_of={as_of} 截面={len(dates)} → {out}")
    return result


def _write_report(path: Path, result: dict) -> None:
    """人读版（与 factor_ic_diagnosis 同口径判读）。"""
    lines = [
        "# 滚动窗口因子 IC 诊断",
        f"- as-of: `{result['as_of']}`（{result.get('as_of_source', '库内 MAX')}，窗口右端截断，未读未来数据）",
        f"- 窗口: 近 {result['window_days']} 交易日 / 每 {result['step']} 日一截面 / "
        f"horizon={result['horizon']} / lookback={result['lookback']}",
        f"- 标的池: {result['universe']['note']}（{result['universe']['n_symbols']} 只）",
        f"- 截面数: {result['n_sections']}",
        "",
        "| 因子 | IC 均值 | ICIR | t | IC>0 占比 | 近 20 截面 IC | 判读 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, s in result["factors"].items():
        rec = s["recent"]
        m, t = s["mean"], s["t"]
        if abs(m) < 0.02 and abs(t) < 2:
            verdict = "无预测力（噪音）"
        elif m < 0 and t <= -2:
            verdict = "反向有效（越按它选越亏）"
        elif m > 0 and t >= 2:
            verdict = "正向有效"
        else:
            verdict = "弱信号/不显著"
        if m * rec["mean"] < 0:
            verdict += " + 短期口径翻向"
        lines.append(
            f"| {name} | {m:+.4f} | {s['icir']:.3f} | {t:.2f} "
            f"| {s['pos_rate']:.2f} | {rec['mean']:+.4f} | {verdict} |"
        )
    lines += [
        "",
        "⚠️ 纯诊断层：不产出信号、不进入回测/选股/上线，不写回打分/排序/下单。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _optin_prefiltered_universe()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--window-days", type=int, default=90)
    ap.add_argument(
        "--as-of",
        default=None,
        help="窗口右端交易日（YYYY-MM-DD）。缺省取库内 MAX。用于可复现的历史窗口/两窗对照。",
    )
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--max-symbols", type=int, default=300)
    ap.add_argument("--min-avg-amount", type=float, default=0.0)
    ap.add_argument("--symbols-file", default=None)
    ap.add_argument(
        "--extra-factors", default=os.environ.get("AQSP_IC_EXTRA_FACTORS", "")
    )
    ap.add_argument(
        "--planb-profile",
        default=None,
        help="方案 B 注入：planb_v1/v2/v3（composite/基础三因子按该 profile 代表臂 7 维权重打分，"
        "主判据「改后权重双窗 IC 对照」入口）。缺省 = 钉 WF001（现状零漂移，不改 yaml）。",
    )
    a = ap.parse_args(argv)
    run(
        a.db,
        window_days=a.window_days,
        as_of=a.as_of,
        lookback=a.lookback,
        horizon=a.horizon,
        step=a.step,
        top_n=a.max_symbols,
        min_avg_amount=a.min_avg_amount,
        symbols_file=a.symbols_file,
        extra_factors=[
            x for x in (a.extra_factors or "").split(",") if x in _EXTRA_CHOICES
        ],
        out_dir=a.output_dir,
        planb_profile=a.planb_profile,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
