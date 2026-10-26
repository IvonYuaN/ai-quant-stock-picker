"""P0-2 alert_event 数据层 —— 该卖提醒收件箱（站内红点数据源）+ 去重。

合规（与 portfolio.py 同款）：提醒是用户持仓触发的本地事件，存
``~/.vibe-research`` 下按租户隔离，**不进仓库、不上传**。

存储位置：``~/.vibe-research/alert_events.json``（local 租户）或
``~/.vibe-research/users/<tid>/alert_events.json``（其它租户），可用
``VR_DATA_DIR`` 覆盖根目录（与 portfolio.py 共用同一约定，测试同一
conftest 重定向即可隔离）。

alert_event schema（PRD 7.2 新增 2）：
    id, level, symbol, position_id(nullable),
    trigger_at(price), threshold_at(price),
    basis_json{rule, actual, evidence},
    status(OPEN|HANDLED|IGNORED|EXPIRED),
    handled_at, handled_note,
    ignore_streak(int), freq_mode, frequency_note, created_at, updated_at

去重键：持仓唯一 ID + 规则 ID + 显式设置/成本版本；兼容旧事件的代码/级别/位价键。
同一持续条件只更新证据，按 once/daily/always 控制外发，不复活已处置条件。

外发不在此层：见 ``app.py`` /api/live-risk/check（调 notification_runtime
的现成去重状态机 + notifier 10 通道），本层只管站内事件簿。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Sequence
from uuid import uuid4

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock

import tenant as _tenant

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".vibe-research"
)
# 可重入锁：公共方法（upsert/list/transition）持锁后会再调内部 _load/_save
# （它们各自也 with _LOCK）。用 threading.Lock 会在同线程嵌套时自我死锁
# （live_risk_check 端点实测卡死）。RLock 放行同线程重入、跨线程仍排他。
_LOCK = threading.RLock()

#: PRD 7.2 合法状态机：OPEN 只能到 HANDLED / IGNORED / EXPIRED（终态）
_VALID_TRANSITIONS = {
    "OPEN": {"HANDLED", "IGNORED", "EXPIRED"},
}


def _tenant_dir() -> str:
    tid = _tenant.current_tenant.get()
    if tid == "local":
        return CACHE_DIR
    return os.path.join(CACHE_DIR, "users", tid)


def _file() -> str:
    return os.path.join(_tenant_dir(), "alert_events.json")


def _now_iso() -> str:
    return now_shanghai().isoformat(timespec="seconds")


def _load() -> list[dict]:
    """读事件簿；文件缺失/损坏 ⇒ 空簿（不静默吞掉旧数据——损坏时另存 .corrupt）。"""
    with _LOCK:
        path = _file()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            # 损坏数据保留现场供排查，绝不把半截 JSON 当空簿覆盖
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
            return []
        events = data.get("events") if isinstance(data, dict) else None
        return events if isinstance(events, list) else []


def _save(events: list[dict]) -> None:
    """原子写（tmp + os.replace），与 portfolio.py 同款——并发读不撞半截 JSON。"""
    with _LOCK:
        os.makedirs(_tenant_dir(), exist_ok=True)
        path = _file()
        tmp = path + ".tmp"
        checked_at = last_checked_at()
        check_summary = last_check()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "updated": _now_iso(),
                    "events": events,
                    "last_checked_at": checked_at,
                    "last_check": check_summary,
                },
                f,
                ensure_ascii=False,
                indent=1,
            )
        os.replace(tmp, path)


def _dedup_key(event: dict) -> str:
    if event.get("rule_id"):
        return f"{event['rule_id']}|{event.get('position_id') or ''}|{event.get('rule_revision', 0)}"
    return f"{event.get('symbol')}|{event.get('level')}|{round(float(event.get('threshold_at') or 0), 2)}"


def upsert_from_reminders(
    reminders: list[dict],
    *,
    checked_symbols: set[str] | None = None,
    checked_rules: set[str] | None = None,
    active_symbols: set[str] | None = None,
) -> list[dict]:
    """把 ``live_risk.SellReminder.to_dict()`` 列表落进事件簿（去重、更新位价）。

    同一持仓/规则 OPEN 仅更新价格与证据；终态保持到条件确实解除再触发。
    daily 可跨日重开，always 持续发送但不会把已处置的同一持续条件立即复活。

    Returns: 本次新增（或重新 OPEN）的事件列表。
    """
    with _LOCK, advisory_lock(_file()):
        return _upsert_reminders_locked(
            reminders, checked_symbols=checked_symbols,
            checked_rules=checked_rules, active_symbols=active_symbols,
        )


def _upsert_reminders_locked(
    reminders: list[dict], *, checked_symbols: set[str] | None = None,
    checked_rules: set[str] | None = None, active_symbols: set[str] | None = None,
) -> list[dict]:
    """Apply a check while the caller owns both alert locks."""
    new_events: list[dict] = []
    events = _load()
    by_key = {_dedup_key(e): e for e in events}
    current_keys: set[str] = set()
    stamp = _now_iso()
    for rem in reminders:
        symbol = str(rem.get("symbol") or "")
        if not symbol:
            continue
        key = _dedup_key(
            {
                "symbol": symbol,
                "level": rem.get("level"),
                "threshold_at": rem.get("threshold_price"),
                "rule_id": rem.get("rule_id"),
                "position_id": rem.get("position_id"),
                "rule_revision": rem.get("rule_revision", 0),
            }
        )
        current_keys.add(key)
        existing = by_key.get(key)
        mode = rem.get("freq_mode", "once")
        same_day = bool(
            existing
            and str(
                existing.get("last_emitted_at") or existing.get("created_at", "")
            )[:10]
            == stamp[:10]
        )
        continuous = bool(existing and existing.get("condition_active", True))
        if (
            existing
            and existing.get("status") != "OPEN"
            and continuous
            and (mode != "daily" or same_day)
        ):
            existing["updated_at"] = stamp
            continue
        base = {
            "id": uuid4().hex,
            "level": rem.get("level"),
            "symbol": symbol,
            "position_id": rem.get("position_id") or None,
            "trigger_at": rem.get("current_price"),
            "threshold_at": rem.get("threshold_price"),
            "pnl_pct": rem.get("pnl_pct"),
            "position_context": rem.get("position_context"),
            "sellable_now": rem.get("sellable_now"),
            "action_hint": rem.get("action_hint"),
            "t1_note": rem.get("t1_note", ""),
            "basis_json": rem.get("basis_json") or {},
            "status": "OPEN",
            "handled_at": None,
            "handled_note": "",
            "ignore_streak": 0,
            "created_at": stamp,
            "rule_id": rem.get("rule_id", ""),
            "rule_revision": rem.get("rule_revision", 0),
            "freq_mode": mode,
            "condition_active": True,
            "last_notified_at": None,
            "last_emitted_at": stamp,
        }
        # PRD 7.3-4：无依据不入库
        if not base["basis_json"].get("rule") or not base["basis_json"].get(
            "actual"
        ):
            continue
        if existing and existing.get("status") == "OPEN":
            # 同键 OPEN：只刷现价与证据，不新增
            existing["trigger_at"] = base["trigger_at"]
            existing["threshold_at"] = base["threshold_at"]
            existing["pnl_pct"] = base["pnl_pct"]
            existing["position_context"] = base["position_context"]
            existing["sellable_now"] = base["sellable_now"]
            existing["action_hint"] = base["action_hint"]
            existing["t1_note"] = base["t1_note"]
            existing["freq_mode"] = mode
            existing["condition_active"] = True
            existing["basis_json"] = base["basis_json"]
            existing["updated_at"] = _now_iso()
            if mode == "always" or (mode == "daily" and not same_day):
                existing["last_emitted_at"] = stamp
                new_events.append(existing.copy())
            continue
        base["updated_at"] = _now_iso()
        events.append(base)
        by_key[key] = base
        new_events.append(base)
    for event in events:
        if (
            active_symbols is not None
            and event.get("symbol") not in active_symbols
            and event.get("status") == "OPEN"
        ):
            event.update(status="EXPIRED", condition_active=False, updated_at=stamp)
        evaluated = (event.get("rule_id") in (checked_rules or set())) or (
            not event.get("rule_id")
            and event.get("symbol") in (checked_symbols or set())
        )
        if evaluated and _dedup_key(event) not in current_keys:
            event["condition_active"] = False
            if event.get("status") == "OPEN":
                event["status"] = "EXPIRED"
                event["updated_at"] = stamp
    _save(events)
    return new_events


def list_events(status: str | None = None, symbol: str | None = None) -> list[dict]:
    """收件箱数据源：默认按 created_at 倒序；``status`` 过滤（GET /api/alerts 用）。"""
    with _LOCK:
        events = _load()
    if status:
        events = [e for e in events if e.get("status") == status]
    if symbol:
        events = [e for e in events if e.get("symbol") == symbol]
    return sorted(events, key=lambda e: str(e.get("created_at", "")), reverse=True)


def record_review_plan(event_id: str, choice: str) -> dict | None:
    """Record a human review choice for this open position, without dismissing risk.

    Returns None when the event is missing or terminal. Same-position rules share
    one atomic choice; a reopened position must not inherit a previous choice.
    """
    if choice not in {"hold", "reduce", "t_watch"}:
        raise ValueError("方案必须是 hold、reduce 或 t_watch")
    with _LOCK, advisory_lock(_file()):
        events = _load()
        target = next((e for e in events if e.get("id") == event_id), None)
        if target is None or target.get("status") != "OPEN":
            return None
        previous = target.get("review_plan") or {}
        plan = previous.copy() if previous.get("choice") == choice else {"choice": choice, "updated_at": _now_iso()}
        for event in events:
            if (event.get("status") == "OPEN"
                and event.get("symbol") == target.get("symbol")
                and event.get("position_id") == target.get("position_id")
                and event.get("sellable_now") == target.get("sellable_now")):
                event["review_plan"] = plan.copy()
        _save(events)
        return plan


def transition(
    event_id: str,
    action: str,
    note: str = "",
    *,
    before_commit: Callable[[dict, bool], dict | None] | None = None,
) -> dict | None:
    """OPEN → HANDLED / IGNORED（终态）。

    - ``action``: ``handled`` | ``ignored``
    - ``ignored`` 时 ``ignore_streak`` +1（P1-2 灵敏度自适应的数据源）
    - 非 OPEN 状态的操作返回 None（前端据此 409）
    - before_commit(event, recovery) 先持久化规则处置；失败保持 OPEN。
      EXPIRED 只能凭已存在的同动作凭据恢复，不能新计数。
    """
    with _LOCK, advisory_lock(_file()):
        events = _load()
        target = next((e for e in events if e.get("id") == event_id), None)
        if target is None:
            return None
        current = str(target.get("status") or "OPEN")
        if action not in {"handled", "ignored"}:
            raise ValueError("action 必须是 handled 或 ignored")
        dest = "HANDLED" if action == "handled" else "IGNORED"
        recovery = current == "EXPIRED" and before_commit is not None
        if not recovery and (
            current not in _VALID_TRANSITIONS or dest not in _VALID_TRANSITIONS[current]
        ):
            return None
        target["status"] = dest
        target["handled_at"] = _now_iso()
        target["handled_note"] = note or ""
        if dest == "IGNORED":
            target["ignore_streak"] = int(target.get("ignore_streak") or 0) + 1
        else:
            target["ignore_streak"] = 0
        rule = before_commit(target.copy(), recovery) if before_commit else None
        if recovery and rule is None:
            return None
        if rule is not None:
            for key in ("ignore_streak", "freq_mode", "frequency_note"):
                target[key] = rule[key]
        target["updated_at"] = _now_iso()
        _save(events)
        return target


def last_checked_at() -> str | None:
    """The last successful evaluation, independent from event creation times."""
    try:
        with open(_file(), encoding="utf-8") as handle:
            return json.load(handle).get("last_checked_at")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def pending_notifications(eligible_rules: set[str] | None = None) -> list[dict]:
    """Read-only checks must not consume the later background delivery allowance."""
    today = _now_iso()[:10]
    with _LOCK, advisory_lock(_file()):
        events = _load()
        pending = [
            e
            for e in events
            if e.get("status") == "OPEN"
            and (e.get("level") != "L1" or e.get("sellable_now"))
            and (
                eligible_rules is None
                or e.get("rule_id") in eligible_rules
                or _dedup_key(e) in eligible_rules
            )
            and e.get("condition_active", True)
            and (
                e.get("freq_mode") == "always"
                or not e.get("last_notified_at")
                or (
                    e.get("freq_mode") == "daily"
                    and str(e["last_notified_at"])[:10] != today
                )
            )
        ]
        for event in pending:
            period = today if event.get("freq_mode") == "daily" else "once"
            if (
                event.get("delivery_period") != period
                or not event.get("delivery_view")
                or event.get("freq_mode") == "always"
            ):
                event["delivery_period"] = period
                event["delivery_view"] = {
                    key: event.get(key)
                    for key in (
                        "symbol",
                        "level",
                        "basis_json",
                        "action_hint",
                        "t1_note",
                        "position_context",
                    )
                }
        _save(events)
        return [event.copy() for event in pending]


def mark_notified(event_id: str) -> None:
    """A delivery allowance is consumed only after a channel confirms success."""
    with _LOCK, advisory_lock(_file()):
        events = _load()
        for event in events:
            if event["id"] == event_id:
                event["last_notified_at"] = _now_iso()
        _save(events)


def mark_notified_many(event_ids: Sequence[str], *, notified_at: str) -> None:
    """Atomically consume a delivered batch at its actual receipt timestamp.

    A recovered old receipt cannot move today's daily allowance backwards or
    consume a newer day. The caller must supply a confirmed delivery timestamp.
    """
    from datetime import datetime

    parsed = datetime.fromisoformat(notified_at)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("送达凭据时间必须包含时区")
    wanted = set(event_ids)
    with _LOCK, advisory_lock(_file()):
        events = _load()
        changed = False
        for event in events:
            if event.get("id") not in wanted:
                continue
            prior = event.get("last_notified_at")
            if prior and datetime.fromisoformat(str(prior)) >= parsed:
                continue
            event["last_notified_at"] = notified_at
            changed = True
        if changed:
            _save(events)


def last_check() -> dict | None:
    """Read persisted rule-level coverage from the last successful request."""
    try:
        with open(_file(), encoding="utf-8") as handle:
            return json.load(handle).get("last_check")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def mark_checked(checks: dict | None = None) -> str:
    """Persist successful checks even when no condition triggered."""
    with _LOCK, advisory_lock(_file()):
        events = _load()
        _save(events)
        with open(_file(), encoding="utf-8") as handle:
            data = json.load(handle)
        data["last_checked_at"] = _now_iso()
        data["last_check"] = checks
        tmp = _file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, _file())
        return data["last_checked_at"]


def close_from_operation(
    code: str, date: str, price: float, shares: float, cost: float,
    operation_id: str | None = None,
) -> dict:
    """Record a global actual sale and reconcile its position alerts server-side.

    The portfolio receipt is committed first. Repeating the same operation repairs
    an interrupted event save without another sale or touching a reentered lot.
    Partial sales keep risk open; full sales handle only the matching position.
    Lock order matches close_from_event (alerts, then portfolio).
    """
    import portfolio as pf

    receipt_id = operation_id or f"manual_sale:{uuid4().hex}"
    with _LOCK, advisory_lock(_file()):
        events = _load()
        portfolio = pf.close_position(code, date, price, shares, cost, receipt_id)
        receipt = next(row for row in portfolio["closed"] if row.get("operation_id") == receipt_id)
        position_id = receipt.get("position_id")
        stamp = _now_iso()
        changed = False
        for event in events:
            if event.get("symbol") != code or event.get("status") != "OPEN":
                continue
            legacy_closed = (
                not event.get("position_id")
                and receipt.get("position_closed")
                and not any(holding["code"] == code for holding in portfolio["holdings"])
            )
            if not legacy_closed and (not position_id or event.get("position_id") != position_id):
                continue
            sale = {key: receipt[key] for key in ("operation_id", "date", "price", "shares")}
            if event.get("last_sale") == sale:
                continue
            event.update(last_sale=sale, updated_at=stamp)
            if receipt.get("position_closed"):
                event.update(
                    status="HANDLED", condition_active=False, handled_at=stamp,
                    handled_note=f"已回录全部卖出；本次 {shares:g} 股 @ {price:g}",
                    operation_id=receipt_id,
                )
            changed = True
        if changed:
            _save(events)
        return portfolio


def close_from_event(event_id: str, date: str, price: float, shares: float) -> dict:
    """Recoverable two-file disposition: portfolio persists the idempotency key first."""
    import portfolio as pf

    with _LOCK, advisory_lock(_file()):
        events = _load()
        event = next((e for e in events if e["id"] == event_id), None)
        if event is None:
            raise ValueError("提醒不存在")
        operation_id = f"alert:{event_id}"
        if event["status"] != "OPEN":
            # A monitor may expire the event between the durable sale and a failed
            # event write. The portfolio receipt, not the alert status, proves it
            # is safe to finish that interrupted disposition without another sale.
            receipt_exists = any(
                closed.get("operation_id") == operation_id
                for closed in pf.get_portfolio().get("closed", [])
            )
            if not receipt_exists:
                raise ValueError("提醒已处理，不能重复回录")
        portfolio = pf.close_position(
            event["symbol"], date, price, shares, 0, operation_id, require_holding=True,
            expected_position_id=event.get("position_id") or None,
        )
        sale_receipt = next(
            (
                closed
                for closed in portfolio.get("closed", [])
                if closed.get("operation_id") == operation_id
            ),
            None,
        )
        position_closed = bool(sale_receipt and sale_receipt.get("position_closed"))
        stamp = _now_iso()
        for target in events:
            if target["id"] == event_id:
                # A partial sale must leave the triggering condition OPEN.  The
                # position still exists and the next check must be able to keep
                # showing/notify the risk; marking it HANDLED here would make a
                # once-frequency alert disappear until its condition resets.
                target.update(
                    last_sale={
                        "operation_id": operation_id,
                        "date": date,
                        "price": price,
                        "shares": shares,
                    },
                    updated_at=stamp,
                    operation_id=operation_id,
                )
                if position_closed:
                    target.update(
                        status="HANDLED",
                        condition_active=False,
                        handled_at=stamp,
                        handled_note=f"已回录全部卖出 {shares:g} 股 @ {price:g}",
                    )
            elif (
                position_closed
                and target["symbol"] == event["symbol"]
                and target["status"] == "OPEN"
                and (
                    not target.get("position_id")
                    or target.get("position_id") == event.get("position_id")
                )
            ):
                target.update(
                    status="HANDLED",
                    condition_active=False,
                    handled_at=stamp,
                    handled_note=f"已回录全部卖出 {shares:g} 股 @ {price:g}",
                    updated_at=stamp,
                    operation_id=operation_id,
                )
        _save(events)
        return {"event": event, "portfolio": portfolio}


__all__ = ["list_events", "transition", "upsert_from_reminders"]
