from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

REQUIRED_LEDGER_FIELDS = {
    "id",
    "signal_date",
    "symbol",
    "score",
    "strategies",
    "status",
    "thresholds_version",
    "regime_at_signal",
    "signal_day_group",
}

LEDGER_PATH = Path("data/predictions.jsonl")


def _iter_records():
    if not LEDGER_PATH.exists() or LEDGER_PATH.stat().st_size == 0:
        pytest.skip("ledger empty or missing")
    with LEDGER_PATH.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            yield i, json.loads(line)


def _is_run_event(rec: dict) -> bool:
    return str(rec.get("symbol") or "") == "__RUN__"


def test_ledger_path_exists():
    if not LEDGER_PATH.exists():
        pytest.skip("ledger not yet created")
    assert LEDGER_PATH.is_file()


def test_ledger_records_have_required_fields():
    for i, rec in _iter_records():
        if _is_run_event(rec):
            assert rec.get("status") == "blocked_by_circuit_breaker"
            assert rec.get("reason")
            continue
        missing = REQUIRED_LEDGER_FIELDS - set(rec.keys())
        assert not missing, f"line {i + 1} missing: {missing}"


def test_ledger_dates_are_valid():
    for i, rec in _iter_records():
        d = rec.get("signal_date")
        if d:
            date.fromisoformat(d)


def test_ledger_symbols_are_6_digit():
    for i, rec in _iter_records():
        if _is_run_event(rec):
            continue
        sym = str(rec.get("symbol", ""))
        assert sym.isdigit() and len(sym) == 6, f"line {i + 1} bad symbol: {sym!r}"


def test_ledger_created_at_has_timezone():
    for i, rec in _iter_records():
        ts = rec.get("created_at", "")
        if ts:
            assert "+" in ts or "Z" in ts, f"line {i + 1} missing timezone: {ts!r}"
