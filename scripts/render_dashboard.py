#!/usr/bin/env python3
"""Render a static AQSP dashboard from the latest run outputs."""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from aqsp.core.time import now_shanghai


@dataclass(frozen=True)
class LedgerStats:
    total: int
    pending: int
    validated: int
    not_executable: int
    win_rate: float | None
    avg_return_pct: float | None
    latest_signal_date: str
    thresholds_version: str


@dataclass(frozen=True)
class PaperStats:
    total: int
    open_positions: int
    closed: int
    not_executable: int
    pending_entry: int
    avg_return_pct: float | None


def _safe_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def read_ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_paper_rows(path: Path) -> list[dict[str, Any]]:
    return read_ledger_rows(path)


def summarize_ledger(rows: list[dict[str, Any]]) -> LedgerStats:
    validated_rows = [r for r in rows if r.get("status") == "validated"]
    executable_validated = [
        r for r in validated_rows if r.get("status") != "not_executable"
    ]
    returns = [
        value
        for value in (_safe_float(r.get("return_pct")) for r in executable_validated)
        if value is not None
    ]
    wins = sum(1 for r in executable_validated if bool(r.get("win")))
    win_rate = wins / len(executable_validated) if executable_validated else None
    avg_return = sum(returns) / len(returns) if returns else None
    return LedgerStats(
        total=len(rows),
        pending=sum(1 for r in rows if r.get("status") == "pending"),
        validated=len(validated_rows),
        not_executable=sum(1 for r in rows if r.get("status") == "not_executable"),
        win_rate=win_rate,
        avg_return_pct=avg_return,
        latest_signal_date=str(rows[-1].get("signal_date", "")) if rows else "",
        thresholds_version=str(rows[-1].get("thresholds_version", "")) if rows else "",
    )


def summarize_paper(rows: list[dict[str, Any]]) -> PaperStats:
    closed_rows = [r for r in rows if r.get("status") == "closed"]
    returns = [
        value
        for value in (_safe_float(r.get("return_pct")) for r in closed_rows)
        if value is not None
    ]
    avg_return = sum(returns) / len(returns) if returns else None
    return PaperStats(
        total=len(rows),
        open_positions=sum(1 for r in rows if r.get("status") == "open"),
        closed=len(closed_rows),
        not_executable=sum(1 for r in rows if r.get("status") == "not_executable"),
        pending_entry=sum(1 for r in rows if r.get("status") == "pending_entry"),
        avg_return_pct=avg_return,
    )


def read_candidates(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except EmptyDataError:
        return []
    return [dict(row) for row in df.to_dict(orient="records")]


def latest_candidate_date(candidates: list[dict[str, str]]) -> str:
    dates = sorted({row.get("date", "") for row in candidates if row.get("date")})
    return dates[-1] if dates else ""


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "冷启动"
    return f"{value:.2%}"


def _fmt_num(value: str | float | None) -> str:
    if value in ("", None):
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _fmt_return(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    cls = "pos" if number > 0 else "neg" if number < 0 else ""
    return f"<span class='{cls}'>{number:.2f}</span>"


def _candidate_cards(candidates: list[dict[str, str]]) -> str:
    if not candidates:
        return '<section class="empty">本次没有候选股，或数据源未成功返回。</section>'
    cards: list[str] = []
    for idx, row in enumerate(candidates, 1):
        symbol = html.escape(row.get("symbol", ""))
        name = html.escape(row.get("name", ""))
        score = _fmt_num(row.get("score"))
        rating = html.escape(row.get("rating", ""))
        strategies = html.escape(row.get("strategies", ""))
        reasons = html.escape(row.get("reasons", ""))
        risks = html.escape(row.get("risks", ""))
        cards.append(
            f"""
            <article class="card">
              <div class="rank">#{idx}</div>
              <div>
                <h3>{symbol} <span>{name}</span></h3>
                <p class="score">{score}<small> / {rating}</small></p>
              </div>
              <dl>
                <dt>策略</dt><dd>{strategies or "-"}</dd>
                <dt>买点</dt><dd>{_fmt_num(row.get("ideal_buy"))}</dd>
                <dt>收盘</dt><dd>{_fmt_num(row.get("close"))}</dd>
                <dt>止损</dt><dd>{_fmt_num(row.get("stop_loss"))}</dd>
                <dt>止盈</dt><dd>{_fmt_num(row.get("take_profit"))}</dd>
                <dt>仓位</dt><dd>{html.escape(row.get("position", "") or "-")}</dd>
              </dl>
              <p class="reason">{reasons or "无"}</p>
              <p class="risk">{risks or "无明显风险标签"}</p>
            </article>
            """
        )
    return "\n".join(cards)


def _recent_rows(rows: list[dict[str, Any]]) -> str:
    recent = list(reversed(rows[-12:]))
    if not recent:
        return "<tr><td colspan='5'>暂无 ledger 记录</td></tr>"
    out = []
    for row in recent:
        out.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('signal_date', '')))}</td>"
            f"<td>{html.escape(str(row.get('symbol', '')))}</td>"
            f"<td>{_fmt_num(row.get('score'))}</td>"
            f"<td>{html.escape(str(row.get('status', '')))}</td>"
            f"<td>{_fmt_return(row.get('return_pct'))}</td>"
            "</tr>"
        )
    return "\n".join(out)


