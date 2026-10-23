"""Read-only evidence for user positions; never a strategy or order engine."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.core.time import (
    _is_basic_trading_day,
    _load_basic_trading_calendar,
    market_hours,
    now_shanghai,
    to_shanghai,
)
from aqsp.web.live_candidate_view import LiveCandidateViewConfig


@dataclass(frozen=True)
class ContextEvidence:
    status: str
    summary: str
    as_of: str | None = None
    source: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class TObservationPlan:
    eligible: bool | None
    reason: str


@dataclass(frozen=True)
class PositionAssessment:
    stance: str
    title: str
    reason: str
    options: tuple[str, ...]
    t_plan: TObservationPlan


@dataclass(frozen=True)
class PositionDecisionContext:
    name: str
    opened_at: str | None
    holding_trade_days: int | None
    market: ContextEvidence
    sector: ContextEvidence
    stock: ContextEvidence
    assessment: PositionAssessment

    def to_dict(self) -> dict[str, Any]:
        """Serialize display evidence without changing portfolio or alert state."""
        return asdict(self)


def holding_trade_days(opened_at: object, *, today: date | None = None) -> int | None:
    """Count completed session transitions, not the risk engine's 0/1 T+1 flag.

    Opening day is zero. Unknown, future, non-session and malformed dates stay
    unknown; no guessed legacy opening date is written back to the portfolio.
    """
    current = today or now_shanghai().date()
    if not isinstance(opened_at, str):
        return None
    try:
        opened = date.fromisoformat(opened_at)
    except ValueError:
        return None
    if (
        opened.isoformat() != opened_at
        or opened > current
        or any(
            not _calendar_known(year) for year in range(opened.year, current.year + 1)
        )
        or not _is_basic_trading_day(opened)
    ):
        return None
    days = 0
    cursor = opened + timedelta(days=1)
    while cursor <= current:
        days += int(_is_basic_trading_day(cursor))
        cursor += timedelta(days=1)
    return days


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _source_date(value: object, current: datetime) -> tuple[date, str] | None:
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 8 and value.isdigit():
            parsed = datetime.strptime(value, "%Y%m%d").date()
            return (parsed, parsed.isoformat()) if parsed <= current.date() else None
        if len(value) == 10:
            parsed = date.fromisoformat(value)
            return (parsed, parsed.isoformat()) if parsed <= current.date() else None
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        stamp = to_shanghai(stamp)
        return (stamp.date(), stamp.isoformat()) if stamp <= current else None
    except ValueError:
        return None


def _calendar_known(year: int) -> bool:
    return any(day.year == year for day in _load_basic_trading_calendar())


def _session(current: datetime, *, completed: bool = False) -> date | None:
    """Use the project's authoritative local calendar without Tushare requests."""
    opening, closing = market_hours(current)
    day = current.date()
    if not _calendar_known(day.year):
        return None
    if _is_basic_trading_day(day) and current >= (closing if completed else opening):
        return day
    day -= timedelta(days=1)
    while _calendar_known(day.year):
        if _is_basic_trading_day(day):
            return day
        day -= timedelta(days=1)
    return None


def _unknown(summary: str, *, evidence: tuple[str, ...] = ()) -> ContextEvidence:
    return ContextEvidence("unknown", summary, evidence=evidence)


def _timely_quote(stamp: tuple[date, str], current: datetime) -> bool:
    if len(stamp[1]) == 10:
        return False
    opening, closing = market_hours(current)
    if _is_basic_trading_day(current.date()) and opening <= current < closing:
        return (
            current - datetime.fromisoformat(stamp[1])
            <= LiveCandidateViewConfig().max_age
        )
    return True


def load_cached_market_overview(
    cache_dir: str | Path | None = None, *, checked_at: datetime | None = None
) -> Mapping[str, Any] | None:
    """Read the existing overview cache without refresh, network, or file writes."""
    import market

    current = to_shanghai(checked_at or now_shanghai())
    root = Path(cache_dir or market._MARKET_CACHE_DIR)
    try:
        entry = json.loads((root / "overview.json").read_text(encoding="utf-8"))
        if not isinstance(entry, Mapping):
            return None
        timestamp = _number(entry.get("ts"))
        if timestamp is None or not 0 <= current.timestamp() - timestamp <= market._TTL:
            return None
        value = entry.get("val")
        return value if isinstance(value, Mapping) else None
    except (OSError, ValueError, TypeError):
        return None


