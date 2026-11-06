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


class FactorLifecycleError(ValueError):
    """An illegal lifecycle transition or insufficient promotion evidence."""


FACTOR_STATUSES = ("draft", "watch", "active", "retired")
_STATUS_LABEL = {
    "draft": "草稿",
    "watch": "观察",
    "active": "启用",
    "retired": "退役",
}
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"watch"}),
    "watch": frozenset({"active", "draft", "retired"}),
    "active": frozenset({"watch", "retired"}),
    "retired": frozenset(),
}
_PROMOTION_TARGET = "active"
_VALIDATION_STATUSES = ("diagnostic_only", "blocked")
_MIN_REVIEW_NOTE_CHARS = 8


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
    for item in payload["definitions"]:
        if item.get("status", "draft") not in FACTOR_STATUSES:
            raise ValueError("因子定义含未知生命周期状态，已停止写入以保护原记录")
        history = item.get("lifecycle_history", [])
        if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
            raise ValueError("因子生命周期审计记录格式无效，已停止写入以保护原记录")
        last_validation = item.get("last_validation")
        if last_validation is not None and not isinstance(last_validation, dict):
            raise ValueError("因子检验观察记录格式无效，已停止写入以保护原记录")
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
                raise ValueError("只有草稿因子可以修改公式；观察、启用或退役状态请先人工降级回草稿")
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


def _validate_transition_evidence(
    evidence: dict[str, Any] | None,
    target_status: str,
) -> dict[str, Any]:
    """Gate every transition behind an explicit, attributable human action."""
    if not isinstance(evidence, dict):
        raise FactorLifecycleError("状态迁移必须由人工显式确认并提供可核对的操作记录")
    if evidence.get("human_confirmed") is not True:
        raise FactorLifecycleError("状态迁移必须由人工显式确认；禁止依据检验结果自动晋级或变更状态")
    actor = evidence.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        raise FactorLifecycleError("状态迁移缺少操作人，无法留痕")
    note = evidence.get("manual_review_note", "")
    if not isinstance(note, str):
        raise FactorLifecycleError("人工确认记录格式无效")
    note = note.strip()
    family_id = evidence.get("validation_family_id")
    if family_id is not None and not _is_sha256(family_id):
        raise FactorLifecycleError("检验报告 family_id 无效，无法作为可核对证据")
    if target_status == _PROMOTION_TARGET and len(note) < _MIN_REVIEW_NOTE_CHARS and family_id is None:
        raise FactorLifecycleError(
            f"晋级为启用必须提供可核对证据：有效的检验报告 family_id，"
            f"或不少于 {_MIN_REVIEW_NOTE_CHARS} 个字的具体人工确认记录"
        )
    return {
        "actor": actor.strip(),
        "manual_review_note": note,
        "validation_family_id": family_id,
        "evidence_kind": (
            "validation_report" if family_id else "manual_review" if note else "human_action_only"
        ),
    }


def _require_promotion_evidence(payload: dict[str, Any], evidence: dict[str, Any]) -> None:
    """watch -> active may only cite a saved, non-blocked diagnostic report."""
    family_id = evidence["validation_family_id"]
    if family_id is None:
        return  # 具体人工确认记录已在 _validate_transition_evidence 校验
    run = next(
        (row for row in payload["validation_runs"] if row.get("family_id") == family_id),
        None,
    )
    if run is None:
        raise FactorLifecycleError("晋级引用的检验报告不存在或已移出当前租户")
    if run.get("status") != "diagnostic_only" or run.get("blockers"):
        raise FactorLifecycleError("晋级引用的检验报告被阻断，不能作为启用证据")
    if run.get("promotion_authorized") is True or run.get("oos_verified") is True:
        raise FactorLifecycleError("检验报告异常地声明了晋级授权，已拒绝以保证人工闸门")


