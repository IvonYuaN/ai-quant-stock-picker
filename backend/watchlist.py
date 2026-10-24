"""Server-side audit journal for stocks removed from the browser watchlist.

The watchlist itself remains a browser-private collection.  This module only
records a successful removal so the merged ``我的持仓`` view can explain why a
candidate left the user's monitoring scope.  Records are tenant isolated and
are deliberately independent from strategy snapshots and risk rules.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterable
from uuid import uuid4

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock

import tenant

CACHE_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".vibe-research"
)
MAX_REMOVAL_RECORDS = 20
_CODE_RE = re.compile(r"^\d{6}$")
_LOCK = threading.RLock()


def _tenant_dir() -> str:
    tid = tenant.current_tenant.get()
    return CACHE_DIR if tid == "local" else os.path.join(CACHE_DIR, "users", tid)


def _file() -> str:
    return os.path.join(_tenant_dir(), "watchlist_removals.json")


def _now_iso() -> str:
    return now_shanghai().isoformat(timespec="seconds")


def _load() -> list[dict]:
    path = _file()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        # Keep a damaged journal available for recovery instead of replacing it
        # with an empty list on the next removal.
        try:
            os.replace(path, path + ".corrupt")
        except OSError:
            pass
        return []
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _save(records: list[dict]) -> None:
    os.makedirs(_tenant_dir(), exist_ok=True)
    path = _file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(
            {"version": 1, "updated": _now_iso(), "records": records[-MAX_REMOVAL_RECORDS:]},
            handle,
            ensure_ascii=False,
            indent=1,
        )
    os.replace(tmp, path)


def _normalize_codes(codes: Iterable[str]) -> list[str]:
    if isinstance(codes, (str, bytes)):
        raise ValueError("移出监控的代码必须是代码列表")
    normalized: list[str] = []
    for value in codes:
        code = str(value).strip()
        if not _CODE_RE.fullmatch(code):
            raise ValueError("移出监控的代码必须是 6 位数字")
        if code not in normalized:
            normalized.append(code)
    if not normalized:
        raise ValueError("至少提供一只移出监控的股票")
    return normalized


def record_removal(codes: Iterable[str], operation_id: str | None = None) -> dict:
    """Persist one successful browser-watchlist removal, idempotently."""
    normalized = _normalize_codes(codes)
    operation = str(operation_id or "").strip() or f"watchlist-remove:{uuid4().hex}"
    if len(operation) > 128:
        raise ValueError("操作标识过长")
    with _LOCK, advisory_lock(_file()):
        records = _load()
        for record in records:
            if record.get("operation_id") == operation:
                if record.get("codes") != normalized:
                    raise ValueError("重复操作标识的移出内容不同")
                return dict(record)
        record = {
            "id": uuid4().hex,
            "operation_id": operation,
            "codes": normalized,
            "removed_at": _now_iso(),
        }
        records.append(record)
        _save(records)
        return dict(record)


def list_removals(limit: int = MAX_REMOVAL_RECORDS) -> list[dict]:
    """Return newest removal records, newest first."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit 必须是正整数")
    with _LOCK:
        records = _load()
    return [dict(record) for record in reversed(records[-limit:])]


__all__ = ["list_removals", "record_removal"]