def _market_context(
    overview: Mapping[str, Any] | None, current: datetime
) -> ContextEvidence:
    sentiment = overview.get("sentiment") if overview else None
    if not isinstance(sentiment, Mapping):
        return _unknown("市场背景未知：没有可用市场快照")
    stamp = _source_date(sentiment.get("as_of") or sentiment.get("date"), current)
    if stamp is None or stamp[0] != _session(current):
        return _unknown("市场背景未知：源交易日缺失、过期或错误")
    up, down = _number(sentiment.get("up")), _number(sentiment.get("down"))
    if up is None or down is None or min(up, down) < 0 or up + down <= 0:
        return _unknown("市场背景未知：涨跌家数不完整")
    breadth = str(sentiment.get("breadth") or "")
    status = {
        "冰点": "weak",
        "偏弱": "weak",
        "偏强": "strong",
        "普涨": "strong",
        "中性": "neutral",
    }.get(breadth, "unknown")
    evidence = (f"上涨 {up:g} 家，下跌 {down:g} 家",)
    return ContextEvidence(
        status,
        f"市场宽度{breadth or '尚未判定'}；{evidence[0]}",
        stamp[1],
        "既有市场情绪快照/东方财富",
        evidence,
    )


def _sector_context(
    holding: Mapping[str, Any],
    overview: Mapping[str, Any] | None,
    sector_snapshot: Mapping[str, Any] | None,
    current: datetime,
) -> ContextEvidence:
    industry = str(holding.get("industry") or holding.get("sector") or "").strip()
    if not industry:
        return _unknown("板块背景未知：持仓没有经数据源确认的行业归属")
    rows = overview.get("sectors", ()) if overview else ()
    candidate = sector_snapshot
    if candidate is None and isinstance(rows, Sequence) and not isinstance(rows, str):
        candidate = next(
            (
                row
                for row in rows
                if isinstance(row, Mapping) and str(row.get("name") or "") == industry
            ),
            None,
        )
    if (
        not isinstance(candidate, Mapping)
        or str(candidate.get("name") or "") != industry
    ):
        return _unknown(f"{industry}板块背景未知：没有匹配的板块快照")
    change = _number(candidate.get("change_pct", candidate.get("pct")))
    evidence = (f"{industry}涨跌幅 {change:+.2f}%",) if change is not None else ()
    stamp = _source_date(candidate.get("as_of") or candidate.get("date"), current)
    if (
        stamp is None
        or stamp[0] != _session(current)
        or not _timely_quote(stamp, current)
        or change is None
    ):
        return _unknown(
            f"{industry}板块背景未知：源时间或涨跌幅未验证", evidence=evidence
        )
    status = "falling" if change < 0 else "rising" if change > 0 else "flat"
    return ContextEvidence(
        status,
        evidence[0],
        stamp[1],
        str(candidate.get("source") or "既有板块行情"),
        evidence,
    )


def _stock_context(
    quote: Mapping[str, Any] | None, history: pd.DataFrame | None, current: datetime
) -> ContextEvidence:
    if quote:
        raw_time = quote.get("as_of")
        if not raw_time and quote.get("quote_time"):
            from astock import parse_gtimg_time

            raw_time = parse_gtimg_time(quote["quote_time"])
        stamp = _source_date(raw_time, current)
        change = _number(quote.get("change_pct"))
        if (
            stamp
            and stamp[0] == _session(current)
            and _timely_quote(stamp, current)
            and change is not None
        ):
            status = "falling" if change < 0 else "rising" if change > 0 else "flat"
            return ContextEvidence(
                status,
                f"个股当日涨跌幅 {change:+.2f}%",
                stamp[1],
                str(quote.get("source") or "腾讯行情"),
                (f"当日涨跌幅 {change:+.2f}%",),
            )
    if (
        history is None
        or history.empty
        or not {"date", "close"}.issubset(history.columns)
    ):
        return _unknown("个股趋势未知：缺少有源时间的行情及不复权日线")
    if str(history.attrs.get("price_mode") or "raw") not in {"raw", ""}:
        return _unknown("个股趋势未知：复权口径不是不复权日线")
    dates = pd.to_datetime(history["date"], errors="coerce")
    if dates.isna().any() or (dates.dt.date > current.date()).any():
        return _unknown("个股趋势未知：日线日期无效或包含未来日期")
    frame = (
        history.assign(_date=dates.dt.date)
        .sort_values("_date")
        .drop_duplicates("_date")
    )
    completed = _session(current, completed=True)
    if completed is None:
        return _unknown("个股趋势未知：本地交易日历未覆盖当前交易日")
    frame = frame[frame["_date"] <= completed]
    if len(frame) < 2 or frame["_date"].iloc[-1] != completed:
        return _unknown("个股趋势未知：已完成日线不足或过期")
    previous, latest = (_number(value) for value in frame["close"].tail(2))
    if previous is None or latest is None or min(previous, latest) <= 0:
        return _unknown("个股趋势未知：日线价格无效")
    change = (latest / previous - 1) * 100
    if not math.isfinite(change):
        return _unknown("个股趋势未知：日线涨跌幅无法有效复算")
    status = "falling" if change < 0 else "rising" if change > 0 else "flat"
    source = str(
        history.attrs.get("source_name")
        or history.attrs.get("source")
        or "持仓检查不复权日线"
    )
    return ContextEvidence(
        status,
        f"最近已完成交易日涨跌幅 {change:+.2f}%（非当前分时）",
        frame["_date"].iloc[-1].isoformat(),
        source,
        (f"前收 {previous:g}，收盘 {latest:g}",),
    )


