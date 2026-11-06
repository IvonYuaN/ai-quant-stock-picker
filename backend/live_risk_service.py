"""Shared LIVE position-risk service used by HTTP and the existing monitor."""

from __future__ import annotations

import math
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
import structlog
import pandas as pd
import portfolio as pf
from aqsp.data.cache import DataCache
from aqsp.core.errors import DataError
from aqsp.core.time import latest_completed_trading_day
from aqsp.data.adjust import AdjustmentService
from aqsp.data.source_factory import build_data_source
from price_observation_rules import IndicatorSnapshot, SignalSnapshot

logger = structlog.get_logger(__name__)


class PortfolioReadError(RuntimeError):
    """Position storage or quotes could not be read."""


def _indicator_snapshots(
    rules: list[dict], *, checked_at: datetime
) -> dict[tuple[str, str], IndicatorSnapshot]:
    """Calculate configured indicators from fresh raw bars and PIT factors.

    One symbol can watch several metrics. Each (symbol, metric) pair gets its
    own snapshot so an invalid value is never borrowed from a sibling rule.
    Price-based metrics are computed on the same as-of PIT-adjusted basis as
    the existing RSI observation, never on mixed raw prices.
    """
    from aqsp.indicators import MonitorIndicator, enrich_indicators, monitor_indicator_spec

    pairs: set[tuple[str, str]] = {
        (str(row["symbol"]), str(row.get("metric") or ""))
        for row in rules
        if row.get("status") == "active" and row.get("rule_type") == "indicator"
    }
    if not pairs:
        return {}

    snapshots: dict[tuple[str, str], IndicatorSnapshot] = {}
    valid: dict[str, list[tuple[str, MonitorIndicator]]] = {}
    for symbol, metric in sorted(pairs):
        try:
            spec = monitor_indicator_spec(metric)
        except ValueError:
            snapshots[(symbol, metric)] = IndicatorSnapshot(
                None, None, "", "不支持的监控指标"
            )
            continue
        valid.setdefault(symbol, []).append((metric, spec))
    if not valid:
        return snapshots

    symbols = sorted(valid)
    max_bars = max(int(spec.min_bars) for rows in valid.values() for _, spec in rows)
    expected_as_of = latest_completed_trading_day(checked_at)

    def fail_all(actual: str | None, source: str, issue: str) -> dict[tuple[str, str], IndicatorSnapshot]:
        for symbol, rows in valid.items():
            for metric, _spec in rows:
                snapshots[(symbol, metric)] = IndicatorSnapshot(
                    None, actual, source, issue
                )
        return snapshots

    try:
        source = build_data_source("sqlite_db", cache=None)
        if source.price_mode() != "raw":
            raise DataError("指标监控要求 raw 不复权 SQLite 日线")
        latest = source.get_latest_daily_date()
        if latest != expected_as_of:
            actual = latest.isoformat() if latest else "缺失"
            return fail_all(
                actual,
                "sqlite_db",
                f"日线数据陈旧：最新 {actual}，预期 {expected_as_of.isoformat()}",
            )
        # Natural-day window covering the longest warm-up plus holiday padding.
        # This is a data-fetch bound, not a strategy threshold.
        lookback_days = max_bars * 2 + 30
        start = expected_as_of - timedelta(days=lookback_days)
        frames = source.fetch_daily(symbols, start, expected_as_of, adjust="")
        adjustment = AdjustmentService(cache=DataCache())
        for symbol in symbols:
            rows = valid[symbol]
            frame = frames.get(symbol)
            actual_rows = 0 if frame is None else len(frame)
            if frame is None or frame.empty:
                issue = f"该标的无可用日线，实际 {actual_rows} 根"
                for metric, _spec in rows:
                    snapshots[(symbol, metric)] = IndicatorSnapshot(
                        None, expected_as_of.isoformat(), "sqlite_db", issue
                    )
                continue
            raw_dates = frame.get("date")
            bar_dates = (
                pd.to_datetime(raw_dates, errors="coerce").dt.date
                if raw_dates is not None
                else None
            )
            symbol_as_of = None
            if bar_dates is not None and not bar_dates.empty:
                latest_value = bar_dates.max()
                if not pd.isna(latest_value):
                    symbol_as_of = latest_value
            if symbol_as_of != expected_as_of:
                actual = symbol_as_of.isoformat() if symbol_as_of else "缺失"
                issue = f"该标的日线陈旧：最新 {actual}，预期 {expected_as_of.isoformat()}"
                for metric, _spec in rows:
                    snapshots[(symbol, metric)] = IndicatorSnapshot(
                        None,
                        actual if symbol_as_of else None,
                        "sqlite_db",
                        issue,
                    )
                continue
            adjusted = adjustment.apply_qfq(frame)
            adjusted["open"] = adjusted["open_qfq"]
            adjusted["high"] = adjusted["high_qfq"]
            adjusted["low"] = adjusted["low_qfq"]
            adjusted["close"] = adjusted["close_qfq"]
            enriched = enrich_indicators(adjusted)
            for metric, spec in rows:
                # Each metric has its own warm-up. A short window blocks only
                # the indicators that need more bars, not their siblings.
                if actual_rows < spec.min_bars:
                    snapshots[(symbol, metric)] = IndicatorSnapshot(
                        None,
                        expected_as_of.isoformat(),
                        "sqlite_db",
                        f"{spec.label} 样本不足：需要 {spec.min_bars} 根，实际 {actual_rows} 根",
                    )
                    continue
                value = enriched[metric].iloc[-1]
                if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    snapshots[(symbol, str(metric))] = IndicatorSnapshot(
                        None,
                        expected_as_of.isoformat(),
                        "sqlite_db",
                        f"{spec.label} 结果不可用",
                    )
                    continue
                snapshots[(symbol, str(metric))] = IndicatorSnapshot(
                    float(value), expected_as_of.isoformat(), "sqlite_db"
                )
        return snapshots
    except Exception as exc:  # noqa: BLE001 -- indicator failures stay visible and fail closed
        logger.warning("indicator_observation_data_unavailable", error=str(exc))
        return fail_all(None, "", "指标依据不可用")


