"""Shared LIVE position-risk service used by HTTP and the existing monitor."""

from __future__ import annotations

import math
import json
import re
import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
import structlog
import portfolio as pf
from aqsp.core.errors import DataError

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
    holding_days_override: dict[str, int] | None = None, *, notify: bool = True,
    strategy_sectors: Mapping[str, Mapping[str, object]] | None = None,
) -> dict:
    """Check current tenant positions; persist events and optional notifications.

    ``strategy_sectors`` is supplied only by the scheduler's shared refresh.  A
    mapping, including an empty mapping, means cache-only: optional industry
    evidence must not open another upstream request during this check.  Direct
    API callers keep the legacy cache/SWR path when it is omitted.
    """
    import alert_events
    from position_followup import plan_key, risk_rule_key
    with alert_events._LOCK, pf._LOCK:
        initial_roots = pf._load().get("position_reviews", {})
        expected_plan_keys = {position_id: plan_key(root, position_id) for position_id, root in initial_roots.items()}
    try:
        pf_data = pf.get_portfolio()
    except Exception as e:  # noqa: BLE001
        raise PortfolioReadError(f"持仓读取异常：{e}") from e

    holdings = [
        {"code": h["code"], "shares": h["shares"], "cost": h["cost"]}
        for h in pf_data.get("holdings", [])
    ]
    from decision_context import now_shanghai, position_quote_issue

    checked_at = now_shanghai()
    quote_issues = {
        h["code"]: position_quote_issue(h.get("quote"), checked_at=checked_at)
        for h in pf_data.get("holdings", [])
    }
    prices = {
        h["code"]: h["price"]
        for h in pf_data.get("holdings", [])
        if isinstance(h.get("price"), (int, float))
        and math.isfinite(h["price"])
        and h["price"] > 0
        and not isinstance(h["price"], bool)
        and not quote_issues[h["code"]]
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

    rules = pf.observe_risk_prices(prices, expected_holdings=pf_data.get("holdings", []))
    active_codes = {h["code"] for h in holdings}
    rules = [r for r in rules if r["symbol"] in active_codes]
    histories: dict[str, pd.DataFrame] = {}
    checks: dict = {"checked": [], "skipped": []}
    history_rules = [r for r in rules if r["enabled"] and r["symbol"] in prices and r["level"] in {"L2", "L4"}]
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
            reason = quote_issues.get(symbol) or "缺少有效现价"
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
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
                frame = (
                    frame[
                        frame["date"].notna()
                        & (frame["date"].dt.date <= today_shanghai())
                    ]
                    .sort_values("date", kind="stable")
                    .drop_duplicates("date")
                )
                closes = pd.to_numeric(frame["close"], errors="coerce")
                # EMA uses every input bar; MA only uses its trailing window.
                evaluated_closes = closes if level == "L4" else closes.tail(minimum)
                if len(frame) < minimum:
                    reason = "日线样本不足"
                elif frame["date"].iloc[-1].date() < latest_completed_trading_day():
                    reason = "日线过期"
                elif (
                    not evaluated_closes.gt(0).all()
                    or not evaluated_closes.map(math.isfinite).all()
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
        position_rules=usable_rules,
    )
    import alert_events
    from decision_context import build_position_context, load_cached_market_overview

    overview = load_cached_market_overview()
    serialized = [reminder.to_dict() for reminder in reminders]
    planned_codes = {root.get("symbol") for root in initial_roots.values() if root.get("review_plan", {}).get("choice")}
    sector_codes = sorted({row["symbol"] for row in serialized} | (planned_codes & active_codes))
    if strategy_sectors is None:
        from position_context_source import load_position_sectors

        sectors = load_position_sectors(sector_codes)
    else:
        sectors = {
            code: value for code, value in strategy_sectors.items()
            if code in sector_codes and isinstance(value, Mapping)
        }
    contexts: dict[str, dict] = {}
    for holding in pf_data.get("holdings", []):
        code = holding["code"]
        sector = sectors.get(code)
        # Industry membership and board quotes must both carry source time.
        # Never persist current classifications into historical holdings/ledger.
        if sector and not position_quote_issue({"as_of": sector.get("membership_as_of")}, checked_at=checked_at):
            context_holding = {**holding, "industry": sector.get("industry")}
        else:
            context_holding, sector = holding, None
        contexts[code] = build_position_context(
            context_holding,
            [row for row in serialized if row["symbol"] == code],
            quote=holding.get("quote"),
            stock_history=histories.get(code),
            market_overview=overview,
            sector_snapshot=sector,
            checked_at=checked_at,
        ).to_dict()
        from position_strategy_evidence import enrich_strategy_context

        contexts[code] = enrich_strategy_context(contexts[code], overview, sector, checked_at=checked_at)
    for row in serialized:
        row["position_context"] = contexts.get(str(row["symbol"]))

    from live_risk_commit import commit_position_check

    committed = commit_position_check(
        serialized, pf_data.get("holdings", []), rules, checked_rules, set(prices),
    )
    serialized, new_events = committed.reminders, committed.new_events
    checked_rules = committed.eligible_rules
    if committed.changed_symbols:
        checks["checked"] = [key for key in checks["checked"] if key.split(":", 1)[0] not in committed.changed_symbols]
        checks["skipped"].extend(
            {"symbol": code, "level": "POSITION", "reason": "检查期间持仓或规则已变化，请重新检查"}
            for code in sorted(committed.changed_symbols)
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
    # Reuse this already fetched source batch; page reads never create checks.
    from position_followup_service import check_plan_followups
    try:
        risk_notified = {str(event.get("position_id")) for event in alert_events.list_events()
                         if event.get("rule_id") in eligible
                         and str(event.get("last_notified_at") or "")[:10] == checked_at.date().isoformat()}
        followup = check_plan_followups(pf_data, contexts, current=checked_at, notify=notify,
                                       risk_notified_positions=risk_notified, expected_plan_keys=expected_plan_keys,
                                       expected_rule_keys={position_id: risk_rule_key(rules, position_id) for position_id in initial_roots},
                                       changed_symbols=set(committed.changed_symbols))
        checks["skipped"].extend(followup["skipped"])
        notify_results.extend(followup["notify"])
        suppressed = suppressed or followup["notify_suppressed"]
    except (OSError, ValueError, DataError):
        logger.exception("position_plan_followup_failed")
        checks["skipped"].append({"symbol": "", "level": "PLAN", "reason": "计划跟进未完成；已保存风险检查不受影响，请核对后台记录"})
    return {
        "reminders": serialized,
        "new_events": new_events,
        "notify": notify_results,
        "notify_suppressed": suppressed,
        "checks": checks,
        "last_checked_at": last_checked_at,
    }


def _tenant_folders(root: Path) -> list[Path]:
    """Return valid tenant data directories in stable order."""
    folders = [root]
    users = root / "users"
    if users.is_dir() and not users.is_symlink():
        folders += sorted({path.parent for pattern in ('*/portfolio.json', '*/candidate-observations.json')
                           for path in users.glob(pattern)})
    return [
        folder for folder in folders
        if not folder.is_symlink()
        and not folder.parent.is_symlink()
        and folder.is_dir()
    ]


def _collect_strategy_codes(folders: Sequence[Path]) -> tuple[str, ...]:
    """Collect position and active-candidate codes without any network I/O."""
    import candidate_observation
    import tenant

    codes: set[str] = set()
    for folder in folders:
        tid = "local" if folder == Path(pf.CACHE_DIR) else folder.name
        if tid != "local" and not re.fullmatch(r"[uk]_[0-9a-f]{20}", tid):
            continue
        portfolio_path = folder / "portfolio.json"
        candidate_path = folder / "candidate-observations.json"
        try:
            if portfolio_path.is_file() and not portfolio_path.is_symlink():
                payload = json.loads(portfolio_path.read_text(encoding="utf-8"))
                holdings = payload.get("holdings", []) if isinstance(payload, dict) else []
                if isinstance(holdings, list):
                    codes.update(
                        row.get("code") for row in holdings
                        if isinstance(row, dict) and isinstance(row.get("code"), str)
                        and re.fullmatch(r"[0-9]{6}", row["code"])
                    )
            if candidate_path.is_file() and not candidate_path.is_symlink():
                token = tenant.current_tenant.set(tid)
                try:
                    resources = candidate_observation.load_store().get("resources", {})
                finally:
                    tenant.current_tenant.reset(token)
                if isinstance(resources, dict):
                    codes.update(
                        code for code, row in resources.items()
                        if re.fullmatch(r"[0-9]{6}", str(code))
                        and isinstance(row, dict)
                        and isinstance(row.get("watch"), dict)
                        and row["watch"].get("status") == "active"
                    )
        except (DataError, OSError, ValueError, TypeError, KeyError):
            # The normal tenant loop reports this bucket as failed.  One bad
            # bucket must not prevent optional evidence for healthy buckets.
            continue
    return tuple(sorted(codes))


def _refresh_shared_strategy_sources(codes: Sequence[str]) -> tuple[dict[str, Mapping[str, object]], dict[str, str]]:
    """Refresh shared market/industry evidence once, then read its cache."""
    if not codes:
        return {}, {"state": "skipped", "issue": "没有持仓或活跃候选"}
    try:
        from aqsp.core.time import now_shanghai
        from position_strategy_evidence import load_cached_strategy_sectors, refresh_strategy_sources

        result = asyncio.run(refresh_strategy_sources(codes, checked_at=now_shanghai()))
        checked_at = now_shanghai()
        cached = load_cached_strategy_sectors(codes, checked_at=checked_at)
        return cached, result.to_dict()
    except (DataError, OSError, ValueError, TypeError, RuntimeError, asyncio.CancelledError):
        logger.warning("shared_strategy_evidence_unavailable")
        return {}, {"state": "partial", "issue": "共享大盘/板块依据刷新失败，缺失项保持未知"}


def check_existing_tenants(*, notify: bool = False) -> dict:
    """Monitor only existing persisted tenant portfolios; never migrate or seed data.

    Tenant ids/codes stay inside this process. The monitor receives aggregate counts.
    Failure in one portfolio does not silently mark the others healthy.
    """
    import tenant

    root = Path(pf.CACHE_DIR)
    candidates = _tenant_folders(root)
    strategy_codes = _collect_strategy_codes(candidates)
    strategy_sectors, strategy_refresh = _refresh_shared_strategy_sources(strategy_codes)
    summary: dict = {
        "tenants_checked": 0,
        "open_events": 0,
        "skipped_checks": 0,
        "failed_tenants": 0,
        "candidate_checks": 0,
        "candidate_notifications": 0,
        "strategy_evidence_refresh": strategy_refresh,
    }
    for folder in candidates:
        if folder.is_symlink() or folder.parent.is_symlink() or not folder.is_dir():
            continue
        tid = "local" if folder == root else folder.name
        if tid != "local" and not re.fullmatch(r"[uk]_[0-9a-f]{20}", tid):
            continue
        try:
            path = folder / 'portfolio.json'
            if path.is_symlink():
                continue
            payload = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            has_positions = isinstance(payload, dict) and bool(payload.get('holdings') or payload.get('position_reviews'))
            candidate_path = folder / 'candidate-observations.json'
            has_candidates = candidate_path.is_file() and not candidate_path.is_symlink()
            if not has_positions and not has_candidates:
                continue
            token = tenant.current_tenant.set(tid)
            try:
                if has_positions:
                    result = check_positions(notify=notify, strategy_sectors=strategy_sectors)
                    summary['skipped_checks'] += len(result['checks']['skipped'])
                    import alert_events
                    summary['open_events'] += len(alert_events.list_events(status='OPEN'))
                if has_candidates:
                    from candidate_monitor import check_candidates
                    checked = check_candidates(notify=notify)
                    summary['candidate_checks'] += checked['checked']
                    summary['candidate_notifications'] += checked['notified']
                summary["tenants_checked"] += 1
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
