"""Commit a risk snapshot only while its actual position/rule inputs still match."""

from __future__ import annotations

from dataclasses import dataclass

import alert_events as ae
import portfolio as pf
from aqsp.utils.jsonl_io import advisory_lock


@dataclass(frozen=True)
class PositionCheckCommit:
    reminders: list[dict]
    new_events: list[dict]
    eligible_rules: set[str]
    changed_symbols: set[str]


def commit_position_check(
    reminders: list[dict], holdings: list[dict], rules: list[dict],
    checked_rules: set[str], checked_symbols: set[str],
) -> PositionCheckCommit:
    """Serialize with actual sales, and reject stale position/rule snapshots.

    The lock order is alerts -> portfolio, matching sale/disposition paths. No
    quote/history fetch or outbound call occurs under these commit locks.
    """
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        current = pf._load()
        positions = {row["code"]: row for row in current.get("holdings", [])}
        live_rules = {row["id"]: row for row in current.get("risk_rules", [])}
        stable_symbols = {
            row["code"] for row in holdings
            if row["code"] in positions
            and pf.risk_position_matches(row, positions[row["code"]])
        }
        stable_rules = {
            rule["id"] for rule in rules
            if rule["symbol"] in stable_symbols and rule["id"] in live_rules
            and all(rule.get(key) == live_rules[rule["id"]].get(key) for key in (
                "position_id", "revision", "enabled", "param_value", "freq_mode",
                "trigger_price", "high_water_price",
            ))
        }
        eligible = checked_rules & stable_rules
        accepted = [row for row in reminders if row.get("rule_id") in eligible]
        events = ae._upsert_reminders_locked(
            accepted, checked_rules=eligible,
            checked_symbols=checked_symbols & stable_symbols,
            active_symbols=set(positions),
        )
        changed = {row["code"] for row in holdings} - stable_symbols
        changed |= {rule["symbol"] for rule in rules if rule["id"] in checked_rules and rule["id"] not in stable_rules}
        return PositionCheckCommit(accepted, events, eligible, changed)