def _paper_rows(rows: list[dict[str, Any]]) -> str:
    recent = list(reversed(rows[-10:]))
    if not recent:
        return "<tr><td colspan='6'>暂无虚拟盘记录</td></tr>"
    out = []
    for row in recent:
        reason = (
            row.get("not_executable_reason")
            or row.get("exit_reason")
            or (
                f"等待 {row.get('signal_date', '')} 次日开盘"
                if row.get("status") == "pending_entry"
                else ""
            )
        )
        out.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('symbol', '')))}</td>"
            f"<td>{html.escape(str(row.get('status', '')))}</td>"
            f"<td>{html.escape(str(row.get('entry_date', '')))}</td>"
            f"<td>{_fmt_num(row.get('entry_price'))}</td>"
            f"<td>{_fmt_return(row.get('return_pct'))}</td>"
            f"<td>{html.escape(str(reason))}</td>"
            "</tr>"
        )
    return "\n".join(out)


def render_dashboard(
    candidates: list[dict[str, str]],
    rows: list[dict[str, Any]],
    title: str,
    paper_rows: list[dict[str, Any]] | None = None,
) -> str:
    stats = summarize_ledger(rows)
    paper = summarize_paper(paper_rows or [])
    generated_at = now_shanghai().isoformat(timespec="seconds")
    today = now_shanghai().date().isoformat()
    candidate_date = latest_candidate_date(candidates)
    safe_title = html.escape(title)
    latest_date = html.escape(stats.latest_signal_date or "暂无")
    display_date = html.escape(candidate_date or "暂无")
    thresholds_version = html.escape(stats.thresholds_version or "未知")
    warning = ""
    if not candidates:
        warning = "暂无真实候选输出。请先成功运行 aqsp run 或提供最新 CSV。"
    elif candidate_date and candidate_date != today:
        warning = (
            f"当前候选数据日期为 {html.escape(candidate_date)}, "
            f"不是今天 {html.escape(today)}。不要按这个页面下单。"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{
      --ink: #162018;
      --muted: #687568;
      --paper: #f7f4ec;
      --card: rgba(255, 255, 252, .90);
      --green: #1f7a4d;
      --amber: #b86b1d;
      --line: rgba(22, 32, 24, .14);
      --red: #b44836;
      --warn: #fff1c2;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Iowan Old Style", "Songti SC", "Noto Serif CJK SC", serif;
      background:
        radial-gradient(circle at 14% 8%, rgba(31, 122, 77, .16), transparent 26rem),
        radial-gradient(circle at 86% 14%, rgba(184, 107, 29, .15), transparent 22rem),
        linear-gradient(135deg, #fbf6ea 0%, #e2ead9 48%, #f5ead4 100%);
      min-height: 100vh;
    }}
    header {{ padding: 56px clamp(20px, 6vw, 80px) 26px; }}
    h1 {{ font-size: clamp(36px, 7vw, 88px); line-height: .92; margin: 0; letter-spacing: -.05em; }}
    .sub {{ color: var(--muted); font-size: 16px; margin-top: 18px; }}
    .meta {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 22px; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 8px 12px; background: rgba(255,255,255,.42); color: var(--muted); }}
    .warning {{ margin: 0 clamp(20px, 6vw, 80px) 22px; padding: 14px 18px; border: 1px solid rgba(184,107,29,.32); background: var(--warn); border-radius: 18px; color: #7a4a12; }}
    .stats, .grid {{ display: grid; gap: 16px; padding: 0 clamp(20px, 6vw, 80px) 24px; }}
    .stats {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
    .stat, .card, .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 24px 70px rgba(28, 45, 31, .12);
      backdrop-filter: blur(14px);
    }}
    .stat {{ padding: 20px; }}
    .stat b {{ display: block; font-size: 30px; }}
    .stat span {{ color: var(--muted); }}
    .grid {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); align-items: start; }}
    .card {{ padding: 22px; position: relative; overflow: hidden; }}
    .rank {{ color: var(--amber); font-weight: 800; }}
    h3 {{ margin: 8px 0 0; font-size: 24px; }}
    h3 span {{ color: var(--muted); font-size: 17px; }}
    .score {{ font-size: 42px; margin: 8px 0 14px; color: var(--green); }}
    .score small {{ font-size: 15px; color: var(--muted); }}
    dl {{ display: grid; grid-template-columns: 56px 1fr; gap: 8px; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; }}
    .reason {{ line-height: 1.7; }}
    .risk {{ color: #9b3f2f; }}
    .pos {{ color: var(--green); font-weight: 700; }}
    .neg {{ color: var(--red); font-weight: 700; }}
    .panel {{ margin: 0 clamp(20px, 6vw, 80px) 56px; padding: 22px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 12px; border-bottom: 1px solid var(--line); text-align: left; }}
    .empty {{ margin: 0 clamp(20px, 6vw, 80px); padding: 24px; border: 1px dashed var(--line); border-radius: 20px; }}
  </style>
</head>
<body>
  <header>
    <h1>{safe_title}</h1>
    <p class="sub">生成时间 {generated_at}。仅供研究，不构成投资建议；下单仍由人决策。</p>
    <div class="meta">
      <span class="pill">最新信号日 {latest_date}</span>
      <span class="pill">候选数据日 {display_date}</span>
      <span class="pill">阈值版本 {thresholds_version}</span>
      <span class="pill">候选数 {len(candidates)}</span>
    </div>
  </header>
  {"<section class='warning'>" + warning + "</section>" if warning else ""}
  <section class="stats">
    <div class="stat"><b>{stats.total}</b><span>ledger 总记录</span></div>
    <div class="stat"><b>{stats.pending}</b><span>待验证信号</span></div>
    <div class="stat"><b>{stats.validated}</b><span>已验证信号</span></div>
    <div class="stat"><b>{stats.not_executable}</b><span>不可成交样本</span></div>
    <div class="stat"><b>{_fmt_pct(stats.win_rate)}</b><span>已验证胜率</span></div>
    <div class="stat"><b>{_fmt_num(stats.avg_return_pct)}</b><span>平均收益 pct</span></div>
    <div class="stat"><b>{paper.open_positions}</b><span>虚拟持仓</span></div>
    <div class="stat"><b>{paper.closed}</b><span>虚拟平仓</span></div>
    <div class="stat"><b>{paper.not_executable}</b><span>虚拟不可成交</span></div>
    <div class="stat"><b>{paper.pending_entry}</b><span>等待入场数据</span></div>
    <div class="stat"><b>{_fmt_num(paper.avg_return_pct)}</b><span>虚拟平均收益 pct</span></div>
  </section>
  <main class="grid">
    {_candidate_cards(candidates)}
  </main>
  <section class="panel">
    <h2>最近信号</h2>
    <table>
      <thead><tr><th>日期</th><th>代码</th><th>评分</th><th>状态</th><th>收益 pct</th></tr></thead>
      <tbody>{_recent_rows(rows)}</tbody>
    </table>
  </section>
  <section class="panel">
    <h2>虚拟盘</h2>
    <table>
      <thead><tr><th>代码</th><th>状态</th><th>入场日</th><th>入场价</th><th>收益 pct</th><th>原因</th></tr></thead>
      <tbody>{_paper_rows(paper_rows or [])}</tbody>
    </table>
  </section>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="reports/close.csv")
    parser.add_argument("--ledger", default="data/predictions.jsonl")
    parser.add_argument("--paper-ledger", default="data/paper_trades.jsonl")
    parser.add_argument("--output", default="dist/dashboard/index.html")
    parser.add_argument("--title", default="AQSP 量化选股面板")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_dashboard(
        read_candidates(Path(args.csv)),
        read_ledger_rows(Path(args.ledger)),
        args.title,
        read_paper_rows(Path(args.paper_ledger)),
    )
    output.write_text(html_text, encoding="utf-8")
    print(f"dashboard={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
