"""Tenant-scoped, human-authored single-symbol price observation rules."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
import re
import threading
from typing import Any
from uuid import uuid4

import portfolio as pf
import tenant
from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.utils.jsonl_io import advisory_lock

_LOCK = threading.RLock()
_CODE = re.compile(r"^\d{6}$")
_COMPARATORS = {"at_or_above", "at_or_below"}
_COMBINATIONS = {"all", "any"}
_SEVERITIES = {"info", "warn", "critical"}
MAX_ACTIVE_RULES = 50
MAX_RULE_CONDITIONS = 5
MAX_COOLDOWN_MINUTES = 1440
_SIGNAL_ID = re.compile(r"^[a-z0-9_]{1,40}$")


@dataclass(frozen=True)
class IndicatorSnapshot:
    value: float | None
    as_of: str | None
    source: str
    issue: str | None = None


@dataclass(frozen=True)
class SignalSnapshot:
    triggered: bool | None
    as_of: str | None
    source: str
    signal_name: str
    signal_revision: int
    definition_sha256: str
    current_price: float | None = None
    issue: str | None = None


class RuleConflict(ValueError):
    """A rule changed after the client read it."""


def _signal_snapshot_digest(snapshot: dict[str, Any]) -> str:
    payload = {
        key: snapshot[key]
        for key in ("id", "name", "kind", "enabled", "conditions", "revision")
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file() -> str:
    tid = tenant.current_tenant.get()
    root = pf.CACHE_DIR if tid == "local" else os.path.join(pf.CACHE_DIR, "users", tid)
    return os.path.join(root, "price_observation_rules.json")


def _load() -> dict[str, Any]:
    path = _file()
    if not os.path.isfile(path):
        return {"version": 1, "items": []}
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("items"), list):
        raise ValueError("价格观察规则文件格式无效，已停止写入以保护原记录")
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
    """Read rules owned by the current tenant."""
    with _LOCK:
        return deepcopy(_load()["items"])


def active_rules() -> list[dict[str, Any]]:
    """Return enabled rules without changing their stored snapshots."""
    return [row for row in list_rules() if row.get("status") == "active"]


def _conditions(value: dict[str, Any]) -> list[dict[str, float | str]]:
    raw = value.get("conditions")
    if raw is None:
        raw = [{"comparator": value.get("comparator"), "price": value.get("price")}]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_RULE_CONDITIONS:
        raise ValueError(f"价格条件数量必须为 1 至 {MAX_RULE_CONDITIONS} 条")
    result: list[dict[str, float | str]] = []
    for index, condition in enumerate(raw, start=1):
        if not isinstance(condition, dict):
            raise ValueError(f"第 {index} 条价格条件格式无效")
        comparator = condition.get("comparator")
        price = condition.get("price")
        if comparator not in _COMPARATORS:
            raise ValueError(f"第 {index} 条观察方向无效")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise ValueError(f"第 {index} 条观察价格必须是有效正数")
        result.append({"comparator": comparator, "price": float(price)})
    return result


def _stored_conditions(value: dict[str, Any]) -> list[dict[str, float | str]]:
    """Read new condition arrays and migrate legacy single-price rows in memory."""
    raw = value.get("conditions")
    if isinstance(raw, list) and raw:
        return raw
    return [{"comparator": value.get("comparator"), "price": value.get("price")}]


def create_rule(value: dict[str, Any]) -> dict[str, Any]:
    """Create a tenant-scoped price rule; hits only ask the user to review."""
    symbol = value.get("symbol")
    rule_type = value.get("rule_type", "price")
    signal_id: str | None = None
    signal_snapshot: dict[str, Any] | None = None
    signal_snapshot_sha256: str | None = None
    if rule_type not in {"price", "indicator", "signal"}:
        raise ValueError("观察规则类型必须是 price、indicator 或 signal")
    if rule_type == "signal":
        signal_id = value.get("signal_id")
        if not isinstance(signal_id, str) or not _SIGNAL_ID.fullmatch(signal_id):
            raise ValueError("条件信号标识无效")
        import signal_definitions

        definition = signal_definitions.get_signal(signal_id)
        if definition is None:
            raise ValueError("条件信号不存在或已移出当前租户")
        if definition.get("enabled") is not True:
            raise ValueError("请先启用该条件信号")
        signal_snapshot = {
            key: definition[key]
            for key in ("id", "name", "kind", "enabled", "conditions", "revision")
        }
        signal_snapshot_sha256 = hashlib.sha256(
            json.dumps(signal_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        conditions = []
        metric = None
        comparator = None
        threshold = None
    elif rule_type == "indicator":
        from aqsp.indicators import monitor_indicator_spec

        conditions = []
        metric = value.get("metric")
        spec = monitor_indicator_spec(metric)
        comparator = value.get("comparator")
        threshold = value.get("threshold")
        if comparator not in _COMPARATORS:
            raise ValueError("指标比较方向无效")
        threshold = spec.validate_threshold(threshold)
    else:
        conditions = _conditions(value)
        metric = None
        comparator = conditions[0]["comparator"]
        threshold = conditions[0]["price"]
    combination = value.get("combination", "all")
    severity = value.get("severity", "info")
    cooldown_minutes = value.get("cooldown_minutes", 0)
    note = value.get("note", "")
    if not isinstance(symbol, str) or not _CODE.fullmatch(symbol):
        raise ValueError("股票代码必须是 6 位数字")
    if combination not in _COMBINATIONS:
        raise ValueError("条件关系必须是 all 或 any")
    if severity not in _SEVERITIES:
        raise ValueError("提醒级别必须是 info、warn 或 critical")
    if type(cooldown_minutes) is not int or not 0 <= cooldown_minutes <= MAX_COOLDOWN_MINUTES:
        raise ValueError(f"冷却时间必须是 0 至 {MAX_COOLDOWN_MINUTES} 分钟的整数")
    if not isinstance(note, str) or len(note.strip()) > 300:
        raise ValueError("备注最多 300 个字符")
    with pf._LOCK:
        held = any(row.get("code") == symbol for row in pf._load().get("holdings", []))
    if held:
        raise ValueError("该股票已在持仓中，请在持仓工作区设置处理条件")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        if sum(row.get("status") == "active" for row in payload["items"]) >= MAX_ACTIVE_RULES:
            raise ValueError(f"最多同时启用 {MAX_ACTIVE_RULES} 条价格观察规则")
        duplicate = next((row for row in payload["items"] if row.get("status") == "active"
                          and row.get("symbol") == symbol
                          and row.get("rule_type", "price") == rule_type
                          and row.get("signal_id") == signal_id
                          and row.get("metric") == metric
                          and (rule_type == "signal" or _stored_conditions(row) == conditions)
                          and row.get("comparator") == comparator
                          and row.get("threshold", row.get("price")) == threshold
                          and row.get("combination", "all") == combination), None)
        if duplicate:
            raise ValueError("相同股票和价位的观察规则已存在")
        stamp = now_shanghai().isoformat(timespec="seconds")
        row = {
            "id": uuid4().hex, "symbol": symbol,
            "rule_type": rule_type, "metric": metric,
            "signal_id": signal_id,
            "signal_snapshot": signal_snapshot,
            "signal_snapshot_sha256": signal_snapshot_sha256,
            "comparator": comparator,
            "price": conditions[0]["price"] if conditions else None,
            "threshold": threshold,
            "conditions": conditions, "combination": combination,
            "severity": severity, "cooldown_minutes": cooldown_minutes,
            "note": note.strip(), "status": "active",
            "revision": 1, "created_at": stamp, "updated_at": stamp,
            "last_check": None, "audit": [{"action": "create", "at": stamp}],
        }
        payload["items"].append(row)
        _save(payload)
        return deepcopy(row)


def update_rule(rule_id: str, *, status: str, expected_revision: int) -> dict[str, Any] | None:
    """Pause or resume one rule using tenant-local compare-and-swap."""
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
            raise RuleConflict("观察规则已变化，请刷新后重试")
        if row.get("status") == "transferred":
            raise ValueError("该规则已移交到持仓工作区，不能恢复候选价格观察")
        if status == "active" and row.get("status") != "active":
            with pf._LOCK:
                held = any(item.get("code") == row.get("symbol") for item in pf._load().get("holdings", []))
            if held:
                raise ValueError("该股票已在持仓中，请在持仓工作区设置处理条件")
            duplicate = next((item for item in payload["items"]
                              if item.get("id") != rule_id and item.get("status") == "active"
                              and item.get("symbol") == row.get("symbol")
                              and item.get("rule_type", "price") == row.get("rule_type", "price")
                              and item.get("signal_id") == row.get("signal_id")
                              and item.get("metric") == row.get("metric")
                              and (row.get("rule_type") == "signal" or _stored_conditions(item) == _stored_conditions(row))
                              and item.get("comparator") == row.get("comparator")
                              and item.get("threshold", item.get("price")) == row.get("threshold", row.get("price"))
                              and item.get("combination", "all") == row.get("combination", "all")), None)
            if duplicate:
                raise ValueError("相同股票和组合条件的活动观察规则已存在")
            if sum(item.get("status") == "active" for item in payload["items"]) >= MAX_ACTIVE_RULES:
                raise ValueError(f"最多同时启用 {MAX_ACTIVE_RULES} 条价格观察规则")
        if row.get("status") != status:
            stamp = now_shanghai().isoformat(timespec="seconds")
            row["audit"].append({"action": status, "at": stamp, "previous_status": row["status"]})
            row["status"] = status
            row["revision"] += 1
            row["updated_at"] = stamp
            _save(payload)
        return deepcopy(row)


def check_rules(
    quotes: dict[str, dict[str, Any]], *, checked_at: datetime | None = None,
    held_positions: dict[str, str] | None = None,
    indicator_values: dict[tuple[str, str], IndicatorSnapshot] | None = None,
    signal_values: dict[tuple[str, str], SignalSnapshot] | None = None,
) -> dict[str, Any]:
    """Evaluate active rules only against quotes accepted by the freshness contract."""
    from decision_context import position_quote_issue

    current = checked_at or now_shanghai()
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        rows = payload["items"]
        triggered: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        checked_rule_ids: set[str] = set()
        transferred: list[dict[str, str]] = []
        checked = 0
        changed = False
        for row in rows:
            if row.get("status") == "active" and row.get("symbol") in (held_positions or {}):
                position_id = str((held_positions or {})[row["symbol"]])
                stamp = current.isoformat()
                row["status"] = "transferred"
                row["transferred_to_position_id"] = position_id
                row["revision"] += 1
                row["updated_at"] = stamp
                row.setdefault("audit", []).append({
                    "action": "position_handoff", "at": stamp,
                    "position_id": position_id,
                })
                transferred.append({"symbol": row["symbol"], "position_id": position_id})
                checked_rule_ids.add(str(row["id"]))
                changed = True
                continue
            if row.get("status") != "active":
                checked_rule_ids.add(str(row["id"]))
                continue
            checked += 1
            if row.get("rule_type") == "signal":
                signal_id = str(row.get("signal_id") or "")
                snapshot = (signal_values or {}).get((str(row.get("symbol")), signal_id))
                issue = snapshot.issue if snapshot else "缺少条件信号计算结果"
                pinned_definition = row.get("signal_snapshot")
                stored_digest = row.get("signal_snapshot_sha256")
                if not issue and (
                    not isinstance(pinned_definition, dict)
                    or not isinstance(stored_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", stored_digest)
                ):
                    issue = "条件信号修订快照无效"
                elif not issue:
                    try:
                        if _signal_snapshot_digest(pinned_definition) != stored_digest:
                            issue = "条件信号修订快照校验失败"
                    except (KeyError, TypeError, ValueError):
                        issue = "条件信号修订快照格式无效"
                if not issue and (
                    snapshot is None
                    or snapshot.source != "sqlite_db"
                    or not snapshot.as_of
                ):
                    issue = "条件信号来源或时点缺失"
                if not issue and snapshot is not None:
                    try:
                        snapshot_day = datetime.fromisoformat(snapshot.as_of).date()
                    except (TypeError, ValueError):
                        issue = "条件信号数据日期无效"
                    else:
                        if snapshot_day != latest_completed_trading_day(current):
                            issue = "条件信号数据日期陈旧"
                if not issue and (
                    snapshot is None
                    or type(snapshot.triggered) is not bool
                    or not snapshot.signal_name
                    or snapshot.signal_revision < 1
                    or not re.fullmatch(r"[0-9a-f]{64}", snapshot.definition_sha256)
                ):
                    issue = "条件信号定义或结果无效"
                if not issue and (
                    snapshot is None
                    or isinstance(snapshot.current_price, bool)
                    or not isinstance(snapshot.current_price, (int, float))
                    or not math.isfinite(float(snapshot.current_price))
                    or snapshot.current_price <= 0
                ):
                    issue = "条件信号原始收盘价无效"
                if issue:
                    skipped.append({"symbol": row["symbol"], "reason": issue})
                    row["last_check"] = {
                        "price": None,
                        "as_of": snapshot.as_of if snapshot else None,
                        "checked_at": current.isoformat(),
                        "triggered": False,
                        "source": snapshot.source if snapshot else "",
                        "issue": issue,
                        "signal_id": signal_id,
                        "signal_revision": snapshot.signal_revision if snapshot else None,
                        "freshness": "stale" if "陈旧" in issue else "missing",
                    }
                    changed = True
                    continue
                assert snapshot is not None
                checked_rule_ids.add(str(row["id"]))
                row["last_check"] = {
                    "price": float(snapshot.current_price),
                    "as_of": snapshot.as_of,
                    "checked_at": current.isoformat(),
                    "triggered": snapshot.triggered,
                    "source": snapshot.source,
                    "issue": None,
                    "signal_id": signal_id,
                    "signal_name": snapshot.signal_name,
                    "signal_revision": snapshot.signal_revision,
                    "definition_sha256": snapshot.definition_sha256,
                    "freshness": "fresh",
                }
                changed = True
                if snapshot.triggered:
                    triggered.append({
                        **deepcopy(row),
                        "current_price": float(snapshot.current_price),
                        "quote_as_of": snapshot.as_of,
                        "source": snapshot.source,
                        "signal_name": snapshot.signal_name,
                        "signal_revision": snapshot.signal_revision,
                        "signal_definition_sha256": snapshot.definition_sha256,
                    })
                continue
            if row.get("rule_type", "price") == "indicator":
                snapshot = (indicator_values or {}).get(
                    (str(row.get("symbol")), str(row.get("metric")))
                )
                issue = snapshot.issue if snapshot else "缺少指标计算结果"
                if not issue and (
                    snapshot is None
                    or snapshot.source != "sqlite_db"
                    or not snapshot.as_of
                ):
                    issue = "指标来源或时点缺失"
                if not issue and snapshot is not None:
                    try:
                        snapshot_day = datetime.fromisoformat(snapshot.as_of).date()
                    except (TypeError, ValueError):
                        issue = "指标数据日期无效"
                    else:
                        if snapshot_day != latest_completed_trading_day(current):
                            issue = "指标数据日期陈旧"
                value = snapshot.value if snapshot else None
                if not issue and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    issue = "指标值无效"
                if issue:
                    skipped.append({"symbol": row["symbol"], "reason": issue})
                    row["last_check"] = {
                        "value": None, "as_of": snapshot.as_of if snapshot else None,
                        "checked_at": current.isoformat(), "triggered": False,
                        "source": snapshot.source if snapshot else "", "issue": issue,
                        "metric": row.get("metric"),
                        "freshness": "stale" if "陈旧" in issue else "missing",
                    }
                    changed = True
                    continue
                checked_rule_ids.add(str(row["id"]))
                threshold = float(row["threshold"])
                hit = (
                    float(value) >= threshold
                    if row["comparator"] == "at_or_above"
                    else float(value) <= threshold
                )
                row["last_check"] = {
                    "value": float(value), "as_of": snapshot.as_of,
                    "checked_at": current.isoformat(), "triggered": hit,
                    "source": snapshot.source, "metric": row["metric"],
                    "threshold": threshold, "comparator": row["comparator"],
                    "freshness": "fresh",
                }
                changed = True
                if hit:
                    triggered.append({
                        **deepcopy(row), "current_price": float(value),
                        "quote_as_of": snapshot.as_of, "source": snapshot.source,
                        "matched_conditions": [{
                            "comparator": row["comparator"], "price": threshold,
                            "triggered": True,
                        }],
                        "indicator_value": float(value),
                    })
                continue
            quote = quotes.get(row["symbol"]) or {}
            issue = position_quote_issue(quote, checked_at=current)
            if issue:
                skipped.append({"symbol": row["symbol"], "reason": issue})
                row["last_check"] = {"price": None, "quote_as_of": None, "checked_at": current.isoformat(), "triggered": False, "source": "", "issue": issue}
                changed = True
                continue
            source = str(quote.get("source") or quote.get("source_name") or "").strip()
            if not source:
                skipped.append({"symbol": row["symbol"], "reason": "行情来源缺失"})
                row["last_check"] = {"price": None, "quote_as_of": None, "checked_at": current.isoformat(), "triggered": False, "source": "", "issue": "行情来源缺失"}
                changed = True
                continue
            price = quote.get("price")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                skipped.append({"symbol": row["symbol"], "reason": "行情价格无效"})
                row["last_check"] = {"price": None, "quote_as_of": None, "checked_at": current.isoformat(), "triggered": False, "source": source, "issue": "行情价格无效"}
                changed = True
                continue
            quote_as_of = str(quote.get("as_of") or quote.get("quote_time") or "")
            try:
                stamp = datetime.fromisoformat(quote_as_of) if "T" in quote_as_of else datetime.strptime(quote_as_of, "%Y%m%d%H%M%S")
            except ValueError:
                skipped.append({"symbol": row["symbol"], "reason": "行情时点无法核验"})
                row["last_check"] = {"price": None, "quote_as_of": None, "checked_at": current.isoformat(), "triggered": False, "source": source, "issue": "行情时点无法核验"}
                changed = True
                continue
            if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > current:
                skipped.append({"symbol": row["symbol"], "reason": "行情时点缺少时区或晚于检查时间"})
                row["last_check"] = {"price": None, "quote_as_of": None, "checked_at": current.isoformat(), "triggered": False, "source": source, "issue": "行情时点缺少时区或晚于检查时间"}
                changed = True
                continue
            checked_rule_ids.add(str(row["id"]))
            condition_results = [
                {
                    **condition,
                    "triggered": price >= condition["price"]
                    if condition["comparator"] == "at_or_above"
                    else price <= condition["price"],
                }
                for condition in _stored_conditions(row)
            ]
            combination = row.get("combination", "all")
            hit = (
                all(item["triggered"] for item in condition_results)
                if combination == "all"
                else any(item["triggered"] for item in condition_results)
            )
            row["last_check"] = {
                "price": float(price), "quote_as_of": quote_as_of,
                "checked_at": current.isoformat(), "triggered": hit,
                "source": source, "matched_conditions": condition_results,
                "combination": combination,
            }
            changed = True
            if hit:
                triggered.append({**deepcopy(row), "current_price": float(price), **row["last_check"]})
        if changed:
            _save(payload)
        return {
            "checked": checked, "checked_rule_ids": checked_rule_ids,
            "triggered": triggered, "skipped": skipped,
            "transferred": transferred,
        }


__all__ = ["IndicatorSnapshot", "SignalSnapshot", "RuleConflict", "active_rules", "check_rules", "create_rule", "list_rules", "update_rule"]
