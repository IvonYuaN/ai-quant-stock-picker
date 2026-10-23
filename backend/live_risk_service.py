"""Shared LIVE position-risk service used by HTTP and the existing monitor."""

from __future__ import annotations

import os
import math
import json
import re
from pathlib import Path
import structlog
import portfolio as pf

logger = structlog.get_logger(__name__)


class PortfolioReadError(RuntimeError):
    """Position storage or quotes could not be read."""


def _live_risk_alert_markdown(events: list[dict]) -> str:
    lines = [f"# 持仓风险提醒-{len(events)} 条", ""]
    for event in events:
        basis = event.get("basis_json") or {}
        lines.append(
            f"- **{event.get('level')}｜{event.get('symbol')}** {event.get('action_hint')}（{basis.get('rule', '')}；{basis.get('actual', '')}）{event.get('t1_note', '')}"
        )
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
            source = build_data_source("tencent")
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

    new_events = alert_events.upsert_from_reminders(
        [r.to_dict() for r in reminders],
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
    pending = alert_events.pending_notifications(eligible) if notify else []
    if pending:
        from aqsp.notification_runtime import dispatch_notification_once
        import portfolio as _pf

        state_path = os.path.join(_pf._tenant_dir(), "notify-state-live-risk.json")
        for e in pending:
            period = last_checked_at[:10] if e.get("freq_mode") == "daily" else "once"
            kind = f"live-risk|{e['id']}|{period}"
            if e.get("freq_mode") == "always":
                from uuid import uuid4

                kind += f"|{uuid4().hex}"
            results = dispatch_notification_once(
                _live_risk_alert_markdown([e.get("delivery_view") or e]),
                mode="summary",
                prefix="live-risk",
                kind=kind,
                state_path=state_path,
            )
            if not results:
                suppressed = True
            elif any(getattr(result, "ok", False) for result in results):
                alert_events.mark_notified(e["id"])
            from aqsp.notifier import format_notify_results

            notify_results.extend(
                format_notify_results(results, prefix="live-risk") if results else []
            )
    return {
        "reminders": [r.to_dict() for r in reminders],
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
