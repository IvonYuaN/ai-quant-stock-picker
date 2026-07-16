from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from aqsp.core.time import SHANGHAI_TZ
from aqsp.market_context import REALTIME_CROSS_MARKET_INSTRUMENTS
from scripts import collect_realtime_cross_market as collector

NOW = datetime(2026, 7, 16, 10, 30, tzinfo=SHANGHAI_TZ)


def _observation(instrument: str) -> dict[str, object]:
    return {
        "value": 100.0,
        "change_pct": 0.5,
        "source": "test",
        "source_url": f"https://example.test/{instrument}",
        "observed_at": NOW.isoformat(timespec="seconds"),
        "fetched_at": NOW.isoformat(timespec="seconds"),
        "timestamp_source": "vendor",
    }


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_collect_realtime_cross_market_writes_fresh_when_all_instruments_valid(
    tmp_path: Path, monkeypatch
) -> None:
    payload = {
        instrument: _observation(instrument)
        for instrument in REALTIME_CROSS_MARKET_INSTRUMENTS
    }
    seen: dict[str, object] = {}

    def fake_fetch(*, timeout_seconds: float, now: datetime):
        seen.update(timeout_seconds=timeout_seconds, now=now)
        return payload

    monkeypatch.setattr(collector, "fetch_live_market_context_payload", fake_fetch)
    output = tmp_path / "sidecar.json"

    result = collector.collect_realtime_cross_market(
        output, timeout_seconds=0.75, now=NOW
    )

    assert result["status"] == "fresh"
    assert _read(output) == result
    assert result["generated_at"] == NOW.isoformat(timespec="seconds")
    assert result["payload"] == payload
    assert seen == {"timeout_seconds": 0.75, "now": NOW}


def test_collect_realtime_cross_market_writes_partial_when_some_instruments_fail(
    tmp_path: Path, monkeypatch
) -> None:
    payload = {
        instrument: _observation(instrument)
        for instrument in REALTIME_CROSS_MARKET_INSTRUMENTS
    }
    payload["WTI"] = {
        "status": "timeout",
        "value": None,
        "detail": "test timeout",
    }
    monkeypatch.setattr(
        collector,
        "fetch_live_market_context_payload",
        lambda **_: payload,
    )
    output = tmp_path / "sidecar.json"

    result = collector.collect_realtime_cross_market(output, now=NOW)

    assert result["status"] == "partial"
    assert _read(output)["payload"] == payload


def test_collect_realtime_cross_market_degrades_exception_to_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    def fail_fetch(**_kwargs: object) -> dict[str, dict[str, object]]:
        raise RuntimeError("source offline")

    monkeypatch.setattr(collector, "fetch_live_market_context_payload", fail_fetch)
    output = tmp_path / "nested" / "sidecar.json"

    result = collector.collect_realtime_cross_market(output, now=NOW)

    assert result == {
        "schema_version": collector.SCHEMA_VERSION,
        "generated_at": NOW.isoformat(timespec="seconds"),
        "status": "unavailable",
        "payload": {},
    }
    assert _read(output) == result
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_main_surfaces_artifact_write_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        collector,
        "collect_realtime_cross_market",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert collector.main(["--output", str(tmp_path / "sidecar.json")]) == 1