def build_position_context(
    holding: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    quote: Mapping[str, Any] | None = None,
    stock_history: pd.DataFrame | None = None,
    market_overview: Mapping[str, Any] | None = None,
    sector_snapshot: Mapping[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> PositionDecisionContext:
    """Explain evidence and conditional options without replacing hard risk rules.

    Inputs are already-read tenant positions, matching reminders and existing
    source artifacts. No loss percentage alone implies a sale or a T trade.
    """
    current = to_shanghai(checked_at or now_shanghai())
    code = str(holding.get("code") or holding.get("symbol") or "")
    matching = tuple(
        event
        for event in events
        if str(event.get("symbol") or "") == code
        and (
            not event.get("position_id")
            or not holding.get("position_id")
            or event["position_id"] == holding["position_id"]
        )
    )
    hard_risk = any(event.get("level") == "L1" for event in matching)
    market_context = _market_context(market_overview, current)
    sector_context = _sector_context(holding, market_overview, sector_snapshot, current)
    stock_context = _stock_context(quote, stock_history, current)
    name = str((quote or {}).get("name") or holding.get("name") or "")
    if name == code:
        name = ""
    opened = holding.get("opened_at")
    days = holding_trade_days(opened, today=current.date())
    context_dates = tuple(
        _source_date(item.as_of, current)
        for item in (market_context, sector_context, stock_context)
    )
    same_session = all(
        stamp is not None and stamp[0] == _session(current) for stamp in context_dates
    )
    joint_weakness = (
        same_session
        and market_context.status == "weak"
        and sector_context.status == "falling"
        and stock_context.status == "falling"
    )
    if hard_risk:
        stance, title = "risk_review", "止损规则已触发，复核风险与仓位选择"
        cost = _number(holding.get("cost"))
        price = _number((quote or {}).get("price", holding.get("price")))
        triggered = next(event for event in matching if event.get("level") == "L1")
        threshold = _number(triggered.get("threshold_price", triggered.get("threshold_at")))
        facts = []
        if cost is not None and cost > 0 and price is not None and price > 0:
            facts.append(f"成本 {cost:.4f}，检查价 {price:.4f}，浮动盈亏 {(price / cost - 1):+.1%}")
        if threshold is not None and threshold > 0:
            facts.append(f"已低于设定的成本止损线 {threshold:.4f}")
        reason = "；".join(facts + ["L1 硬风险仍生效，需复核仓位；这不等于必须一次性清仓，也不能仅以深亏或做 T 为由忽略风险。"])
        if joint_weakness:
            title = "止损规则叠加大盘、板块和个股走弱，优先复核减仓或退出"
            reason += " 三项同交易日弱势证据一致，继续等反弹或做 T 的依据不足。"
        else:
            reason += " 尚无大盘、板块、个股同日同步走弱的完整证据；风险提醒保留，是否持有、减仓或做 T 需按可承受损失与后续条件复核。"
    elif joint_weakness:
        stance, title = "exit_review", "市场、所属板块与个股同向走弱，复核减仓或退出"
        reason = (
            "三个已注明来源和时点的观察均偏弱；这是风险复核证据，不是自动卖出指令。"
        )
    else:
        stance, title = "observe", "结合已触发规则复核，持有或减仓需看后续证据"
        reason = "单项阈值或账面亏损不足以说明现在必须全部卖出；市场、板块、个股未形成完整同向弱势证据。"
    sellable = _number(holding.get("sellable_shares"))
    if sellable is not None and sellable <= 0:
        t_plan = TObservationPlan(
            False, "当前无可卖持仓，受T+1限制，不能执行卖出再回补。"
        )
    elif joint_weakness:
        t_plan = TObservationPlan(
            False, "大盘、所属板块与个股均有同交易日走弱证据；先复核风险处置，不用做T延后判断。"
        )
    else:
        t_plan = TObservationPlan(
            None,
            "可记录做T观察意向，但当前没有经验证的日内区间、流动性及往返成本证据，不能判断适合执行；亏损或止损线触发本身都不是做T依据。",
        )
    options = (
        "继续持有：须核对触发原因及风险承受，不能忽略已越线规则",
        "减仓或退出：结合真实可卖数量、风险规则与已验证背景自行复核",
        "做T观察：仅有经过验证的日内条件后再评估，不代表当前可执行",
    )
    return PositionDecisionContext(
        name,
        opened if isinstance(opened, str) else None,
        days,
        market_context,
        sector_context,
        stock_context,
        PositionAssessment(stance, title, reason, options, t_plan),
    )
