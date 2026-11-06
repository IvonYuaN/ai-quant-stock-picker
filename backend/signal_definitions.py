"""Tenant-scoped user-defined boolean signals, separate from numeric factors."""
from __future__ import annotations

import json
import math
import os
import re
import threading
from typing import Any

import pandas as pd
import portfolio as pf
import tenant
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


_LOCK = threading.RLock()
_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_OPS = frozenset({">", ">=", "<", "<=", "==", "!="})
MAX_CONDITIONS = 8
MAX_DAYS = 60
_PRICE_FIELDS = frozenset({"open", "high", "low", "close", "prev_close"})


class SignalDefinitionConflict(ValueError):
    """A signal revision changed after the editor read it."""


def _file() -> str:
    tenant_id = tenant.current_tenant.get()
    root = pf.CACHE_DIR if tenant_id == "local" else os.path.join(pf.CACHE_DIR, "users", tenant_id)
    return os.path.join(root, "signal_definitions.json")


def _load(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"schema_version": 1, "items": []}
    try:
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("信号定义库不可读取，已停止写入以保护原记录") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("items"), list)
        or any(not isinstance(item, dict) for item in payload["items"])
    ):
        raise ValueError("信号定义库格式无效，已停止写入以保护原记录")
    return payload


def _save(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def list_signals() -> list[dict[str, Any]]:
    with _LOCK:
        items = _load(_file())["items"]
    return sorted(items, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def get_signal(signal_id: str) -> dict[str, Any] | None:
    """Return one current-tenant signal definition, if it exists."""
    return next((item for item in list_signals() if item.get("id") == signal_id), None)


def options() -> dict[str, Any]:
    import factor_trials

    return {
        "fields": sorted(factor_trials.TRIAL_FIELDS),
        "operators": sorted(_OPS),
        "max_conditions": MAX_CONDITIONS,
        "max_days": MAX_DAYS,
        "consumer_status": "monitor_snapshot_and_historical_scan",
    }


def needs_pit_factors(signal: dict[str, Any]) -> bool:
    """Return whether any cross-session price operand needs an as-of price basis."""
    return bool(price_factor_offsets(signal))


def price_factor_offsets(signal: dict[str, Any]) -> tuple[int, ...]:
    """Return bar offsets whose price operands require dated PIT factors."""
    offsets: set[int] = set()
    for condition in signal.get("conditions", []):
        left = str(condition.get("left", ""))
        left_offset = int(condition.get("left_days", 0) or 0) + (left == "prev_close")
        if left in _PRICE_FIELDS and left_offset > 0:
            offsets.add(left_offset)
        right = condition.get("right")
        right_field = right[6:] if isinstance(right, str) and right.startswith("field:") else ""
        right_offset = int(condition.get("right_days", 0) or 0) + (right_field == "prev_close")
        if right_field in _PRICE_FIELDS and right_offset > 0:
            offsets.add(right_offset)
    return tuple(sorted(offsets))


def max_lookback(signal: dict[str, Any]) -> int:
    """Return the greatest required prior trading-bar offset in a definition."""
    offsets = [0]
    for condition in signal.get("conditions", []):
        offsets.append(int(condition.get("left_days", 0) or 0) + (condition.get("left") == "prev_close"))
        right = condition.get("right")
        right_field = right[6:] if isinstance(right, str) and right.startswith("field:") else ""
        offsets.append(int(condition.get("right_days", 0) or 0) + (right_field == "prev_close"))
    return max(offsets)


def evaluate_signal_series(signal: dict[str, Any], frame: pd.DataFrame) -> pd.Series:
    """Evaluate one signal per daily row on a single symbol's ascending raw-bar frame.

    Required frame columns are ``date``, raw OHLCV fields, optional ``adj_factor``.
    Price fields shifted to earlier sessions are mapped to each result date's PIT
    price basis; missing PIT factors make those result rows non-triggering.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty or "date" not in frame:
        raise DataError("信号计算需要非空且含日期的日线数据")
    ordered = frame.copy()
    parsed_dates = pd.to_datetime(ordered["date"], errors="coerce")
    if parsed_dates.isna().any():
        raise DataError("信号计算遇到无效交易日")
    ordered["_signal_date"] = parsed_dates.dt.strftime("%Y-%m-%d")
    ordered = ordered.sort_values("_signal_date", kind="stable").reset_index(drop=True)
    if ordered["_signal_date"].duplicated().any():
        raise DataError("信号计算遇到重复交易日")
    if "close" not in ordered:
        raise DataError("信号计算缺少 close 日线字段")
    ordered["raw_close"] = pd.to_numeric(ordered.get("raw_close", ordered["close"]), errors="coerce")
    ordered["prev_close"] = pd.to_numeric(ordered["close"], errors="coerce").shift(1)
    factors = pd.to_numeric(ordered.get("adj_factor", pd.Series(float("nan"), index=ordered.index)), errors="coerce")
    if "adj_factor" in ordered:
        factors = factors.where(factors.gt(0))

    result = pd.Series(True, index=ordered.index, dtype=bool)
    for condition in signal.get("conditions", []):
        left_name = str(condition["left"])
        if left_name not in ordered:
            raise DataError(f"信号字段缺失: {left_name}")
        left_days = int(condition.get("left_days", 0) or 0)
        if left_name == "raw_close" and left_days > 0:
            raise DataError("raw_close 不支持跨日偏移，请使用 PIT 价基对齐的 close 字段")
        left = pd.to_numeric(ordered[left_name], errors="coerce").shift(left_days)
        valid = left.notna() & left.map(lambda value: pd.notna(value) and abs(float(value)) != float("inf"))
        left_offset = left_days + (left_name == "prev_close")
        if left_name in _PRICE_FIELDS and left_offset > 0:
            historical_factor = factors.shift(left_offset)
            valid &= historical_factor.notna() & factors.notna()
            left = left * historical_factor / factors

        right = condition["right"]
        right_name = right[6:] if isinstance(right, str) and right.startswith("field:") else None
        right_days = int(condition.get("right_days", 0) or 0)
        if right_name is not None:
            if right_name not in ordered:
                raise DataError(f"信号右侧字段缺失: {right_name}")
            if right_name == "raw_close" and right_days > 0:
                raise DataError("raw_close 不支持跨日偏移，请使用 PIT 价基对齐的 close 字段")
            right_series = pd.to_numeric(ordered[right_name], errors="coerce").shift(right_days)
            valid &= right_series.notna() & right_series.map(
                lambda value: pd.notna(value) and abs(float(value)) != float("inf")
            )
            right_offset = right_days + (right_name == "prev_close")
            if right_name in _PRICE_FIELDS and right_offset > 0:
                historical_factor = factors.shift(right_offset)
                valid &= historical_factor.notna() & factors.notna()
                right_series = right_series * historical_factor / factors
        else:
            try:
                numeric_right = float(right)
            except (TypeError, ValueError) as exc:
                raise DataError("信号右值不是数字或字段引用") from exc
            if not pd.notna(numeric_right) or abs(numeric_right) == float("inf"):
                raise DataError("信号右值必须是有限数字")
            right_series = numeric_right

        op = condition["op"]
        if op == ">":
            compared = left > right_series
        elif op == ">=":
            compared = left >= right_series
        elif op == "<":
            compared = left < right_series
        elif op == "<=":
            compared = left <= right_series
        elif op == "==":
            compared = left == right_series
        elif op == "!=":
            compared = left != right_series
        else:
            raise DataError("信号比较运算符无效")
        result &= valid & compared.fillna(False)
    return result


def _validate(
    *, signal_id: str, name: str, kind: str, enabled: bool, conditions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    import factor_trials

    if not _ID_RE.fullmatch(signal_id):
        raise ValueError("信号标识仅允许小写字母、数字和下划线，长度 1 到 40")
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 120:
        raise ValueError("信号名称必须为 1 到 120 个字符")
    if kind not in {"entry", "exit", "both"}:
        raise ValueError("信号类型必须为 entry / exit / both")
    if type(enabled) is not bool:
        raise ValueError("enabled 必须是布尔值")
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= MAX_CONDITIONS:
        raise ValueError(f"条件数量必须为 1 到 {MAX_CONDITIONS} 条")
    allowed = factor_trials.TRIAL_FIELDS
    clean: list[dict[str, Any]] = []
    for index, condition in enumerate(conditions, start=1):
        if not isinstance(condition, dict):
            raise ValueError(f"第 {index} 个条件格式无效")
        left = condition.get("left")
        op = condition.get("op")
        right = condition.get("right")
        if left not in allowed:
            raise ValueError(f"第 {index} 个条件字段不在 raw 行情白名单")
        if op not in _OPS:
            raise ValueError(f"第 {index} 个条件运算符无效")
        if isinstance(right, str) and right.startswith("field:"):
            right_value = right[6:]
            if right_value not in allowed:
                raise ValueError(f"第 {index} 个条件右侧字段不在 raw 行情白名单")
        else:
            try:
                numeric = float(right)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"第 {index} 个条件右值必须是数字或白名单字段") from exc
            if not math.isfinite(numeric):
                raise ValueError(f"第 {index} 个条件右值必须是有限数字")
            right = numeric
        row: dict[str, Any] = {"left": left, "op": op, "right": right}
        for key in ("left_days", "right_days"):
            days = condition.get(key, 0)
            if type(days) is not int or not 0 <= days <= MAX_DAYS:
                raise ValueError(f"第 {index} 个条件 {key} 必须在 0 到 {MAX_DAYS} 之间")
            row[key] = days
        right_field = row["right"][6:] if isinstance(row["right"], str) and row["right"].startswith("field:") else None
        if left == "raw_close" and row["left_days"] > 0:
            raise ValueError(f"第 {index} 个条件 raw_close 不允许跨日偏移，请改用 close")
        if right_field == "raw_close" and row["right_days"] > 0:
            raise ValueError(f"第 {index} 个条件 raw_close 不允许跨日偏移，请改用 close")
        clean.append(row)
    return clean


def save_signal(
    *,
    signal_id: str,
    name: str,
    kind: str,
    enabled: bool,
    conditions: list[dict[str, Any]],
    expected_revision: int | None,
) -> dict[str, Any]:
    clean_conditions = _validate(
        signal_id=signal_id, name=name, kind=kind, enabled=enabled, conditions=conditions
    )
    clean_name = name.strip()
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        items = payload["items"]
        existing = next((item for item in items if item.get("id") == signal_id), None)
        if existing is None and expected_revision is not None:
            raise SignalDefinitionConflict("信号不存在或已移出当前租户")
        if existing is not None and expected_revision != existing.get("revision"):
            raise SignalDefinitionConflict("信号已被其他操作更新，请重新读取")
        if existing is None:
            record = {
                "id": signal_id,
                "revision": 1,
                "created_at": now_shanghai().isoformat(),
            }
            items.append(record)
        else:
            record = existing
            record["revision"] += 1
        record.update(
            {
                "name": clean_name,
                "kind": kind,
                "enabled": enabled,
                "conditions": clean_conditions,
                "updated_at": now_shanghai().isoformat(),
            }
        )
        _save(path, payload)
        return record


def delete_signal(signal_id: str, expected_revision: int) -> bool:
    if not _ID_RE.fullmatch(signal_id):
        raise ValueError("信号标识格式无效")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        record = next((item for item in payload["items"] if item.get("id") == signal_id), None)
        if record is None:
            return False
        if type(expected_revision) is not int or expected_revision != record.get("revision"):
            raise SignalDefinitionConflict("信号已被其他操作更新，请重新读取")
        payload["items"].remove(record)
        _save(path, payload)
        return True
