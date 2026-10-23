"""Durable grouped delivery is synthetic and never contacts real channels."""

from __future__ import annotations

import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import pytest

import alert_events as ae
import live_risk_delivery as delivery
import tenant
from aqsp import notification_runtime as runtime
from aqsp.core.errors import DataError
from aqsp.notifier import NotifyResult
from aqsp.utils.jsonl_io import advisory_lock


NOW = datetime(2026, 10, 23, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _reminder(level: str, mode: str = "once") -> dict:
    return {
        "symbol": "600000",
        "position_id": "position-one",
        "level": level,
        "rule_id": f"600000-{level}",
        "freq_mode": mode,
        "current_price": 9,
        "threshold_price": 9.5,
        "sellable_now": True,
        "basis_json": {"rule": "synthetic rule", "actual": "synthetic price"},
    }


@pytest.fixture
def channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        tenant, "current_tenant", ContextVar("delivery", default="local")
    )
    clock = [NOW]
    monkeypatch.setattr(ae, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(delivery, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(runtime, "now_shanghai", lambda: clock[0])
    sent: list[dict] = []

    def dispatch(markdown: str, **kwargs: object) -> list[NotifyResult]:
        sent.append({"markdown": markdown, "tenant": tenant.current_tenant.get()})
        return [NotifyResult("synthetic", True, "stub")]

    monkeypatch.setattr(runtime, "dispatch_notification", dispatch)
    acknowledgements: list[tuple[tuple[str, ...], str]] = []

    def acknowledge(event_ids: Sequence[str], *, notified_at: str) -> None:
        acknowledgements.append((tuple(event_ids), notified_at))
        with ae._LOCK, advisory_lock(ae._file()):
            events = ae._load()
            for event in events:
                if event["id"] in event_ids and (
                    not event.get("last_notified_at")
                    or event["last_notified_at"] < notified_at
                ):
                    event["last_notified_at"] = notified_at
            ae._save(events)

    def run() -> delivery.PositionDeliveryResult:
        return delivery.deliver_position_alerts(
            {f"600000-{level}" for level in ("L1", "L2", "L4", "L5")},
            clock[0].isoformat(),
            render=lambda events: ",".join(event["id"] for event in events),
            acknowledge=acknowledge,
        )

    return {
        "clock": clock,
        "sent": sent,
        "ack": acknowledgements,
        "run": run,
        "acknowledge": acknowledge,
        "root": tmp_path,
    }


def test_live_risk_delivery_groups_rules_when_position_matches(channel: dict) -> None:
    events = ae.upsert_from_reminders([_reminder("L1"), _reminder("L5")])
    result = channel["run"]()
    assert len(channel["sent"]) == 1 and result.notify
    assert set(channel["sent"][0]["markdown"].split(",")) == {
        event["id"] for event in events
    }
    channel["run"]()
    assert len(channel["sent"]) == 1
    assert all(event["last_notified_at"] for event in ae.list_events())


def test_live_risk_delivery_recovers_members_when_acknowledgement_partially_fails(
    channel: dict,
) -> None:
    events = ae.upsert_from_reminders([_reminder("L1"), _reminder("L5")])
    ack = channel["acknowledge"]

    def partial(event_ids: Sequence[str], *, notified_at: str) -> None:
        ack(event_ids[:1], notified_at=notified_at)
        raise OSError("second member acknowledgement failed")

    with pytest.raises(OSError, match="second member"):
        delivery.deliver_position_alerts(
            {"600000-L1", "600000-L5"},
            NOW.isoformat(),
            render=lambda rows: ",".join(row["id"] for row in rows),
            acknowledge=partial,
        )
    new_event = ae.upsert_from_reminders([_reminder("L2")])[0]
    channel["run"]()
    assert len(channel["sent"]) == 2
    assert channel["sent"][1]["markdown"] == new_event["id"]
    assert all(event["last_notified_at"] for event in ae.list_events())
    assert not set(channel["sent"][1]["markdown"].split(",")) & {
        event["id"] for event in events
    }


def test_live_risk_delivery_recovers_runtime_receipt_when_local_receipt_write_fails(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ae.upsert_from_reminders([_reminder("L1"), _reminder("L5")])
    save = delivery._save_batches

    def fail_sent(path: Path, batches: Sequence[delivery._Batch]) -> None:
        if any(batch.sent_at for batch in batches):
            raise OSError("local sent receipt failed")
        save(path, batches)

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_save_batches", fail_sent)
        with pytest.raises(OSError, match="local sent"):
            channel["run"]()
    new = ae.upsert_from_reminders([_reminder("L2")])[0]
    channel["run"]()
    assert len(channel["sent"]) == 2
    assert channel["sent"][1]["markdown"] == new["id"]
    assert all(event["last_notified_at"] for event in ae.list_events())
    assert all(event["id"] not in channel["sent"][1]["markdown"] for event in first)


def test_live_risk_delivery_never_sends_when_batch_cannot_be_persisted(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ae.upsert_from_reminders([_reminder("L1")])
    monkeypatch.setattr(
        delivery,
        "_save_batches",
        lambda *args: (_ for _ in ()).throw(OSError("prepare failed")),
    )
    with pytest.raises(OSError, match="prepare failed"):
        channel["run"]()
    assert not channel["sent"] and not channel["ack"]


def test_live_risk_delivery_does_not_consume_when_dispatch_returns_empty(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ae.upsert_from_reminders([_reminder("L1")])
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "dispatch_notification_once", lambda *a, **kw: [])
        assert channel["run"]().notify_suppressed
    assert not channel["ack"]
    assert ae.list_events()[0]["last_notified_at"] is None
    channel["run"]()
    assert len(channel["sent"]) == 1 and channel["ack"]


def test_live_risk_delivery_respects_failure_cooldown_when_all_channels_fail(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ae.upsert_from_reminders([_reminder("L1")])
    with monkeypatch.context() as patch:
        patch.setattr(
            runtime,
            "dispatch_notification",
            lambda *a, **kw: [NotifyResult("stub", False, "failure")],
        )
        assert channel["run"]().notify
    assert channel["run"]().notify_suppressed
    assert not channel["sent"] and not channel["ack"]
    channel["clock"][0] += timedelta(minutes=61)
    channel["run"]()
    assert len(channel["sent"]) == 1 and len(channel["ack"]) == 1


def test_live_risk_delivery_preserves_modes_when_once_daily_always_share_position(
    channel: dict,
) -> None:
    events = ae.upsert_from_reminders(
        [
            _reminder("L1"),
            _reminder("L2", "daily"),
            _reminder("L5", "always"),
        ]
    )
    by_level = {event["level"]: event["id"] for event in events}
    channel["run"]()
    channel["run"]()
    assert channel["sent"][1]["markdown"] == by_level["L5"]
    channel["clock"][0] += timedelta(days=1)
    channel["run"]()
    assert set(channel["sent"][2]["markdown"].split(",")) == {
        by_level["L2"],
        by_level["L5"],
    }
    assert len(channel["sent"]) == 3


def test_live_risk_delivery_recovers_source_send_day_when_ack_delayed_until_next_day(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = ae.upsert_from_reminders([_reminder("L2", "daily")])[0]
    save = delivery._save_batches

    def fail_sent(path: Path, batches: Sequence[delivery._Batch]) -> None:
        if any(batch.sent_at for batch in batches):
            raise OSError("receipt write failed")
        save(path, batches)

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_save_batches", fail_sent)
        with pytest.raises(OSError):
            channel["run"]()
    channel["clock"][0] += timedelta(days=1)
    channel["run"]()
    assert channel["ack"][0] == ((event["id"],), NOW.isoformat())
    assert len(channel["sent"]) == 2


def test_live_risk_delivery_blocks_uncertain_once_when_runtime_receipt_write_fails(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ae.upsert_from_reminders([_reminder("L1"), _reminder("L5")])
    with monkeypatch.context() as patch:
        patch.setattr(
            runtime,
            "mark_notification_sent",
            lambda **kw: (_ for _ in ()).throw(OSError("runtime sent write failed")),
        )
        with pytest.raises(OSError, match="runtime sent"):
            channel["run"]()
    new = ae.upsert_from_reminders([_reminder("L2")])[0]
    result = channel["run"]()
    assert result.notify_suppressed and any(
        "结果未确认" in row for row in result.notify
    )
    assert len(channel["sent"]) == 2
    assert channel["sent"][1]["markdown"] == new["id"]
    assert all(
        not event["last_notified_at"]
        for event in ae.list_events()
        if event["id"] in {row["id"] for row in first}
    )


def test_live_risk_delivery_serializes_same_tenant_when_new_rule_arrives_mid_send(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ae.upsert_from_reminders([_reminder("L1"), _reminder("L5")])
    entered, release, second_started = Event(), Event(), Event()
    calls = []

    def blocked(markdown: str, **kwargs: object) -> list[NotifyResult]:
        calls.append(markdown)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return [NotifyResult("stub", True, "ok")]

    monkeypatch.setattr(runtime, "dispatch_notification", blocked)

    def second() -> delivery.PositionDeliveryResult:
        second_started.set()
        return channel["run"]()

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(channel["run"])
        assert entered.wait(5)
        new = ae.upsert_from_reminders([_reminder("L2")])[0]
        b = executor.submit(second)
        assert second_started.wait(5)
        assert not b.done()
        release.set()
        a.result(timeout=5)
        b.result(timeout=5)
    assert len(calls) == 2 and calls[1] == new["id"]
    assert set(calls[0].split(",")) == {event["id"] for event in first}


def test_live_risk_delivery_isolates_tenants_when_one_sender_is_blocked(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = Event(), Event()
    sent = []

    def dispatch(markdown: str, **kwargs: object) -> list[NotifyResult]:
        tid = tenant.current_tenant.get()
        sent.append(tid)
        if tid == "user-a":
            entered.set()
            assert release.wait(5)
        return [NotifyResult("stub", True, "ok")]

    monkeypatch.setattr(runtime, "dispatch_notification", dispatch)

    def run(tid: str) -> delivery.PositionDeliveryResult:
        token = tenant.current_tenant.set(tid)
        try:
            ae.upsert_from_reminders([_reminder("L1")])
            return channel["run"]()
        finally:
            tenant.current_tenant.reset(token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(run, "user-a")
        assert entered.wait(5)
        b = executor.submit(run, "user-b")
        try:
            assert b.result(timeout=3).notify
        finally:
            release.set()
        a.result(timeout=5)
    assert set(sent) == {"user-a", "user-b"}


@pytest.mark.parametrize("payload", ["broken", '{"version":2,"batches":[]}', "{}"])
def test_live_risk_delivery_fails_closed_when_member_journal_is_invalid(
    channel: dict,
    payload: str,
) -> None:
    ae.upsert_from_reminders([_reminder("L1")])
    (channel["root"] / "live-risk-delivery.json").write_text(payload)
    with pytest.raises(DataError):
        channel["run"]()
    assert not channel["sent"] and not channel["ack"]


def test_live_risk_delivery_recovers_legacy_once_receipt_when_upgrading(
    channel: dict,
) -> None:
    event = ae.upsert_from_reminders([_reminder("L1")])[0]
    runtime.mark_notification_sent(
        kind=f"live-risk|{event['id']}|once",
        markdown="legacy",
        state_path=channel["root"] / "notify-state-live-risk.json",
    )
    channel["run"]()
    assert not channel["sent"]
    assert channel["ack"] == [((event["id"],), NOW.isoformat())]


def test_live_risk_delivery_omits_expired_members_when_failed_batch_is_retried(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ae.upsert_from_reminders([_reminder("L1"), _reminder("L5")])
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "dispatch_notification_once", lambda *a, **kw: [])
        channel["run"]()
    ae.upsert_from_reminders([], checked_rules={"600000-L1"})
    channel["run"]()
    assert channel["sent"][0]["markdown"] == first[1]["id"]
    journal = json.loads((channel["root"] / "live-risk-delivery.json").read_text())
    assert len(journal["batches"]) == 1


@pytest.mark.parametrize("legacy_hash", [False, True])
def test_live_risk_delivery_recovers_legacy_string_when_fingerprint_has_old_shape(
    channel: dict,
    legacy_hash: bool,
) -> None:
    event = ae.upsert_from_reminders([_reminder("L1")])[0]
    kind = f"live-risk|{event['id']}|once"
    fingerprint = kind + ":" + "a" * 64 if legacy_hash else kind
    (channel["root"] / "notify-state-live-risk.json").write_text(
        json.dumps({"sent": {kind: fingerprint}})
    )
    channel["run"]()
    assert not channel["sent"] and len(channel["ack"]) == 1


def test_live_risk_delivery_separates_position_identity_and_t1_state(
    channel: dict,
) -> None:
    events = ae.upsert_from_reminders(
        [
            _reminder("L1"),
            {**_reminder("L5"), "position_id": "another-position"},
            {**_reminder("L2"), "sellable_now": False},
        ]
    )
    channel["run"]()
    assert len(channel["sent"]) == 3
    assert {item["markdown"] for item in channel["sent"]} == {
        event["id"] for event in events
    }


def test_live_risk_delivery_consumes_when_one_channel_confirms_success(
    channel: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ae.upsert_from_reminders([_reminder("L1")])
    monkeypatch.setattr(
        runtime,
        "dispatch_notification",
        lambda *a, **kw: [
            NotifyResult("a", False, "failed"),
            NotifyResult("b", True, "ok"),
        ],
    )
    assert len(channel["run"]().notify) == 2
    assert len(channel["ack"]) == 1
    assert not channel["run"]().notify


def test_live_risk_delivery_rejects_when_check_timestamp_has_no_timezone(
    channel: dict,
) -> None:
    with pytest.raises(ValueError, match="timezone"):
        delivery.deliver_position_alerts(
            set(),
            "2026-10-23T13:00:00",
            render=lambda rows: "",
            acknowledge=channel["acknowledge"],
        )
    assert not channel["sent"]
