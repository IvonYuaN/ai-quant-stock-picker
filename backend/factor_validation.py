"""Tenant-scoped diagnostic Newey-West and BH-FDR reports for saved factor trials."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from scipy.stats import t as student_t

from aqsp.core.time import now_shanghai


METHOD_ID = "factor-family-nw-bh-v1"
MIN_COMMON_DATES = 20
MIN_DATES_PER_HAC_LAG = 3


class FactorTrialNotFound(ValueError):
    """The requested immutable trial does not exist in the current tenant."""


def validate_factor_trial_family(
    definitions: list[dict[str, Any]],
    *,
    target_trial_id: str,
) -> dict[str, Any]:
    """Compute a diagnostic factor-family report from saved trial records.

    Every trial sharing the request signature enters the family. Invalid members
    block the report instead of being silently discarded from multiplicity control.
    """
    records: list[dict[str, Any]] = []
    target: dict[str, Any] | None = None
    for definition in definitions:
        for version in definition.get("versions", []):
            for trial in version.get("trials", []):
                record = {
                    **trial,
                    "factor_id": definition.get("id"),
                    "factor_version": version.get("version"),
                    "factor_name": version.get("name"),
                }
                records.append(record)
                if trial.get("trial_id") == target_trial_id:
                    target = record
    if target is None:
        raise FactorTrialNotFound("因子试算不存在或已移出当前租户")

    signature = _request_signature(target)
    blockers: list[str] = []
    if signature is None:
        blockers.append("目标试算缺少完整数据来源或请求范围身份")
        family = [target]
    else:
        family = [row for row in records if _request_signature(row) == signature]
    target_sample_hash = target.get("data_sample_sha256")
    if not _is_sha256(target_sample_hash):
        blockers.append("目标试算缺少实际 raw OHLCV 样本指纹")

    for row in family:
        trial_id = str(row.get("trial_id") or "unknown")
        if row.get("evidence_status") != "preview_only":
            blockers.append(f"试算 {trial_id} 不是完整诊断预览")
        if row.get("blockers"):
            blockers.append(f"试算 {trial_id} 含阻断项")
        if row.get("missing_data_symbols"):
            blockers.append(f"试算 {trial_id} 未覆盖全部请求标的")
        if row.get("used_symbols") != row.get("requested_symbols"):
            blockers.append(f"试算 {trial_id} 实际标的数与请求数不同")
        digest = row.get("data_sample_sha256")
        if not _is_sha256(digest):
            blockers.append(f"试算 {trial_id} 缺少实际 raw OHLCV 样本指纹")
        elif target_sample_hash and digest != target_sample_hash:
            blockers.append(f"试算 {trial_id} 使用了不同的 raw OHLCV 样本")
        expression = row.get("expression")
        expression_hash = row.get("expression_sha256")
        if not isinstance(expression, str) or not isinstance(expression_hash, str):
            blockers.append(f"试算 {trial_id} 缺少公式身份")
        elif hashlib.sha256(expression.encode("utf-8")).hexdigest() != expression_hash:
            blockers.append(f"试算 {trial_id} 公式哈希不匹配")

    hypotheses: dict[str, dict[str, Any]] = {}
    for row in family:
        expression_hash = str(row.get("expression_sha256") or "")
        if not expression_hash:
            continue
        daily = _daily_ic_map(row, blockers)
        previous = hypotheses.get(expression_hash)
        if previous is None:
            hypotheses[expression_hash] = {
                "representative": row,
                "daily_ic": daily,
                "trial_ids": [str(row.get("trial_id") or "")],
            }
        else:
            previous["trial_ids"].append(str(row.get("trial_id") or ""))
            if daily != previous["daily_ic"]:
                blockers.append(
                    f"同一公式哈希 {expression_hash[:12]} 的重复试算结果不一致"
                )

    family_size = len(hypotheses)
    if family_size < 2:
        blockers.append("同一数据请求家族至少需要两个不同公式")
    date_sets = [set(hypothesis["daily_ic"]) for hypothesis in hypotheses.values()]
    common_dates = set.intersection(*date_sets) if date_sets else set()
    ordered_dates = sorted(common_dates)
    holding_days = int(target.get("holding_days") or 0)
    nw_lags = min(max(holding_days - 1, 0), max(len(ordered_dates) - 1, 0))
    minimum_dates = max(
        MIN_COMMON_DATES,
        MIN_DATES_PER_HAC_LAG * (nw_lags + 1),
    )
    if len(ordered_dates) < minimum_dates:
        blockers.append(
            f"共同有效 IC 日期 {len(ordered_dates)} 少于 Newey-West 最低样本 {minimum_dates}"
        )

    items: list[dict[str, Any]] = []
    if not blockers:
        for expression_hash, hypothesis in hypotheses.items():
            values = [hypothesis["daily_ic"][day] for day in ordered_dates]
            result = _newey_west(values, nw_lags)
            if result is None:
                blockers.append(f"公式 {expression_hash[:12]} 的 HAC 方差无效")
                break
            row = hypothesis["representative"]
            items.append(
                {
                    "factor_id": row["factor_id"],
                    "factor_version": row["factor_version"],
                    "factor_name": row["factor_name"],
                    "trial_id": row["trial_id"],
                    "trial_ids": sorted(hypothesis["trial_ids"]),
                    "expression": row["expression"],
                    "expression_sha256": expression_hash,
                    "n_dates": len(ordered_dates),
                    "ic_mean": result["ic_mean"],
                    "newey_west_lags": nw_lags,
                    "newey_west_t": result["t_statistic"],
                    "p_value": result["p_value"],
                }
            )
    if not blockers:
        adjusted = _benjamini_hochberg([row["p_value"] for row in items])
        for row, q_value in zip(items, adjusted, strict=True):
            row["q_value"] = q_value

    family_trial_ids = sorted(str(row.get("trial_id") or "") for row in family)
    family_identity = {
        "method": METHOD_ID,
        "request_signature": signature,
        "data_sample_sha256": target_sample_hash,
        "trial_ids": family_trial_ids,
        "common_dates": ordered_dates,
    }
    family_id = hashlib.sha256(_canonical_json(family_identity).encode("utf-8")).hexdigest()
    return {
        "family_id": family_id,
        "method": METHOD_ID,
        "target_trial_id": target_trial_id,
        "generated_at": now_shanghai().isoformat(),
        "family_size": family_size,
        "family_trial_ids": family_trial_ids,
        "data_sample_sha256": target_sample_hash,
        "symbol_set_sha256": target.get("symbol_set_sha256"),
        "signal_start": target.get("signal_start"),
        "signal_end": target.get("signal_end"),
        "holding_days": target.get("holding_days"),
        "quantile": target.get("quantile"),
        "common_dates": len(ordered_dates),
        "newey_west_lags": nw_lags,
        "status": "diagnostic_only" if not blockers else "blocked",
        "blockers": sorted(set(blockers)),
        "items": items if not blockers else [],
        "human_review_required": True,
        "promotion_authorized": False,
        "oos_verified": False,
    }


def _request_signature(trial: dict[str, Any]) -> tuple[Any, ...] | None:
    fields = (
        "source", "price_mode", "symbol_set_sha256", "signal_start", "signal_end",
        "holding_days", "quantile", "requested_symbols", "warmup_start",
    )
    values = tuple(trial.get(field) for field in fields)
    if any(value is None or value == "" for value in values):
        return None
    return values


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _daily_ic_map(trial: dict[str, Any], blockers: list[str]) -> dict[str, float]:
    trial_id = str(trial.get("trial_id") or "unknown")
    raw = trial.get("daily_ic")
    if not isinstance(raw, list) or not raw:
        blockers.append(f"试算 {trial_id} 缺少逐日 IC 序列")
        return {}
    values: dict[str, float] = {}
    for row in raw:
        if not isinstance(row, dict):
            blockers.append(f"试算 {trial_id} 的逐日 IC 格式无效")
            return {}
        day = row.get("date")
        value = row.get("ic")
        if (
            not isinstance(day, str)
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            blockers.append(f"试算 {trial_id} 含无效逐日 IC")
            return {}
        if day in values:
            blockers.append(f"试算 {trial_id} 存在重复 IC 日期")
            return {}
        values[day] = float(value)
    return values


def _newey_west(values: list[float], lags: int) -> dict[str, float] | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    centered = [value - mean for value in values]
    long_run_variance = sum(value * value for value in centered) / n
    for lag in range(1, min(lags, n - 1) + 1):
        autocovariance = sum(centered[index] * centered[index - lag] for index in range(lag, n)) / n
        bartlett_weight = 1.0 - lag / (lags + 1.0)
        long_run_variance += 2.0 * bartlett_weight * autocovariance
    if not math.isfinite(long_run_variance) or long_run_variance <= 0:
        return None
    t_statistic = mean / math.sqrt(long_run_variance / n)
    p_value = float(student_t.sf(abs(t_statistic), df=n - 1) * 2.0)
    if not math.isfinite(t_statistic) or not math.isfinite(p_value):
        return None
    return {"ic_mean": mean, "t_statistic": t_statistic, "p_value": p_value}


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    total = len(p_values)
    if total == 0:
        return []
    order = sorted(range(total), key=p_values.__getitem__)
    adjusted = [1.0] * total
    running_min = 1.0
    for reverse_rank in range(total - 1, -1, -1):
        index = order[reverse_rank]
        rank = reverse_rank + 1
        running_min = min(running_min, p_values[index] * total / rank)
        adjusted[index] = min(max(running_min, 0.0), 1.0)
    return adjusted


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
