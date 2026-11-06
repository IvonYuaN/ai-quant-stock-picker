"""Tenant-scoped immutable versions of user-authored numeric factor formulas."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any
from uuid import uuid4

import portfolio as pf
import tenant
from aqsp.core.time import now_shanghai
from aqsp.research.factor_expression import compile_factor_expression
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text


_LOCK = threading.RLock()


class FactorDefinitionConflict(ValueError):
    """A definition revision changed after the editor read it."""


def _file() -> str:
    tenant_id = tenant.current_tenant.get()
    root = pf.CACHE_DIR if tenant_id == "local" else os.path.join(pf.CACHE_DIR, "users", tenant_id)
    return os.path.join(root, "factor_definitions.json")


def _load(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"schema_version": 1, "definitions": [], "validation_runs": []}
    try:
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("因子定义库不可读取，已停止写入以保护原记录") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("definitions"), list)
        or any(not isinstance(item, dict) for item in payload["definitions"])
    ):
        raise ValueError("因子定义库格式无效，已停止写入以保护原记录")
    validation_runs = payload.get("validation_runs", [])
    if not isinstance(validation_runs, list) or any(not isinstance(item, dict) for item in validation_runs):
        raise ValueError("因子族验证记录格式无效，已停止写入以保护原记录")
    payload["validation_runs"] = validation_runs
    return payload


def _save(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def list_definitions() -> list[dict[str, Any]]:
    path = _file()
    with _LOCK:
        items = _load(path)["definitions"]
    return sorted(items, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def save_draft(
    *,
    factor_id: str | None,
    name: str,
    hypothesis: str,
    expression: str,
    expected_revision: int | None,
) -> dict[str, Any]:
    clean_name = name.strip()
    clean_hypothesis = hypothesis.strip()
    compiled = compile_factor_expression(expression)
    if not clean_name or len(clean_name) > 120:
        raise ValueError("因子名称必须为 1 到 120 个字符")
    if not clean_hypothesis or len(clean_hypothesis) > 500:
        raise ValueError("研究假设必须为 1 到 500 个字符")

    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        definitions = payload["definitions"]
        existing = next((item for item in definitions if item.get("id") == factor_id), None) if factor_id else None
        if factor_id and existing is None:
            raise FactorDefinitionConflict("因子定义不存在或已移出当前租户")
        if existing is not None:
            if existing.get("status") != "draft":
                raise ValueError("只有草稿因子可以修改；其他生命周期状态尚未开放写入")
            revision = int(existing.get("revision", 0))
            if type(expected_revision) is not int or expected_revision != revision:
                raise FactorDefinitionConflict("因子草稿已被更新，请重新读取")
            versions = existing.get("versions")
            if not isinstance(versions, list) or not versions:
                raise ValueError("因子版本记录无效，已停止写入")
            latest = versions[-1]
            if (
                latest.get("name") == clean_name
                and latest.get("hypothesis") == clean_hypothesis
                and latest.get("expression") == compiled.expression
            ):
                return existing
            version = int(latest["version"]) + 1
            factor_record = existing
        else:
            revision = 0
            version = 1
            factor_record = {
                "id": str(uuid4()),
                "status": "draft",
                "revision": 0,
                "versions": [],
                "created_at": now_shanghai().isoformat(),
            }
            definitions.append(factor_record)

        now = now_shanghai().isoformat()
        canonical = json.dumps(
            {
                "name": clean_name,
                "hypothesis": clean_hypothesis,
                "expression": compiled.expression,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        factor_record["versions"].append(
            {
                "version": version,
                "name": clean_name,
                "hypothesis": clean_hypothesis,
                "expression": compiled.expression,
                "expression_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "fields": list(compiled.fields),
                "max_lookback": compiled.max_lookback,
                "trials": [],
                "created_at": now,
            }
        )
        factor_record["revision"] = revision + 1
        factor_record["updated_at"] = now
        _save(path, payload)
        return factor_record


def get_version(factor_id: str, version: int) -> dict[str, Any]:
    for item in list_definitions():
        if item.get("id") != factor_id:
            continue
        versions = item.get("versions", [])
        selected = next((row for row in versions if row.get("version") == version), None)
        if selected is None:
            break
        return {"definition": item, "version": selected}
    raise FactorDefinitionConflict("因子定义版本不存在或已移出当前租户")


def append_trial(factor_id: str, version: int, trial: dict[str, Any]) -> dict[str, Any]:
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        item = next((row for row in payload["definitions"] if row.get("id") == factor_id), None)
        if item is None:
            raise FactorDefinitionConflict("因子定义不存在或已移出当前租户")
        selected = next((row for row in item.get("versions", []) if row.get("version") == version), None)
        if selected is None:
            raise FactorDefinitionConflict("因子定义版本不存在或已被更新")
        trials = selected.get("trials")
        if not isinstance(trials, list):
            raise ValueError("因子试算记录格式无效，已停止写入")
        repeated = next((row for row in trials if row.get("trial_id") == trial.get("trial_id")), None)
        if repeated is not None:
            return repeated
        trials.append(trial)
        item["revision"] = int(item.get("revision", 0)) + 1
        item["updated_at"] = now_shanghai().isoformat()
        _save(path, payload)
        return trial


def list_validation_runs(*, limit: int = 50) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit 必须在 1 到 200 之间")
    with _LOCK:
        rows = _load(_file())["validation_runs"]
    return sorted(rows, key=lambda row: str(row.get("generated_at", "")), reverse=True)[:limit]


def append_validation_run(run: dict[str, Any]) -> dict[str, Any]:
    """Persist one immutable factor-family report in the current tenant store."""
    family_id = run.get("family_id")
    if not isinstance(family_id, str) or len(family_id) != 64:
        raise ValueError("factor validation family_id 无效")
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        runs = payload["validation_runs"]
        existing = next((row for row in runs if row.get("family_id") == family_id), None)
        if existing is not None:
            return existing
        stored = json.loads(json.dumps(run, ensure_ascii=False, allow_nan=False))
        runs.append(stored)
        _save(path, payload)
        return stored


