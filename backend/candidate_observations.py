"""Tenant-isolated, human-adopted price observations for unheld candidates.

This store is deliberately separate from portfolio positions: an observation is
not a holding, trade, score, or order. Quote checks reuse the existing monitor
round and only report a condition for human review.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
import os
import re
import threading
from typing import Any
from uuid import uuid4

import portfolio as pf
import tenant
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock

_LOCK = threading.RLock()
_CODE_RE = re.compile(r"^\d{6}$")
_COMPARATORS = {"at_or_above", "at_or_below"}
MAX_ACTIVE_OBSERVATIONS = 50


class CandidateObservationConflict(ValueError):
    """The candidate observation changed since the client read it."""


def _file() -> str:
    tid = tenant.current_tenant.get()
    root = pf.CACHE_DIR if tid == "local" else os.path.join(pf.CACHE_DIR, "users", tid)
    return os.path.join(root, "candidate_observations.json")


def _load() -> dict[str, Any]:
    path = _file()
    if not os.path.isfile(path):
        return {"version": 1, "items": [], "analyses": []}
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("items"), list):
        raise ValueError("候选观察数据格式无效，已停止写入以保护原记录")
    if not isinstance(payload.get("analyses", []), list):
        raise ValueError("候选分析数据格式无效，已停止写入以保护原记录")
    payload.setdefault("analyses", [])
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


def _validate(value: dict[str, Any]) -> None:
    symbol = value.get("symbol")
    price = value.get("price")
    if not isinstance(symbol, str) or not _CODE_RE.fullmatch(symbol):
        raise ValueError("股票代码必须是 6 位数字")
    if value.get("comparator") not in _COMPARATORS:
        raise ValueError("观察方向必须是 at_or_above 或 at_or_below")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError("观察价格必须是有效正数")
    for key, limit in (("condition", 500), ("invalidation", 500), ("source_analysis_id", 128), ("source_snapshot_id", 128), ("source_generated_at", 64), ("source_as_of", 64)):
        text = value.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise ValueError(f"{key} 必须是 1 至 {limit} 个字符")
    for key in ("source_generated_at", "source_as_of"):
        try:
            stamp = datetime.fromisoformat(value[key])
        except ValueError as exc:
            raise ValueError("分析时点必须是带时区的 ISO 8601 时间") from exc
        if stamp.utcoffset() is None:
            raise ValueError("分析时点必须包含时区")
        if stamp > now_shanghai():
            raise ValueError("分析时点不能晚于当前时间")


def list_observations() -> list[dict[str, Any]]:
    """Return the current tenant's observations without fetching market data."""
    with _LOCK:
        payload = _load()
    return deepcopy(payload["items"])


def register_analysis(result: dict[str, Any]) -> None:
    """Persist one complete candidate analysis for strict later adoption."""
    required = ("analysis_id", "symbol", "source_snapshot_id", "source_as_of", "source_price", "generated_at", "status")
    if any(not result.get(key) for key in required) or result.get("status") != "complete":
        raise ValueError("只有完整候选分析可以进入采用链路")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        analyses = [row for row in payload["analyses"] if row.get("analysis_id") != result["analysis_id"]]
        analyses.append(deepcopy(result))
        payload["analyses"] = analyses[-100:]
        _save(payload)


def get_analysis(analysis_id: str) -> dict[str, Any] | None:
    """Read a tenant-owned analysis artifact without refreshing evidence."""
    with _LOCK:
        payload = _load()
    row = next((item for item in payload["analyses"] if item.get("analysis_id") == analysis_id), None)
    return deepcopy(row) if isinstance(row, dict) else None


