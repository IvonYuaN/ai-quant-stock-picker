"""Tenant-scoped market-wide abnormal-pool observation rules."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
import threading
from typing import Any
from uuid import uuid4

import portfolio as pf
import tenant
from market import ShortTermEmotionSnapshot
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock


_LOCK = threading.RLock()
_EVENT_TYPES = {"limit_up", "blown_up", "limit_down"}
_SEVERITIES = {"info", "warn", "critical"}
MAX_ACTIVE_RULES = 20
MAX_COOLDOWN_MINUTES = 1440


class RuleConflict(ValueError):
    """A market-event rule changed after the client read it."""


def _file() -> str:
    tid = tenant.current_tenant.get()
    root = pf.CACHE_DIR if tid == "local" else os.path.join(pf.CACHE_DIR, "users", tid)
    return os.path.join(root, "market_event_rules.json")


def _load() -> dict[str, Any]:
    path = _file()
    if not os.path.isfile(path):
        return {"version": 1, "items": []}
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("items"), list):
        raise ValueError("全市场异动规则文件格式无效，已停止写入以保护原记录")
    return payload


def _save(payload: dict[str, Any]) -> None:
    path = _file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def list_rules() -> list[dict[str, Any]]:
    """Read market-event rules owned by the current tenant."""
    with _LOCK:
        return deepcopy(_load()["items"])


def active_rules() -> list[dict[str, Any]]:
    """Return enabled rules without modifying their stored snapshots."""
    return [row for row in list_rules() if row.get("status") == "active"]


def create_rule(value: dict[str, Any]) -> dict[str, Any]:
    """Create a market-wide count threshold for one existing abnormal-pool type."""
    event_type = value.get("event_type")
    min_count = value.get("min_count")
    severity = value.get("severity", "info")
    cooldown_minutes = value.get("cooldown_minutes", 0)
    note = value.get("note", "")
    if event_type not in _EVENT_TYPES:
        raise ValueError("全市场异动类型无效")
    if type(min_count) is not int or min_count < 1 or min_count > 10000:
        raise ValueError("触发数量必须是 1 至 10,000 的整数")
    if severity not in _SEVERITIES:
        raise ValueError("提醒级别必须是 info、warn 或 critical")
    if type(cooldown_minutes) is not int or not 0 <= cooldown_minutes <= MAX_COOLDOWN_MINUTES:
        raise ValueError(f"冷却时间必须是 0 至 {MAX_COOLDOWN_MINUTES} 分钟的整数")
    if not isinstance(note, str) or len(note.strip()) > 300:
        raise ValueError("备注最多 300 个字符")

    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        if sum(row.get("status") == "active" for row in payload["items"]) >= MAX_ACTIVE_RULES:
            raise ValueError(f"最多同时启用 {MAX_ACTIVE_RULES} 条全市场异动规则")
        if any(
            row.get("status") == "active"
            and row.get("event_type") == event_type
            and row.get("min_count") == min_count
            for row in payload["items"]
        ):
            raise ValueError("相同类型和触发数量的活动规则已存在")
        stamp = now_shanghai().isoformat(timespec="seconds")
        row = {
            "id": uuid4().hex,
            "event_type": event_type,
            "min_count": min_count,
            "severity": severity,
            "cooldown_minutes": cooldown_minutes,
            "note": note.strip(),
            "status": "active",
            "revision": 1,
            "created_at": stamp,
            "updated_at": stamp,
            "last_check": None,
            "audit": [{"action": "create", "at": stamp}],
        }
        payload["items"].append(row)
        _save(payload)
        return deepcopy(row)


def update_rule(rule_id: str, *, status: str, expected_revision: int) -> dict[str, Any] | None:
    """Pause or resume one tenant rule with compare-and-swap revision control."""
    if status not in {"active", "paused"}:
        raise ValueError("规则状态必须是 active 或 paused")
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("expected_revision 必须是正整数")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        row = next((item for item in payload["items"] if item.get("id") == rule_id), None)
        if row is None:
            return None
        if row.get("revision") != expected_revision:
            raise RuleConflict("全市场异动规则已变化，请刷新后重试")
        if status == "active" and row.get("status") != "active":
            duplicate = next(
                (
                    item for item in payload["items"]
                    if item.get("id") != rule_id
                    and item.get("status") == "active"
                    and item.get("event_type") == row.get("event_type")
                    and item.get("min_count") == row.get("min_count")
                ),
                None,
            )
            if duplicate:
                raise ValueError("相同类型和触发数量的活动规则已存在")
            if sum(item.get("status") == "active" for item in payload["items"]) >= MAX_ACTIVE_RULES:
                raise ValueError(f"最多同时启用 {MAX_ACTIVE_RULES} 条全市场异动规则")
        if row.get("status") != status:
            stamp = now_shanghai().isoformat(timespec="seconds")
            row["audit"].append({"action": status, "at": stamp, "previous_status": row["status"]})
            row["status"] = status
            row["revision"] += 1
            row["updated_at"] = stamp
            _save(payload)
        return deepcopy(row)


def check_rules(
    snapshot: ShortTermEmotionSnapshot,
    *,
    checked_at: datetime,
) -> dict[str, Any]:
    """Evaluate rules only against today's complete abnormal pools and a fresh cache."""
    if checked_at.tzinfo is None:
        raise ValueError("checked_at 必须携带时区")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        triggered: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        checked_rule_ids: set[str] = set()
        checked = 0
        changed = False
        data = snapshot.data if isinstance(snapshot.data, dict) else {}
        trade_date = str(data.get("date") or "")
        issue = ""
        if snapshot.freshness != "fresh":
            issue = "市场异动池缓存不是新鲜数据"
        elif not trade_date:
            issue = "市场异动池缺少实际交易日"
        elif trade_date != checked_at.date().isoformat():
            issue = "市场异动池交易日不是今天"
        pools = data.get("abnormal_pools") if isinstance(data.get("abnormal_pools"), dict) else {}
        for row in payload["items"]:
            if row.get("status") != "active":
                checked_rule_ids.add(str(row.get("id")))
                continue
            checked += 1
            pool = pools.get(row.get("event_type"))
            count = pool.get("count") if isinstance(pool, dict) else None
            if issue or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                reason = issue or "异动池缺少有效计数"
                skipped.append({"rule_id": str(row["id"]), "reason": reason})
                row["last_check"] = {
                    "checked_at": checked_at.isoformat(),
                    "trade_date": trade_date or None,
                    "freshness": snapshot.freshness,
                    "source": snapshot.source,
                    "triggered": False,
                    "issue": reason,
                }
                row["updated_at"] = checked_at.isoformat(timespec="seconds")
                changed = True
                continue
            items = pool.get("items") if isinstance(pool.get("items"), list) else []
            checked_rule_ids.add(str(row["id"]))
            matched = count >= int(row["min_count"])
            row["last_check"] = {
                "checked_at": checked_at.isoformat(),
                "trade_date": trade_date,
                "freshness": "fresh",
                "source": snapshot.source,
                "cached_at": snapshot.cached_at,
                "count": count,
                "triggered": matched,
            }
            row["updated_at"] = checked_at.isoformat(timespec="seconds")
            changed = True
            if matched:
                triggered.append({
                    "id": row["id"],
                    "revision": row["revision"],
                    "event_type": row["event_type"],
                    "min_count": row["min_count"],
                    "count": count,
                    "severity": row["severity"],
                    "cooldown_minutes": row["cooldown_minutes"],
                    "note": row["note"],
                    "trade_date": trade_date,
                    "source": snapshot.source,
                    "cache_age_seconds": snapshot.age_seconds,
                    "cached_at": snapshot.cached_at,
                    "examples": [
                        {"code": str(item.get("code") or ""), "name": str(item.get("name") or "")}
                        for item in items[:10]
                        if isinstance(item, dict) and item.get("code")
                    ],
                })
        if changed:
            _save(payload)
        return {
            "checked": checked,
            "triggered": triggered,
            "skipped": skipped,
            "checked_rule_ids": checked_rule_ids,
            "source": snapshot.source,
            "freshness": snapshot.freshness,
            "trade_date": trade_date or None,
        }