def transition_status(
    *,
    factor_id: str,
    expected_revision: int,
    target_status: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically move a factor through draft -> watch -> active -> retired.

    Every transition is an explicit human action recorded in ``lifecycle_history``.
    Promotion to ``active`` requires a checkable evidence reference (a saved
    non-blocked diagnostic report or a concrete manual review note) plus an
    explicit human confirmation. No validation result can promote a factor by
    itself, and this function never writes back to scores, weights or thresholds.
    """
    if target_status not in FACTOR_STATUSES:
        raise FactorLifecycleError(f"目标生命周期状态无效：{target_status}")
    checked_evidence = _validate_transition_evidence(evidence, target_status)

    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        item = next((row for row in payload["definitions"] if row.get("id") == factor_id), None)
        if item is None:
            raise FactorDefinitionConflict("因子定义不存在或已移出当前租户")
        revision = int(item.get("revision", 0))
        if type(expected_revision) is not int or expected_revision != revision:
            raise FactorDefinitionConflict("因子生命周期已被更新，请重新读取")
        current = item.get("status", "draft")
        if current not in FACTOR_STATUSES:
            raise FactorLifecycleError("因子当前生命周期状态异常，已停止迁移")
        if target_status not in _ALLOWED_TRANSITIONS[current]:
            raise FactorLifecycleError(
                f"不允许从「{_STATUS_LABEL[current]}」迁移到「{_STATUS_LABEL[target_status]}」；"
                "请按草稿→观察→启用→退役的人工闸门顺序操作"
            )
        if target_status == _PROMOTION_TARGET:
            _require_promotion_evidence(payload, checked_evidence)

        now = now_shanghai().isoformat()
        history = item.setdefault("lifecycle_history", [])
        history.append(
            {
                "from": current,
                "to": target_status,
                "actor": checked_evidence["actor"],
                "note": checked_evidence["manual_review_note"],
                "evidence_kind": checked_evidence["evidence_kind"],
                "evidence_validation_family_id": checked_evidence["validation_family_id"],
                "changed_at": now,
                "revision": revision + 1,
            }
        )
        item["status"] = target_status
        item["revision"] = revision + 1
        item["updated_at"] = now
        _save(path, payload)
        return item


def record_validation_observation(
    *,
    factor_id: str,
    expected_revision: int,
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Record the latest diagnostic conclusion for a watch/active factor.

    Drift observation only: it never changes lifecycle status, scores, weights
    or thresholds, and never authorizes promotion.
    """
    if not isinstance(observation, dict):
        raise ValueError("检验观察记录格式无效，已停止写入")
    family_id = observation.get("family_id")
    if not _is_sha256(family_id):
        raise ValueError("检验观察记录缺少有效 family_id")
    status = observation.get("status")
    if status not in _VALIDATION_STATUSES:
        raise ValueError("检验观察记录的结论状态无效")
    generated_at = observation.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError("检验观察记录缺少检验时间戳")
    blockers = observation.get("blockers", [])
    if not isinstance(blockers, list) or any(not isinstance(item, str) for item in blockers):
        raise ValueError("检验观察记录的阻断项格式无效")

    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load(path)
        item = next((row for row in payload["definitions"] if row.get("id") == factor_id), None)
        if item is None:
            raise FactorDefinitionConflict("因子定义不存在或已移出当前租户")
        revision = int(item.get("revision", 0))
        if type(expected_revision) is not int or expected_revision != revision:
            raise FactorDefinitionConflict("因子生命周期已被更新，请重新读取")
        if item.get("status", "draft") not in ("watch", "active"):
            raise FactorLifecycleError("只有观察或启用状态的因子可以记录检验结论；草稿与退役状态不适用")
        now = now_shanghai().isoformat()
        item["last_validation"] = {
            "family_id": family_id,
            "status": status,
            "generated_at": generated_at,
            "blockers": list(blockers),
            "family_size": int(observation.get("family_size", 0) or 0),
            "common_dates": int(observation.get("common_dates", 0) or 0),
            "observed_at": now,
            "human_review_required": True,
            "promotion_authorized": False,
            "oos_verified": False,
        }
        item["revision"] = revision + 1
        item["updated_at"] = now
        _save(path, payload)
        return item


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