def _signal_snapshots(
    rules: list[dict], *, checked_at: datetime
) -> dict[tuple[str, str], SignalSnapshot]:
    """Evaluate pinned tenant signal definitions on fresh raw daily bars."""
    import signal_definitions

    signal_rules = [
        row for row in rules
        if row.get("status") == "active" and row.get("rule_type") == "signal"
    ]
    definitions: dict[tuple[str, str], dict] = {}
    for row in signal_rules:
        signal_id = str(row.get("signal_id") or "")
        definition = row.get("signal_snapshot")
        if isinstance(definition, dict):
            definitions[(str(row.get("symbol", "")), signal_id)] = definition
    if not definitions:
        return {}

    expected_as_of = latest_completed_trading_day(checked_at)
    snapshots: dict[tuple[str, str], SignalSnapshot] = {}

    def block(key: tuple[str, str], reason: str, *, as_of: str | None = None) -> None:
        definition = definitions.get(key) or {}
        rule = next((item for item in signal_rules if str(item.get("symbol")) == key[0] and str(item.get("signal_id")) == key[1]), {})
        snapshots[key] = SignalSnapshot(
            triggered=None,
            as_of=as_of,
            source="sqlite_db" if as_of else "",
            signal_name=str(definition.get("name") or rule.get("signal_id") or ""),
            signal_revision=int(definition.get("revision") or 0),
            definition_sha256=str(rule.get("signal_snapshot_sha256") or ""),
            issue=reason,
        )

    try:
        source = build_data_source("sqlite_db", cache=None)
        if source.price_mode() != "raw":
            raise DataError("条件信号监控要求 raw 不复权 SQLite 日线")
        latest = source.get_latest_daily_date()
        if latest != expected_as_of:
            actual = latest.isoformat() if latest else None
            reason = f"日线数据陈旧：最新 {actual or '缺失'}，预期 {expected_as_of.isoformat()}"
            for key in definitions:
                block(key, reason, as_of=actual)
            return snapshots

        symbols = sorted({symbol for symbol, _signal_id in definitions})
        latest_by_symbol = source.get_latest_daily_dates(symbols)
        fresh_symbols: list[str] = []
        for symbol in symbols:
            symbol_latest = latest_by_symbol.get(symbol)
            if symbol_latest != expected_as_of:
                actual = symbol_latest.isoformat() if symbol_latest else "缺失"
                for key in definitions:
                    if key[0] == symbol:
                        block(
                            key,
                            f"该标的日线陈旧：最新 {actual}，预期 {expected_as_of.isoformat()}",
                            as_of=symbol_latest.isoformat() if symbol_latest else None,
                        )
                continue
            fresh_symbols.append(symbol)
        if not fresh_symbols:
            return snapshots
        max_lookback = max(
            signal_definitions.max_lookback(definition)
            for (symbol, _signal_id), definition in definitions.items()
            if symbol in fresh_symbols
        )
        calendar_days = max(90, max_lookback * 4)
        frames = source.fetch_daily(
            fresh_symbols, expected_as_of - timedelta(days=calendar_days), expected_as_of, adjust=""
        )
        for symbol in fresh_symbols:
            symbol_keys = [key for key in definitions if key[0] == symbol]
            frame = frames.get(symbol)
            if frame is None or frame.empty:
                for key in symbol_keys:
                    block(key, "本地 raw 日线缺失")
                continue
            frame = frame.copy()
            parsed_dates = pd.to_datetime(frame.get("date"), errors="coerce")
            if parsed_dates.isna().any():
                for key in symbol_keys:
                    block(key, "该标的日线包含无效日期")
                continue
            frame["date"] = parsed_dates.dt.strftime("%Y-%m-%d")
            frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
            if frame["date"].duplicated().any():
                for key in symbol_keys:
                    block(key, "该标的日线有重复交易日")
                continue
            symbol_as_of = date.fromisoformat(str(frame["date"].iloc[-1]))
            if symbol_as_of != expected_as_of:
                actual = symbol_as_of.isoformat()
                for key in symbol_keys:
                    block(key, f"该标的日线陈旧：最新 {actual}，预期 {expected_as_of.isoformat()}", as_of=actual)
                continue
            if len(frame) <= max_lookback:
                for key in symbol_keys:
                    block(key, f"信号回看需要 {max_lookback} 根历史日线，实际仅有 {len(frame)} 根", as_of=expected_as_of.isoformat())
                continue
            latest_bar = frame.iloc[-1]
            volume = pd.to_numeric(pd.Series([latest_bar.get("volume")]), errors="coerce").iloc[0]
            if bool(latest_bar.get("suspended", False)) or pd.isna(volume) or float(volume) <= 0:
                for key in symbol_keys:
                    block(key, "该标的最近交易日停牌或无有效成交，信号不触发", as_of=expected_as_of.isoformat())
                continue
            frame["raw_close"] = pd.to_numeric(frame["close"], errors="coerce")
            frame["prev_close"] = frame["raw_close"].shift(1)

            for key in symbol_keys:
                definition = definitions[key]
                offsets = signal_definitions.price_factor_offsets(definition)
                if offsets:
                    dates = [date.fromisoformat(str(value)) for value in frame["date"]]
                    factor_records = source.get_point_in_time_adjustment_factors([symbol], dates).get(symbol, {})
                    required_indices = {-1, *(-1 - offset for offset in offsets)}
                    unavailable = [
                        frame["date"].iloc[index]
                        for index in sorted(required_indices)
                        if dates[index] not in factor_records
                    ]
                    if unavailable:
                        block(key, f"跨日价格条件缺少 PIT 复权因子：{', '.join(unavailable)}", as_of=expected_as_of.isoformat())
                        continue
                    frame["adj_factor"] = [
                        factor_records[item].value if item in factor_records else float("nan")
                        for item in dates
                    ]
                try:
                    triggered = bool(signal_definitions.evaluate_signal_series(definition, frame).iloc[-1])
                except (DataError, KeyError, TypeError, ValueError) as exc:
                    block(key, f"信号定义无法在当前日线求值：{exc}", as_of=expected_as_of.isoformat())
                    continue
                snapshots[key] = SignalSnapshot(
                    triggered=triggered,
                    as_of=expected_as_of.isoformat(),
                    source="sqlite_db",
                    signal_name=str(definition["name"]),
                    signal_revision=int(definition["revision"]),
                    definition_sha256=str(
                        next(item for item in signal_rules if (str(item.get("symbol")), str(item.get("signal_id"))) == key)["signal_snapshot_sha256"]
                    ),
                    current_price=float(frame["raw_close"].iloc[-1]),
                )
        return snapshots
    except Exception as exc:  # noqa: BLE001 -- all signal input failures stay visible and fail closed
        logger.warning("condition_signal_observation_data_unavailable", error=str(exc))
        for key in definitions:
            if key not in snapshots:
                block(key, "条件信号依据不可用")
        return snapshots


