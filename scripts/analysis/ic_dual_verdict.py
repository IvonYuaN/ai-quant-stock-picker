#!/usr/bin/env python3
"""ic_dual_verdict.py — 每日双窗因子 IC 滚动判决（runner 侧 · proposal-only）。

方案 B 判决（09-28 不采纳）的后续监控面：把一次性手工双窗对照做成每日自动。
口径（权威 = outputs/因子族方案B提案_2026-09-28.md §六，不得擅改）：
  - 两窗相邻不重叠、等长（默认各 365 交易日 / step=5 ⇒ 各 73 截面）：
      B 窗 = 截至库 MAX(trade_date)（右端截断，未读未来数据）
      A 窗 = 截至 MAX − 365 交易日
  - 因子达标 = 两窗 IC 同号 且 双 |t|≥2（且两窗截面数均 ≥ MIN_SECTIONS）；
  - 达标累积 = 连续 N 个数据日（ledger 按 as_of_b 去重计数——数据面停更期
    同 as_of 多次跑批只算 1 日）判决均达标 ⇒ 记录 "revisit_family" 事件。

🔴 红线：
  - **proposal-only**：产物 = ledger + 端点只读 + 报告。"revisit_family" 只是
    事件记录（提醒回换族流程），**无下游消费者自动改参数/权重/下单逻辑**。
  - 复用 ``ic_diagnosis.run()`` 双窗口径（同打分口径，禁止复刻截面打分）；
    双窗跑批写 scratch 子目录（``write_ready=False``），**不污染生产单窗
    ``ic_history.jsonl`` / ``IC_READY``**。
  - 3 年红线：双窗总深 2×window_days ≤ 730（3y 库），绝不放宽。
  - 绝不让 step < horizon（截面重叠虚高 t）——入口处显式守卫。
  - 只读库（sqlite ``?mode=ro`` 继承自 ic_diagnosis），零写点、零 LLM。

用法（runner cron，紧随单窗诊断之后，独立 load 守卫/超时预算）：
  [PYTHONPATH=<release>/src:<release>] \
  python scripts/ic_dual_verdict.py --db <3y库> --output-dir <OUT_DIR> \
      [--window-days 365] [--step 5] [--extra-factors htf,mr,volume,rps]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import timezone
from pathlib import Path

from aqsp.core.time import now_shanghai
from scripts.analysis.ic_diagnosis import _as_of, _distinct_dates, run

# 口径常量（方案 B §六；改须走 PR 说明，禁「亏损就调参」式擅改）
THREE_YEAR_REDLINE_DAYS = 730  # 3y 库可用截面上限对应深度
DEFAULT_WINDOW_DAYS = 365  # 各窗 365 交易日 / step5 ⇒ 73 截面
DEFAULT_STREAK_N = 5  # 连续 N 个数据日达标 ⇒ revisit_family 事件
MIN_SECTIONS = 2  # 单窗最低截面数（_stats n<2 全 NaN 的门槛）


def _window_a_as_of(db: str, as_of_b: str, window_days: int) -> str:
    """A 窗右端 = B 窗右端（库 MAX）往前 window_days 个交易日。

    ``_distinct_dates(db, as_of_b, window_days + 1)`` = ≤MAX 的最近 window_days+1
    个交易日升序；取最旧者（index 0）⇒ 与 B 窗（最近 window_days 日）相邻不重叠。
    库深度不足（交易日 < window_days+1）⇒ 拒绝：A 窗右端落在浅层会让 A/B 重叠、
    口径失真（宁可显式失败，不静默降级）。
    """
    days = _distinct_dates(db, as_of_b, window_days + 1)
    if len(days) < window_days + 1:
        raise ValueError(
            f"库内 {as_of_b} 前交易日仅 {len(days)} 天，不足 {window_days}+1，切不出双窗"
        )
    return days[0]


def _sign(v: float) -> int:
    """IC 均值符号；NaN / 0 记 0（不构成方向）。"""
    if v != v:  # NaN
        return 0
    if abs(v) < 1e-12:
        return 0
    return 1 if v > 0 else -1


def verdict_for_factor(stats_a: dict, stats_b: dict) -> dict:
    """单因子双窗判决（纯函数：两窗 run() 的 per-factor stats dict → 判决）。

    达标（hit）= 两窗同号 且 双 |t|≥2 且 两窗截面数均 ≥ MIN_SECTIONS。
    """
    ma, ta, na = stats_a.get("mean"), stats_a.get("t"), stats_a.get("n", 0)
    mb, tb, nb = stats_b.get("mean"), stats_b.get("t"), stats_b.get("n", 0)

    def _sig(v) -> int:
        return 0 if v is None else _sign(float(v))

    def _abs_ok(v) -> bool:
        return v is not None and v == v and abs(v) >= 2.0

    sign_match = _sig(ma) != 0 and _sig(ma) == _sig(mb)
    dual_significant = (
        _abs_ok(ta)
        and _abs_ok(tb)
        and na >= MIN_SECTIONS
        and nb >= MIN_SECTIONS
    )
    return {
        "mean_a": ma,
        "t_a": ta,
        "n_a": na,
        "mean_b": mb,
        "t_b": tb,
        "n_b": nb,
        "sign_match": sign_match,
        "dual_significant": dual_significant,
        "hit": bool(sign_match and dual_significant),
    }


def factor_streak(ledger_rows: list[dict], factor: str) -> int:
    """某因子从最近一个数据日往回的「连续达标数据日数」。

    ledger 按 (as_of_b, run_at) 升序；**按 as_of_b 去重**（同数据日多次跑批
    只算 1 日，静态期保护）。某日未达标即截断。
    """
    per_day: dict[str, dict] = {}
    for row in sorted(
        ledger_rows, key=lambda r: (str(r.get("as_of_b", "")), str(r.get("run_at", "")))
    ):
        key = str(row.get("as_of_b", ""))
        if not key or key == "None":
            continue
        per_day[key] = row  # 同日取最晚 run_at
    streak = 0
    for day in reversed(sorted(per_day)):
        verdicts = per_day[day].get("factors", {}) or {}
        if verdicts.get(factor, {}).get("hit"):
            streak += 1
        else:
            break
    return streak


def _read_ledger(path: Path) -> list[dict]:
    """读 ledger（fail-soft：文件缺失/单行损坏 ⇒ 跳过该行，绝不崩）。"""
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def run_dual_verdict(
    db: str,
    *,
    out_dir: str | Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    step: int = 5,
    horizon: int = 3,
    lookback: int = 60,
    top_n: int = 300,
    min_avg_amount: float = 0.0,
    extra_factors: list[str] | None = None,
    streak_n: int = DEFAULT_STREAK_N,
    write_ready: bool = True,
) -> dict:
    """双窗判决主流程，返回与 dual_window_latest.json 同构的结构（供测试断言）。

    红线守卫：step≥horizon（重叠虚高 t）；2×window_days ≤ 3 年红线（不碰 5y）。
    双窗各跑一次 run()（scratch 子目录、write_ready=False），互验 B 窗 73 截面
    = 单窗 146 截面「近 73」同一口径（同 run() ⇒ 构造上零口径漂移）。
    """
    if step < horizon:
        raise ValueError(f"step({step}) < horizon({horizon})：截面重叠虚高 t，拒绝")
    if 2 * window_days > THREE_YEAR_REDLINE_DAYS:
        raise ValueError(
            f"双窗总深 {2 * window_days} 日超 3 年红线 {THREE_YEAR_REDLINE_DAYS}，拒绝"
        )

    as_of_b = _as_of(db)
    as_of_a = _window_a_as_of(db, as_of_b, window_days)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scratch = out / "scratch_dual"
    for sub in ("a", "b"):  # 每次跑批清 scratch，防 ic_history 子文件累积
        shutil.rmtree(scratch / sub, ignore_errors=True)

    common = dict(
        window_days=window_days,
        step=step,
        horizon=horizon,
        lookback=lookback,
        top_n=top_n,
        min_avg_amount=min_avg_amount,
        extra_factors=extra_factors,
        write_ready=False,
    )
    rb = run(db, as_of=as_of_b, out_dir=str(scratch / "b"), **common)
    ra = run(db, as_of=as_of_a, out_dir=str(scratch / "a"), **common)

    verdicts = {
        name: verdict_for_factor(ra["factors"].get(name, {}), rb["factors"].get(name, {}))
        for name in rb["factors"]
    }
    hits = [n for n, v in verdicts.items() if v["hit"]]

    run_at = now_shanghai().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {
        "run_at": run_at,
        "as_of_a": as_of_a,
        "as_of_b": as_of_b,
        "window_days": window_days,
        "step": step,
        "n_sections_a": ra["n_sections"],
        "n_sections_b": rb["n_sections"],
        "factors": verdicts,
        "hits": hits,
    }
    ledger_path = out / "dual_window_history.jsonl"
    prev_rows = _read_ledger(ledger_path)
    all_rows = prev_rows + [row]
    streaks = {n: factor_streak(all_rows, n) for n in verdicts}
    event = "revisit_family" if any(v >= streak_n for v in streaks.values()) else None
    # next_candidate = 未达标但连续日数最高者（供「快达标」提示，无副作用）
    pending = {n: s for n, s in streaks.items() if 0 < s < streak_n}
    next_candidate = max(pending, key=pending.get) if pending else None

    result = {
        **row,
        "streak_n": streak_n,
        "streaks": streaks,
        "event": event,
        "next_candidate": next_candidate,
        "generated_at": run_at,
    }
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "dual_window_latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_dual_report(out / "dual_report.md", result)
    if write_ready:
        (out / "IC_READY_DUAL").write_text(run_at, encoding="utf-8")
    print(
        f"[ok] 双窗 IC 判决: A={as_of_a} B={as_of_b} 截面={ra['n_sections']}/"
        f"{rb['n_sections']} 达标={hits or '无'} → {out}"
    )
    return result


def _fmt(v, spec: str = "+.4f") -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return str(v)


def _write_dual_report(path: Path, r: dict) -> None:
    lines = [
        "# 双窗因子 IC 滚动判决（proposal-only）",
        f"- 窗口 A: 截至 `{r['as_of_a']}`（前 {r['window_days']} 交易日）｜"
        f"窗口 B: 截至 `{r['as_of_b']}`（库 MAX）｜step={r['step']}｜"
        f"各 {r['n_sections_a']}/{r['n_sections_b']} 截面（相邻不重叠，均未读未来数据）",
        f"- 判据: 两窗同号 且 双 |t|≥2；连续 {r['streak_n']} 个数据日达标 ⇒ "
        f"revisit_family 事件（**仅记录，不自动改参数/权重/下单**）",
        "",
        "| 因子 | A 窗 IC | A t | B 窗 IC | B t | 方向一致 | 双\\|t\\|≥2 | 达标 | 连续达标日 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, v in r["factors"].items():
        mark = "✅" if v["hit"] else ("⚠️" if v["sign_match"] else "❌")
        lines.append(
            f"| {name} | {_fmt(v['mean_a'])} | {_fmt(v['t_a'], '.2f')} | "
            f"{_fmt(v['mean_b'])} | {_fmt(v['t_b'], '.2f')} | "
            f"{'是' if v['sign_match'] else '否'} | "
            f"{'是' if v['dual_significant'] else '否'} | {mark} | "
            f"{r['streaks'].get(name, 0)}/{r['streak_n']} |"
        )
    lines += [
        "",
        f"- 事件: **{r['event'] or '无'}**｜next_candidate: {r['next_candidate'] or '—'}",
        "⚠️ 纯诊断层：不产出信号、不进入回测/选股/上线，不写回打分/排序/下单。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument("--max-symbols", type=int, default=300)
    ap.add_argument("--min-avg-amount", type=float, default=0.0)
    ap.add_argument(
        "--extra-factors",
        default=os.environ.get("AQSP_IC_EXTRA_FACTORS", ""),
    )
    ap.add_argument("--streak-n", type=int, default=DEFAULT_STREAK_N)
    a = ap.parse_args(argv)
    extra = [x for x in (a.extra_factors or "").split(",") if x]
    run_dual_verdict(
        a.db,
        out_dir=a.output_dir,
        window_days=a.window_days,
        step=a.step,
        horizon=a.horizon,
        lookback=a.lookback,
        top_n=a.max_symbols,
        min_avg_amount=a.min_avg_amount,
        extra_factors=extra,
        streak_n=a.streak_n,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