def adopt_observation(value: dict[str, Any], *, expected_version: int | None = None) -> dict[str, Any]:
    """Persist a human-confirmed candidate condition with retry-safe CAS."""
    _validate(value)
    analysis = get_analysis(value["source_analysis_id"])
    if not analysis or analysis.get("status") != "complete":
        raise CandidateObservationConflict("候选分析不存在或未完成，请重新分析")
    if analysis.get("symbol") != value["symbol"] or analysis.get("source_snapshot_id") != value["source_snapshot_id"]:
        raise CandidateObservationConflict("候选分析与当前标的快照不一致，请重新分析")
    if (analysis.get("generated_at") != value["source_generated_at"]
            or analysis.get("source_as_of") != value["source_as_of"]
            or float(analysis.get("source_price", 0)) != float(value["price"])):
        raise CandidateObservationConflict("采用条件的行情依据已变化，请重新分析")
    try:
        generated_at = datetime.fromisoformat(value["source_generated_at"])
        quote_as_of = datetime.fromisoformat(value["source_as_of"])
        age = (now_shanghai() - generated_at).total_seconds()
        quote_age = (now_shanghai() - quote_as_of).total_seconds()
    except (TypeError, ValueError) as exc:
        raise CandidateObservationConflict("分析或行情时点不可核验，请重新分析") from exc
    from position_followup import FollowupDefaults

    if (generated_at.utcoffset() is None or quote_as_of.utcoffset() is None
            or age < 0 or quote_age < 0 or age > FollowupDefaults().max_check_age_seconds):
        raise CandidateObservationConflict("候选分析或行情已过期，请重新分析后采用")
    from decision_context import position_quote_issue

    if position_quote_issue({"as_of": value["source_as_of"]}):
        raise CandidateObservationConflict("候选行情已过期，请刷新当前依据并重新分析")
    try:
        import aqsp_bridge

        current_candidate = aqsp_bridge.candidate_payload(value["symbol"])
    except Exception as exc:  # noqa: BLE001 -- snapshot failures always reject adoption
        raise CandidateObservationConflict("当前候选快照不可读取，请刷新后重新分析") from exc
    from candidate_discussion import _snapshot_id

    if _snapshot_id(current_candidate) != value["source_snapshot_id"]:
        raise CandidateObservationConflict("候选快照已变化，请重新分析后采用")
    actions = analysis.get("actions") or []
    if not any(
        action.get("action") in {"observe", "buy_review"}
        and action.get("price_ref") == "current"
        and action.get("condition") == value["condition"].strip()
        and action.get("invalidation") == value["invalidation"].strip()
        for action in actions if isinstance(action, dict)
    ):
        raise CandidateObservationConflict("采用条件不属于该候选分析，请重新核对")
    if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
        raise ValueError("expected_version 必须是非负整数")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        active = next((item for item in payload["items"] if item.get("symbol") == value["symbol"] and item.get("state") == "active"), None)
        if active and expected_version != active.get("revision"):
            raise CandidateObservationConflict("该标的观察已在其他页面变化，请刷新后重试")
        if not active and expected_version not in (None, 0):
            raise CandidateObservationConflict("该观察已不存在或已停止，请刷新后重试")
        active_count = sum(row.get("state") == "active" for row in payload["items"])
        if not active and active_count >= MAX_ACTIVE_OBSERVATIONS:
            raise ValueError(f"活跃候选观察最多 {MAX_ACTIVE_OBSERVATIONS} 条，请先停止不再跟踪的条件")
        current = now_shanghai().isoformat(timespec="seconds")
        item = {
            "id": active["id"] if active else uuid4().hex,
            "symbol": value["symbol"],
            "name": str(value.get("name") or value["symbol"])[:80],
            "price": float(value["price"]),
            "comparator": value["comparator"],
            "condition": value["condition"].strip(),
            "invalidation": value["invalidation"].strip(),
            "source_analysis_id": value["source_analysis_id"].strip(),
            "source_snapshot_id": value["source_snapshot_id"].strip(),
            "source_generated_at": value["source_generated_at"],
            "source_as_of": value["source_as_of"],
            "source_quote_as_of": analysis.get("source_as_of"),
            "state": "active",
            "revision": (active.get("revision", 0) + 1) if active else 1,
            "created_at": active.get("created_at", current) if active else current,
            "updated_at": current,
            "last_check": active.get("last_check") if active else None,
            "audit": (active.get("audit", []) if active else []) + [{"action": "adopt", "at": current, "previous": deepcopy(active)}],
        }
        payload["items"] = [item if row.get("id") == item["id"] else row for row in payload["items"]]
        if not active:
            payload["items"].append(item)
        _save(payload)
    return deepcopy(item)


def stop_observation(observation_id: str, expected_version: int) -> dict[str, Any] | None:
    """Stop one observation using compare-and-swap and preserve audit history."""
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        item = next((row for row in payload["items"] if row.get("id") == observation_id), None)
        if not item:
            return None
        if type(expected_version) is not int or item.get("revision") != expected_version:
            raise CandidateObservationConflict("观察已变化，请刷新后重试")
        if item.get("state") == "active":
            current = now_shanghai().isoformat(timespec="seconds")
            item["state"] = "stopped"
            item["revision"] += 1
            item["updated_at"] = current
            item.setdefault("audit", []).append({"action": "stop", "at": current})
            _save(payload)
        return deepcopy(item)


def check_observations(
    quotes: dict[str, dict[str, Any]], *, checked_at: datetime | None = None,
    held_symbols: set[str] | None = None,
) -> dict[str, Any]:
    """Record timely price facts from the current monitor batch; never trade."""
    from decision_context import position_quote_issue

    current = checked_at or now_shanghai()
    path = _file()
    checked: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    with _LOCK, advisory_lock(path):
        payload = _load()
        changed = False
        for item in payload["items"]:
            if item.get("state") != "active":
                continue
            if item["symbol"] in (held_symbols or set()):
                skipped.append({"symbol": item["symbol"], "reason": "该标的已转为持仓，请在持仓策略区复核或停止候选观察"})
                continue
            quote = quotes.get(item["symbol"])
            issue = position_quote_issue(quote or {}, checked_at=current)
            price = quote.get("price") if isinstance(quote, dict) else None
            if issue or isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                skipped.append({"symbol": item["symbol"], "reason": issue or "缺少有效现价"})
                continue
            triggered = price >= item["price"] if item["comparator"] == "at_or_above" else price <= item["price"]
            quote_as_of = quote.get("as_of")
            if not isinstance(quote_as_of, str) or not quote_as_of:
                from astock import parse_gtimg_time

                quote_as_of = parse_gtimg_time(quote.get("quote_time"))
            record = {"price": float(price), "quote_as_of": quote_as_of, "checked_at": current.isoformat(), "triggered": triggered}
            if item.get("last_check") != record:
                item["last_check"] = record
                changed = True
            checked.append({"id": item["id"], "symbol": item["symbol"], **record,
                            "condition": item["condition"], "invalidation": item["invalidation"]})
        if changed:
            _save(payload)
    return {"checked": checked, "skipped": skipped}


__all__ = ["adopt_observation", "check_observations", "get_analysis", "list_observations", "register_analysis", "stop_observation"]
