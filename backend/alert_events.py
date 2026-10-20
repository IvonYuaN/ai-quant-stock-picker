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
    ignore_streak(int, 灵敏度自适应预留), created_at, updated_at

去重键：``(symbol, level, round(threshold_price,2))``——同一风控规则在同一
止损位反复触发不算新事件（PRD：「同一条件重复触发按档位去重」，v1 档位
固定 once，freq_mode 三档控件延后 P1-2）。

外发不在此层：见 ``app.py`` /api/live-risk/check（调 notification_runtime
的现成去重状态机 + notifier 10 通道），本层只管站内事件簿。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone, timedelta

import tenant as _tenant

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".vibe-research"
)
BEIJING = timezone(timedelta(hours=8))
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
    return datetime.now(BEIJING).isoformat(timespec="seconds")


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
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "updated": _now_iso(),
                    "events": events,
                },
                f,
                ensure_ascii=False,
                indent=1,
            )
        os.replace(tmp, path)


def _dedup_key(event: dict) -> str:
    return f"{event.get('symbol')}|{event.get('level')}|{round(float(event.get('threshold_at') or 0), 2)}"


def upsert_from_reminders(reminders: list[dict]) -> list[dict]:
    """把 ``live_risk.SellReminder.to_dict()`` 列表落进事件簿（去重、更新位价）。

    同一去重键已存在且仍 OPEN ⇒ 更新 ``trigger_at``（现价会动）不新建；
    已终态（HANDLED/IGNORED/EXPIRED）⇒ 视为新事件重新 OPEN
    （风控位变了才算新，价格回抽后再次击穿是独立事件）。

    Returns: 本次新增（或重新 OPEN）的事件列表。
    """
    new_events: list[dict] = []
    with _LOCK:
        events = _load()
        by_key = {_dedup_key(e): e for e in events}
        for rem in reminders:
            symbol = str(rem.get("symbol") or "")
            if not symbol:
                continue
            key = f"{symbol}|{rem.get('level')}|{round(float(rem.get('threshold_price') or 0), 2)}"
            existing = by_key.get(key)
            base = {
                "id": existing.get("id") if existing else f"{int(time.time() * 1000)}_{symbol}_{rem.get('level')}",
                "level": rem.get("level"),
                "symbol": symbol,
                "position_id": None,  # 持仓簿无 uuid（P2 再挂），v1 用 symbol 关联
                "trigger_at": rem.get("current_price"),
                "threshold_at": rem.get("threshold_price"),
                "pnl_pct": rem.get("pnl_pct"),
                "sellable_now": rem.get("sellable_now"),
                "action_hint": rem.get("action_hint"),
                "t1_note": rem.get("t1_note", ""),
                "basis_json": rem.get("basis_json") or {},
                "status": "OPEN",
                "handled_at": None,
                "handled_note": "",
                "ignore_streak": 0,
                "created_at": existing.get("created_at") if existing else _now_iso(),
            }
            # PRD 7.3-4：无依据不入库
            if not base["basis_json"].get("rule") or not base["basis_json"].get("actual"):
                continue
            if existing and existing.get("status") == "OPEN":
                # 同键 OPEN：只刷现价与证据，不新增
                existing["trigger_at"] = base["trigger_at"]
                existing["basis_json"] = base["basis_json"]
                existing["updated_at"] = _now_iso()
                continue
            base["updated_at"] = _now_iso()
            events.append(base)
            by_key[key] = base
            new_events.append(base)
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


def transition(event_id: str, action: str, note: str = "") -> dict | None:
    """OPEN → HANDLED / IGNORED（终态）。

    - ``action``: ``handled`` | ``ignored``
    - ``ignored`` 时 ``ignore_streak`` +1（P1-2 灵敏度自适应的数据源）
    - 非 OPEN 状态的操作返回 None（前端据此 409）
    """
    with _LOCK:
        events = _load()
        target = next((e for e in events if e.get("id") == event_id), None)
        if target is None:
            return None
        current = str(target.get("status") or "OPEN")
        dest = "HANDLED" if action == "handled" else "IGNORED"
        if current not in _VALID_TRANSITIONS or dest not in _VALID_TRANSITIONS[current]:
            return None
        target["status"] = dest
        target["handled_at"] = _now_iso()
        target["handled_note"] = note or ""
        if dest == "IGNORED":
            target["ignore_streak"] = int(target.get("ignore_streak") or 0) + 1
        else:
            target["ignore_streak"] = 0
        target["updated_at"] = _now_iso()
        _save(events)
        return target


__all__ = ["list_events", "transition", "upsert_from_reminders"]