def transition_alert(event_id: str, action: str, note: str = "") -> dict | None:
    """Commit disposition and matching frequency state with retry-safe receipts."""
    import alert_events

    event = next((row for row in alert_events.list_events() if row.get("id") == event_id), None)
    if event and event.get("kind") == "price_observation":
        return alert_events.transition(event_id, action, note)

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
    from position_context_source import load_position_sectors

    planned_codes = {root.get("symbol") for root in initial_roots.values() if root.get("review_plan", {}).get("choice")}
    sectors = load_position_sectors(sorted({row["symbol"] for row in serialized} | (planned_codes & active_codes)))
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
    price_observation_rules: dict = {"checked": 0, "triggered": [], "skipped": []}
    try:
        import price_observation_rules as price_rules

        all_price_rules = price_rules.list_rules()
        active_price_rules = [row for row in all_price_rules if row.get("status") == "active"]
    except Exception:  # noqa: BLE001 -- rule storage failure must not suppress position risk checks
        logger.exception("price_observation_rules_read_failed")
        all_price_rules = []
        active_price_rules = []
        price_observation_rules["skipped"].append({"symbol": "", "reason": "通用价格观察规则读取失败"})

    candidate_observations: dict = {
        "checked": [],
        "skipped": [],
        "notify": [],
        "notify_suppressed": False,
    }
    active: list[dict] = []
    candidate_watch = None
    try:
        import candidate_observations as candidate_watch_module

        candidate_watch = candidate_watch_module
        active = [item for item in candidate_watch.list_observations() if item.get("state") == "active"]
    except Exception:  # noqa: BLE001 -- one watch store must not block another
        logger.exception("candidate_observation_read_failed")
        candidate_observations["skipped"].append({"symbol": "", "reason": "候选观察读取失败；其它监控继续"})

    watch_quotes: dict[str, dict] = {
        str(row.get("code")): dict(row.get("quote") or {})
        for row in pf_data.get("holdings", []) if row.get("code") in prices
    }
    for code in prices:
        holding = next((row for row in pf_data.get("holdings", []) if row.get("code") == code), None)
        if holding and "price" not in watch_quotes.get(code, {}):
            watch_quotes.setdefault(code, {})["price"] = prices[code]
    quote_rule_symbols = {
        str(row["symbol"])
        for row in active_price_rules
        if row.get("rule_type", "price") == "price"
    }
    watch_codes = sorted(
        ({item["symbol"] for item in active} | quote_rule_symbols) - set(prices)
    )
    if watch_codes:
        try:
            import astock

            watch_quotes.update(astock.tencent_quote(watch_codes))
        except Exception:  # noqa: BLE001 -- missing quotes fail closed for watch rules
            logger.exception("watch_quote_batch_failed")

    try:
        if candidate_watch is None:
            raise ValueError("候选观察读取失败")
        candidate_observations = candidate_watch.check_observations(
            watch_quotes,
            checked_at=checked_at,
            held_symbols=set(prices),
            held_positions={
                str(row["code"]): str(row["position_id"])
                for row in pf_data.get("holdings", [])
                if row.get("code") and row.get("position_id")
            },
        )
        if notify and candidate_observations.get("checked"):
            from candidate_observation_delivery import deliver_triggered

            messages, notify_suppressed = deliver_triggered(
                active, candidate_observations["checked"]
            )
            candidate_observations["notify"] = messages
            candidate_observations["notify_suppressed"] = notify_suppressed
    except Exception:  # noqa: BLE001 -- candidate monitoring must not suppress position risk checks
        logger.exception("candidate_observation_check_failed")
        candidate_observations["skipped"].append({"symbol": "", "reason": "候选观察未完成；持仓风险检查不受影响"})
    try:
        import price_observation_rules as price_rules

        for code in prices:
            if code in watch_quotes and "price" not in watch_quotes[code]:
                watch_quotes[code]["price"] = prices[code]
        price_observation_rules = price_rules.check_rules(
            watch_quotes,
            checked_at=checked_at,
            indicator_values=_indicator_snapshots(
                active_price_rules, checked_at=checked_at
            ),
            signal_values=_signal_snapshots(
                active_price_rules, checked_at=checked_at
            ),
            held_positions={
                str(row["code"]): str(row["position_id"])
                for row in pf_data.get("holdings", [])
                if row.get("code") and row.get("position_id")
            },
        )
        written = alert_events.upsert_price_observation_events(
            price_observation_rules["triggered"],
            checked_rule_ids=set(price_observation_rules.get("checked_rule_ids", set())),
        )
        price_observation_rules["new_events"] = written
    except Exception:  # noqa: BLE001 -- generic rules must not suppress position risk checks
        logger.exception("price_observation_rule_check_failed")
        price_observation_rules["skipped"].append({"symbol": "", "reason": "通用价格观察未完成；持仓风险检查不受影响"})
    market_event_rules: dict = {"checked": 0, "triggered": [], "skipped": []}
    try:
        import market_event_rules as market_rules

        if market_rules.active_rules():
            import market

            market_event_rules = market_rules.check_rules(
                market.get_short_term_emotion_snapshot(), checked_at=checked_at
            )
            market_event_rules["new_events"] = alert_events.upsert_market_event_rule_events(
                market_event_rules["triggered"],
                checked_rule_ids=set(market_event_rules.get("checked_rule_ids", set())),
            )
    except Exception:  # noqa: BLE001 -- market checks cannot suppress position-risk checks
        logger.exception("market_event_rule_check_failed")
        market_event_rules["skipped"].append({"rule_id": "", "reason": "全市场异动检查未完成；旧提醒保持原状态"})
    return {
        "reminders": serialized,
        "new_events": new_events,
        "notify": notify_results,
        "notify_suppressed": suppressed,
        "checks": checks,
        "candidate_observations": candidate_observations,
        "price_observation_rules": price_observation_rules,
        "market_event_rules": market_event_rules,
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
    # Candidates can be monitored even when the tenant has no positions yet.
    watch_files = [root / "candidate_observations.json"]
    if users.is_dir() and not users.is_symlink():
        watch_files += sorted(users.glob("*/candidate_observations.json"))
    watch_files.append(root / "price_observation_rules.json")
    watch_files.append(root / "market_event_rules.json")
    if users.is_dir() and not users.is_symlink():
        watch_files += sorted(users.glob("*/price_observation_rules.json"))
        watch_files += sorted(users.glob("*/market_event_rules.json"))
    candidates += watch_files
    seen: set[str] = set()
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
        if tid in seen:
            continue
        if tid != "local" and not re.fullmatch(r"[uk]_[0-9a-f]{20}", tid):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if path.name in {"candidate_observations.json", "price_observation_rules.json", "market_event_rules.json"}:
                if not any(isinstance(item, dict) and item.get("state") == "active" for item in payload.get("items", [])):
                    if path.name == "candidate_observations.json" or not any(isinstance(item, dict) and item.get("status") == "active" for item in payload.get("items", [])):
                        continue
            elif not (payload.get("holdings") or payload.get("position_reviews")):
                continue
            token = tenant.current_tenant.set(tid)
            try:
                seen.add(tid)
                if path.name in {"candidate_observations.json", "price_observation_rules.json", "market_event_rules.json"}:
                    # The paired portfolio file may not exist for a new candidate-only tenant.
                    payload = {"holdings": [], "position_reviews": {}}
                result = check_positions(notify=notify)
                summary["tenants_checked"] += 1
                summary["skipped_checks"] += len(result["checks"]["skipped"])
                summary["skipped_checks"] += len(
                    (result.get("candidate_observations") or {}).get("skipped", [])
                )
                summary["skipped_checks"] += len(
                    (result.get("price_observation_rules") or {}).get("skipped", [])
                )
                summary["skipped_checks"] += len(
                    (result.get("market_event_rules") or {}).get("skipped", [])
                )
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
