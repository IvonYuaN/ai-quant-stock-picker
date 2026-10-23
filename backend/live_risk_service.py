"""Shared LIVE position-risk service used by HTTP and the existing monitor."""

from __future__ import annotations

import math
import json
import re
from pathlib import Path
import structlog
import portfolio as pf

logger = structlog.get_logger(__name__)


class PortfolioReadError(RuntimeError):
    """Position storage or quotes could not be read."""


def transition_alert(event_id: str, action: str, note: str = "") -> dict | None:
    """Commit disposition and matching frequency state with retry-safe receipts."""
    import alert_events

    def record(event: dict, recovery: bool) -> dict | None:
        if not event.get("rule_id") or not event.get("position_id"):
            return None
        return pf.record_rule_disposition(
            event["rule_id"],
            action == "ignored",
            expected_position_id=event["position_id"],
            expected_revision=event.get("rule_revision", 0),
            event_id=event["id"],
            require_receipt=recovery,
        )

    return alert_events.transition(event_id, action, note, before_commit=record)


def _live_risk_alert_markdown(events: list[dict]) -> str:
    symbols = {event.get("symbol") for event in events}
    lines = [f"# 持仓风险待复核 · {len(symbols)} 只", ""]
    context = events[0].get("position_context") or {} if events else {}
    if context:
        assessment = context.get("assessment") or {}
        lines.extend([str(assessment.get("title") or ""), str(assessment.get("reason") or ""), ""])
    for event in events:
        basis = event.get("basis_json") or {}
        position = event.get("position_context") or {}
        lines.append(
            f"- **{position.get('name') or '名称待补'} {event.get('symbol')} · {event.get('level')}** {basis.get('rule', '')}；{basis.get('actual', '')} {event.get('t1_note', '')}"
        )
    lines.extend(["", "规则触发不等于全仓卖出指令；请结合大盘、板块与个股证据复核持有、减仓或做 T 条件。做 T 不保证解套，既有硬风险仍保留。"])
    return "\n".join(lines)


