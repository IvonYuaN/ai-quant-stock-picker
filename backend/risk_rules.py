"""User position alert defaults, separate from frozen strategy thresholds.

Defaults come from the accepted T+1 PRD (2026-10-19, sections 6 and 7.2).
These rules only observe user-entered positions; they never alter selection/backtests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import uuid4

from aqsp.core.time import get_next_trading_day, now_shanghai


@dataclass(frozen=True)
class PositionRuleDefaults:
    hard_stop_pct: float = 0.05
    trend_window: int = 5
    trailing_pct: float = 0.05
    ignored_before_daily: int = 2
    history_calendar_days: int = 120
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9


def ensure_rules(data: dict) -> list[dict]:
    """Attach missing defaults in the same portfolio transaction; preserve edits."""
    defaults = PositionRuleDefaults()
    rules = data.setdefault("risk_rules", [])
    stamp = now_shanghai().isoformat(timespec="seconds")
    for holding in data.get("holdings", []):
        symbol = holding["code"]
        position_id = holding.setdefault("position_id", uuid4().hex)
        for level, param in (
            ("L1", defaults.hard_stop_pct),
            ("L2", defaults.trend_window),
            ("L4", 0),
            ("L5", defaults.trailing_pct),
        ):
            rule = next(
                (r for r in rules if r["symbol"] == symbol and r["level"] == level),
                None,
            )
            if rule is None:
                opened_at = holding.get("opened_at")
                effective = None
                if opened_at and level == "L1":
                    from datetime import date

                    effective = get_next_trading_day(
                        date.fromisoformat(opened_at)
                    ).isoformat()
                rule = {
                    "id": f"{symbol}-{level}",
                    "position_id": position_id,
                    "symbol": symbol,
                    "level": level,
                    "enabled": True,
                    "param_value": param,
                    "trigger_price": None,
                    "freq_mode": "daily" if level == "L4" else "once",
                    "origin": "AUTO_DEFAULT",
                    "effective_from": effective,
                    "created_at": stamp,
                    "updated_at": stamp,
                    "ignore_streak": 0,
                    "frequency_note": "",
                    "revision": 0,
                }
                rules.append(rule)
            rule["position_id"] = position_id
            if level == "L1":
                cost = float(holding.get("cost") or 0)
                trigger = (
                    round(cost * (1 - float(rule["param_value"])), 4)
                    if cost > 0
                    else None
                )
                if rule.get("trigger_price") is not None and rule["trigger_price"] != trigger:
                    rule["revision"] = int(rule.get("revision", 0)) + 1
                rule["trigger_price"] = trigger
            if level == "L5":
                peak = max(
                    float(rule.get("high_water_price") or 0),
                    float(holding.get("cost") or 0),
                )
                rule["high_water_price"] = peak
                rule["trigger_price"] = (
                    max(
                        float(rule.get("trigger_price") or 0),
                        peak * (1 - float(rule["param_value"])),
                    )
                    or None
                )
    return rules


def patch_rule(rule: dict, patch: dict) -> None:
    """Validate a user rule patch before mutating it."""
    if not patch or set(patch) - {"enabled", "param_value", "freq_mode"}:
        raise ValueError("仅支持 enabled、param_value、freq_mode")
    if "enabled" in patch and type(patch["enabled"]) is not bool:
        raise ValueError("enabled 必须是布尔值")
    if "freq_mode" in patch and patch["freq_mode"] not in {"once", "daily", "always"}:
        raise ValueError("freq_mode 必须为 once、daily 或 always")
    if "param_value" in patch:
        value = patch["param_value"]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("阈值必须是有限数字")
        if rule["level"] in {"L1", "L5"} and not 0 < value < 1:
            raise ValueError("止损比例必须大于 0 且小于 1")
        if rule["level"] == "L2" and value not in {5, 10, 20}:
            raise ValueError("趋势均线仅支持 MA5、MA10、MA20")
        if rule["level"] == "L4":
            raise ValueError("L4 使用固定 MACD(12,26,9)，仅支持开关和提醒频率")
    if any(key in patch and rule.get(key) != patch[key] for key in ("enabled", "param_value")):
        rule["revision"] = int(rule.get("revision", 0)) + 1
    rule.update(patch)
    rule["origin"] = "MANUAL"
    rule["updated_at"] = now_shanghai().isoformat(timespec="seconds")
    if "freq_mode" in patch:
        rule["ignore_streak"] = 0
        rule["frequency_note"] = ""
