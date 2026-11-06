"""Tenant-scoped, declarative daily strategies backed by the shared safe DSL."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

import portfolio as pf
import tenant
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.research.strategy_expression import (
    MAX_EXPRESSION_CHARS,
    compile_strategy_expression,
    compile_strategy_score_expression,
)
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


_LOCK = threading.RLock()
_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
MAX_STRATEGIES = 50


class StrategyDefinitionConflict(ValueError):
    """A strategy revision changed after the editor read it."""


def _file() -> Path:
    tenant_id = tenant.current_tenant.get()
    root = Path(pf.CACHE_DIR)
    if tenant_id != "local":
        root = root / "users" / tenant_id
    return root / "strategy_definitions.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("用户策略库不可读取，已停止写入以保护原记录") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("items"), list)
        or any(not isinstance(item, dict) for item in payload["items"])
    ):
        raise ValueError("用户策略库格式无效，已停止写入以保护原记录")
    return payload


def _save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def strategy_expression_hash(
    expression: str,
    exit_expression: str | None = None,
    score_expression: str | None = None,
) -> str:
    snapshot = {
        "entry_expression": expression.strip(),
        "exit_expression": (exit_expression or "").strip(),
    }
    if score_expression and score_expression.strip():
        snapshot["score_expression"] = score_expression.strip()
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def options() -> dict[str, Any]:
    from aqsp.indicators import DAILY_EXPRESSION_FIELDS, DAILY_PIT_PRICE_FIELDS

    return {
        "fields": sorted(DAILY_EXPRESSION_FIELDS),
        "pit_price_fields": sorted(DAILY_PIT_PRICE_FIELDS),
        "max_expression_chars": MAX_EXPRESSION_CHARS,
        "max_strategies": MAX_STRATEGIES,
        "runtime": "safe_daily_dsl",
    }


def list_strategies() -> list[dict[str, Any]]:
    with _LOCK:
        items = _load(_file())["items"]
    return sorted(items, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def get_strategy(strategy_id: str) -> dict[str, Any] | None:
    """Return the current tenant's strategy, if one exists."""
    return next((item for item in list_strategies() if item.get("id") == strategy_id), None)


def save_strategy(
    *,
    strategy_id: str,
    name: str,
    description: str,
    expression: str,
    exit_expression: str | None = None,
    score_expression: str | None = None,
    enabled: bool,
    expected_revision: int | None,
) -> dict[str, Any]:
    if not _ID_RE.fullmatch(strategy_id):
        raise ValueError("策略标识仅允许小写字母、数字和下划线，长度 1 到 40")
    clean_name = name.strip()
    clean_description = description.strip()
    clean_expression = expression.strip()
    clean_exit_expression = (exit_expression or "").strip()
    clean_score_expression = (score_expression or "").strip()
    if not clean_name or len(clean_name) > 120:
        raise ValueError("策略名称必须为 1 到 120 个字符")
    if len(clean_description) > 1000:
        raise ValueError("策略说明最多 1000 个字符")
    if type(enabled) is not bool:
        raise ValueError("enabled 必须是布尔值")
    try:
        compiled = compile_strategy_expression(clean_expression)
        exit_compiled = (
            compile_strategy_expression(clean_exit_expression)
            if clean_exit_expression
            else None
        )
        score_compiled = (
            compile_strategy_score_expression(clean_score_expression)
            if clean_score_expression
            else None
        )
    except DataError as exc:
        raise ValueError(str(exc)) from exc
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        items = payload["items"]
        existing = next((item for item in items if item.get("id") == strategy_id), None)
        if existing is None and expected_revision is not None:
            raise StrategyDefinitionConflict("策略不存在或已移出当前租户")
        if existing is not None and expected_revision != existing.get("revision"):
            raise StrategyDefinitionConflict("策略已被其他操作更新，请重新读取")
        if existing is None:
            if len(items) >= MAX_STRATEGIES:
                raise StrategyDefinitionConflict(
                    f"当前租户最多保存 {MAX_STRATEGIES} 个策略；请先删除不再需要的策略"
                )
            record: dict[str, Any] = {
                "id": strategy_id,
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
                "description": clean_description,
                "enabled": enabled,
                "expression": clean_expression,
                "exit_expression": clean_exit_expression,
                "score_expression": clean_score_expression,
                "expression_sha256": strategy_expression_hash(
                    clean_expression, clean_exit_expression, clean_score_expression
                ),
                "expression_fields": list(compiled.fields),
                "max_lookback": compiled.max_lookback,
                "requires_pit_price_basis": compiled.requires_pit_price_basis,
                "exit_expression_fields": list(exit_compiled.fields) if exit_compiled else [],
                "exit_max_lookback": exit_compiled.max_lookback if exit_compiled else 0,
                "exit_requires_pit_price_basis": exit_compiled.requires_pit_price_basis if exit_compiled else False,
                "score_expression_fields": list(score_compiled.fields) if score_compiled else [],
                "score_max_lookback": score_compiled.max_lookback if score_compiled else 0,
                "score_requires_pit_price_basis": score_compiled.requires_pit_price_basis if score_compiled else False,
                "updated_at": now_shanghai().isoformat(),
            }
        )
        _save(path, payload)
        return dict(record)


def delete_strategy(strategy_id: str, expected_revision: int) -> bool:
    if not _ID_RE.fullmatch(strategy_id):
        raise ValueError("策略标识格式无效")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        existing = next((item for item in payload["items"] if item.get("id") == strategy_id), None)
        if existing is None:
            return False
        if existing.get("revision") != expected_revision:
            raise StrategyDefinitionConflict("策略已被其他操作更新，请重新读取")
        payload["items"].remove(existing)
        _save(path, payload)
        return True