def check_positions(
    holding_days_override: dict[str, int] | None = None, *, notify: bool = True
) -> dict:
    """Check current tenant positions; persist events and optional notifications."""
    try:
        pf_data = pf.get_portfolio()
    except Exception as e:  # noqa: BLE001
        raise PortfolioReadError(f"持仓读取异常：{e}") from e

    holdings = [
        {"code": h["code"], "shares": h["shares"], "cost": h["cost"]}
        for h in pf_data.get("holdings", [])
    ]
    prices = {
        h["code"]: h["price"]
        for h in pf_data.get("holdings", [])
        if isinstance(h.get("price"), (int, float))
        and math.isfinite(h["price"])
        and h["price"] > 0
    }

    from aqsp.core.errors import DataError

    try:
        holding_days = pf.resolve_holding_days(
            pf_data.get("holdings", []),
            holding_days_override,
        )
    except DataError as exc:
        raise DataError(str(exc)) from exc

    from aqsp.risk.live_risk import run_live_risk_check

    import risk_rules
    from aqsp.core.time import latest_completed_trading_day, today_shanghai
    from aqsp.data.cache import DataCache
    from aqsp.data.source_factory import build_data_source
    from datetime import timedelta
    import pandas as pd

    rules = pf.observe_risk_prices(prices)
    active_codes = {h["code"] for h in holdings}
    rules = [r for r in rules if r["symbol"] in active_codes]
    histories: dict[str, pd.DataFrame] = {}
    checks: dict = {"checked": [], "skipped": []}
    history_rules = [r for r in rules if r["enabled"] and r["level"] in {"L2", "L4"}]
    if history_rules:
        today = today_shanghai()
        try:
            # API/monitor users cannot write the scheduler-owned shared cache.
            source = build_data_source(
                "tencent",
                cache=DataCache(Path(pf._tenant_dir()) / "live-risk-history.db"),
            )
            histories = source.fetch_daily(
                sorted({r["symbol"] for r in history_rules}),
                today
                - timedelta(
                    days=risk_rules.PositionRuleDefaults().history_calendar_days
                ),
                today,
                adjust="",
            )
        except Exception as exc:  # noqa: BLE001 -- failures are explicit check coverage, never all-clear
            logger.warning("live_risk_history_unavailable", error=str(exc))
            histories = {}
    checked_rules: set[str] = set()
    usable_rules: list[dict] = []
    for rule in rules:
        if not rule["enabled"]:
            checked_rules.add(rule["id"])
            continue
        symbol, level = rule["symbol"], rule["level"]
        reason = ""
        cost = next((float(h["cost"]) for h in holdings if h["code"] == symbol), 0)
        if cost <= 0 or not math.isfinite(cost):
            reason = "无有效正成本基准"
        elif not prices.get(symbol):
            reason = "缺少有效现价"
        elif level in {"L2", "L4"}:
            frame = histories.get(symbol)
            minimum = (
                int(rule["param_value"])
                if level == "L2"
                else risk_rules.PositionRuleDefaults().macd_slow
                + risk_rules.PositionRuleDefaults().macd_signal
            )
            if (
                frame is None
                or frame.empty
                or not {"date", "close"}.issubset(frame.columns)
            ):
                reason = "缺少不复权日线"
            else:
                frame = frame.copy()
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
                frame = (
                    frame[
                        frame["date"].notna()
                        & (frame["date"].dt.date <= today_shanghai())
                    ]
                    .sort_values("date")
                    .drop_duplicates("date")
                )
                if len(frame) < minimum:
                    reason = "日线样本不足"
                elif frame["date"].iloc[-1].date() < latest_completed_trading_day():
                    reason = "日线过期"
                elif (
                    not pd.to_numeric(frame["close"], errors="coerce")
                    .tail(minimum)
                    .gt(0)
                    .all()
                ):
                    reason = "日线价格无效"
                histories[symbol] = frame
        if reason:
            checks["skipped"].append(
                {"symbol": symbol, "level": level, "reason": reason}
            )
        else:
            checks["checked"].append(f"{symbol}:{level}")
            checked_rules.add(rule["id"])
            usable_rules.append(rule)

    reminders = run_live_risk_check(
        holdings,
        prices,
        holding_days=holding_days,
        stop_config=None,  # 生产 thresholds（L1 8% / L5 ATR×2.0）
        price_history=histories,
        position_rules=usable_rules if rules else None,
    )
    import alert_events
    from decision_context import build_position_context, load_cached_market_overview

    overview = load_cached_market_overview()
    serialized = [reminder.to_dict() for reminder in reminders]
    contexts: dict[str, dict] = {}
    for holding in pf_data.get("holdings", []):
        code = holding["code"]
        contexts[code] = build_position_context(
            holding,
            [row for row in serialized if row["symbol"] == code],
            quote=holding.get("quote"),
            stock_history=histories.get(code),
            market_overview=overview,
        ).to_dict()
    for row in serialized:
        row["position_context"] = contexts.get(str(row["symbol"]))

    new_events = alert_events.upsert_from_reminders(
        serialized,
        checked_symbols=set(prices),
        checked_rules=checked_rules,
        active_symbols=active_codes,
    )
    last_checked_at = alert_events.mark_checked(checks)

    notify_results: list[str] = []
    suppressed = False
    eligible = checked_rules | {
        alert_events._dedup_key(e) for e in new_events if not e.get("rule_id")
    }
    if notify:
        from live_risk_delivery import deliver_position_alerts

        delivery = deliver_position_alerts(
            eligible,
            last_checked_at,
            render=_live_risk_alert_markdown,
            acknowledge=alert_events.mark_notified_many,
        )
        notify_results = list(delivery.notify)
        suppressed = delivery.notify_suppressed
    return {
        "reminders": serialized,
        "new_events": new_events,
        "notify": notify_results,
        "notify_suppressed": suppressed,
        "checks": checks,
        "last_checked_at": last_checked_at,
    }


def check_existing_tenants(*, notify: bool = False) -> dict:
    """Monitor only existing persisted tenant portfolios; never migrate or seed data.

    Tenant ids/codes stay inside this process. The monitor receives aggregate counts.
    Failure in one portfolio does not silently mark the others healthy.
    """
    import tenant

    root = Path(pf.CACHE_DIR)
    candidates = [root / "portfolio.json"]
    users = root / "users"
    if users.is_dir() and not users.is_symlink():
        candidates += sorted(users.glob("*/portfolio.json"))
    summary: dict = {
        "tenants_checked": 0,
        "open_events": 0,
        "skipped_checks": 0,
        "failed_tenants": 0,
    }
    for path in candidates:
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            continue
        tid = "local" if path.parent == root else path.parent.name
        if tid != "local" and not re.fullmatch(r"[uk]_[0-9a-f]{20}", tid):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not payload.get("holdings"):
                continue
            token = tenant.current_tenant.set(tid)
            try:
                result = check_positions(notify=notify)
                summary["tenants_checked"] += 1
                summary["skipped_checks"] += len(result["checks"]["skipped"])
                import alert_events

                summary["open_events"] += len(alert_events.list_events(status="OPEN"))
            finally:
                tenant.current_tenant.reset(token)
        except Exception:  # noqa: BLE001 -- privacy: do not expose tenant path/codes to monitor output
            summary["failed_tenants"] += 1
    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Check existing user positions without submitting orders"
    )
    parser.add_argument("--monitor", action="store_true", required=True)
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check_existing_tenants(notify=args.notify), ensure_ascii=False))
