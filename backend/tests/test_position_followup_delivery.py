"""Synthetic plan-delivery channels; no test contacts notification services."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from threading import Event
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import alert_events as ae
import position_followup_delivery as delivery
import tenant
from aqsp import notification_runtime as runtime
from aqsp.core.errors import DataError
from aqsp.notifier import NotifyResult


NOW = datetime(2026, 10, 27, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def candidate(key: str = "position-plan|position-one|plan-v1|above|once") -> dict[str, Any]:
    return {"key": key, "position_id": "position-one", "symbol": "600000", "name": "合成测试股",
            "state": "above", "title": "已到观察价，转弱尚未确认", "detail": "当前价已到用户观察价，先核对证据。",
            "observed_at": NOW.isoformat(), "quote_as_of": NOW.isoformat(),
            "plan_href": "/ignored-untrusted-link", "mode": "once"}


@pytest.fixture
def channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tenant, "current_tenant", ContextVar("plan-delivery", default="local"))
    clock = [NOW]
    monkeypatch.setattr(delivery, "now_shanghai", lambda: clock[0])
    monkeypatch.setattr(runtime, "now_shanghai", lambda: clock[0])
    calls: list[tuple[str, str]] = []

    def dispatch(markdown: str, **kwargs: Any) -> list[NotifyResult]:
        calls.append((tenant.current_tenant.get(), markdown))
        return [NotifyResult("synthetic", True, "stub")]

    monkeypatch.setattr(runtime, "dispatch_notification", dispatch)

    def run(rows: list[dict[str, Any]] | None = None, *, validate: Callable[[dict[str, Any]], bool] = lambda _: True) -> delivery.FollowupDeliveryResult:
        return delivery.deliver_followup_notifications([candidate()] if rows is None else rows, validate=validate)

    return {"root": tmp_path, "calls": calls, "clock": clock, "run": run,
            "journal": tmp_path / "position-plan-delivery.json", "runtime": tmp_path / "notify-state-position-plan.json"}


def test_position_followup_delivery_sends_once_when_same_key_repeats(channel: dict[str, Any]) -> None:
    result = channel["run"]()
    assert result.notify and not result.suppressed
    assert channel["run"]().suppressed
    assert len(channel["calls"]) == 1
    text = channel["calls"][0][1]
    assert "合成测试股" in text and "600000" in text and "行情源时点：2026-10-27T10:00:00+08:00" in text
    assert "到价不等于买卖信号或已成交" in text
    assert "[查看处理计划](https://lh.ifidy.cn/my/holdings/position-one/plan)" in text
    assert "ignored-untrusted-link" not in text


def test_position_followup_delivery_preserves_frozen_content_when_retry_has_new_quote(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "dispatch_notification", lambda *a, **k: [NotifyResult("synthetic", False, "stub")])
        assert channel["run"]().suppressed
    changed = {**candidate(), "detail": "随后观察到不同报价", "observed_at": (NOW + timedelta(minutes=61)).isoformat()}
    channel["clock"][0] += timedelta(minutes=61)
    checked: list[dict[str, Any]] = []
    result = channel["run"]([changed], validate=lambda row: checked.append(row) or True)
    assert not result.suppressed and checked == [candidate()]
    assert "随后观察到不同报价" not in channel["calls"][0][1]


def test_position_followup_delivery_respects_cooldown_when_all_channels_fail(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    failed: list[str] = []

    def dispatch(markdown: str, **kwargs: Any) -> list[NotifyResult]:
        failed.append(markdown)
        return [NotifyResult("synthetic", False, "stub")]

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "dispatch_notification", dispatch)
        assert channel["run"]().suppressed
        assert channel["run"]().suppressed
        assert len(failed) == 1
        channel["clock"][0] += timedelta(minutes=61)
        channel["run"]()
        assert len(failed) == 2
    assert not json.loads(channel["journal"].read_text())["batches"][0]["sent_at"]


def test_position_followup_delivery_does_not_claim_sent_when_no_channel_exists(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "dispatch_notification", lambda *a, **k: [])
    result = channel["run"]()
    assert result.suppressed and all("已发送" not in text for text in result.notify)
    row = json.loads(channel["journal"].read_text())["batches"][0]
    assert row["sent_at"] is None and not row["uncertain"]
    assert not json.loads(channel["runtime"].read_text())["sent"]


def test_position_followup_delivery_blocks_same_key_when_exception_has_no_receipt(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: Any, **kwargs: Any) -> list[NotifyResult]:
        raise OSError("synthetic interrupted send")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "dispatch_notification", explode)
        with pytest.raises(OSError, match="interrupted"):
            channel["run"]()
    channel["clock"][0] += timedelta(days=2)
    result = channel["run"]()
    assert result.suppressed and any("结果未确认" in line for line in result.notify)
    assert not channel["calls"]
    # Another disjoint transition remains deliverable.
    channel["run"]([candidate("position-plan|position-one|plan-v2|below|once")])
    assert len(channel["calls"]) == 1


def test_position_followup_delivery_recovers_sent_receipt_when_local_write_failed(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    save = delivery._save_batches

    def fail_sent(path: Path, batches: list[delivery._Batch]) -> None:
        if any(batch.sent_at for batch in batches):
            raise OSError("synthetic local receipt failure")
        save(path, batches)

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_save_batches", fail_sent)
        with pytest.raises(OSError, match="local receipt"):
            channel["run"]()
    assert len(channel["calls"]) == 1
    # Fresh process-equivalent state comes only from persisted journals.
    assert channel["run"]().suppressed
    assert len(channel["calls"]) == 1
    stored = json.loads(channel["journal"].read_text())["batches"][0]
    assert stored["sent_at"] == NOW.isoformat() and not stored["uncertain"]


def test_position_followup_delivery_stays_uncertain_when_runtime_success_receipt_write_fails(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**kwargs: Any) -> None:
        raise OSError("synthetic runtime receipt failure")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "mark_notification_sent", fail)
        with pytest.raises(OSError, match="runtime receipt"):
            channel["run"]()
    assert len(channel["calls"]) == 1
    assert channel["run"]().suppressed
    assert len(channel["calls"]) == 1


def test_position_followup_delivery_does_not_fabricate_receipt_when_channel_reports_success_alone(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "dispatch_notification_once", lambda *a, **k: [NotifyResult("synthetic", True, "no durable receipt")])
    result = channel["run"]()
    assert result.suppressed and all("已发送" not in text for text in result.notify)
    assert json.loads(channel["journal"].read_text())["batches"][0]["uncertain"]


def test_position_followup_delivery_cancels_without_sending_when_position_changed(channel: dict[str, Any]) -> None:
    result = channel["run"](validate=lambda _: False)
    assert result.suppressed and not channel["calls"]
    assert channel["run"]().suppressed and not channel["calls"]


def test_position_followup_delivery_recovers_cancelled_work_when_new_observation_is_valid(channel: dict[str, Any]) -> None:
    channel["run"](validate=lambda _: False)
    fresh = {**candidate(), "observed_at": (NOW + timedelta(minutes=1)).isoformat(),
             "quote_as_of": (NOW + timedelta(minutes=1)).isoformat(), "detail": "更新行情已复核"}
    assert channel["run"]([fresh], validate=lambda _: False).suppressed
    assert json.loads(channel["journal"].read_text())["batches"][0]["candidate"] == candidate()
    result = channel["run"]([fresh])
    assert not result.suppressed and len(channel["calls"]) == 1
    assert "更新行情已复核" in channel["calls"][0][1]
    later = {**fresh, "observed_at": (NOW + timedelta(minutes=2)).isoformat(), "detail": "成功后不再改写"}
    assert channel["run"]([later]).suppressed
    assert len(channel["calls"]) == 1
    stored = json.loads(channel["journal"].read_text())["batches"][0]
    assert stored["candidate"] == fresh and stored["sent_at"] and not stored["cancelled"]


def test_position_followup_delivery_keeps_failure_cooldown_when_cancelled_work_recovers(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    failed: list[str] = []

    def fail(markdown: str, **kwargs: Any) -> list[NotifyResult]:
        failed.append(markdown)
        return [NotifyResult("synthetic", False, "stub")]

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "dispatch_notification", fail)
        channel["run"]()
    fresh = {**candidate(), "observed_at": (NOW + timedelta(minutes=1)).isoformat(),
             "quote_as_of": (NOW + timedelta(minutes=1)).isoformat(), "detail": "失败后更新行情"}
    # This call only cancels the original content, even with a fresh incoming row.
    channel["run"]([fresh], validate=lambda row: row == fresh)
    stored = json.loads(channel["journal"].read_text())["batches"][0]
    assert stored["cancelled"] and stored["candidate"] == candidate()
    assert channel["run"]([fresh]).suppressed
    assert len(failed) == 1 and not channel["calls"]
    assert json.loads(channel["runtime"].read_text())["failed"]
    channel["clock"][0] += timedelta(minutes=61)
    assert not channel["run"]([fresh]).suppressed
    assert len(channel["calls"]) == 1 and "失败后更新行情" in channel["calls"][0][1]


@pytest.mark.parametrize("guard", ["pending", "uncertain"])
def test_position_followup_delivery_does_not_rearm_when_prior_send_might_exist(channel: dict[str, Any], guard: str) -> None:
    channel["run"](validate=lambda _: False)
    if guard == "pending":
        runtime.reserve_notification(kind=candidate()["key"], markdown=delivery._render(candidate()), state_path=channel["runtime"])
        channel["clock"][0] += timedelta(days=2)  # Even expired pending is uncertain.
    else:
        journal = json.loads(channel["journal"].read_text())
        journal["batches"][0]["uncertain"] = True
        channel["journal"].write_text(json.dumps(journal))
    fresh = {**candidate(), "observed_at": (NOW + timedelta(days=3)).isoformat(), "detail": "不能重臂"}
    assert channel["run"]([fresh]).suppressed
    stored = json.loads(channel["journal"].read_text())["batches"][0]
    assert stored["candidate"] == candidate() and stored["cancelled"]
    assert not channel["calls"]


def test_position_followup_delivery_restores_success_before_cancelled_recovery(channel: dict[str, Any]) -> None:
    channel["run"](validate=lambda _: False)
    runtime.mark_notification_sent(kind=candidate()["key"], markdown=delivery._render(candidate()), state_path=channel["runtime"])
    fresh = {**candidate(), "observed_at": (NOW + timedelta(minutes=1)).isoformat(), "detail": "不能覆盖已有成功"}
    assert channel["run"]([fresh]).suppressed and not channel["calls"]
    stored = json.loads(channel["journal"].read_text())["batches"][0]
    assert stored["sent_at"] and stored["candidate"] == candidate()


@pytest.mark.parametrize("change", [
    {"observed_at": NOW.isoformat()},
    {"quote_as_of": (NOW - timedelta(minutes=1)).isoformat()},
    {"quote_as_of": None},
    {"position_id": "another-position"},
    {"symbol": "000001"},
    {"state": "below"},
    {"mode": "daily"},
])
def test_position_followup_delivery_keeps_cancelled_when_observation_regresses(channel: dict[str, Any], change: dict[str, Any]) -> None:
    channel["run"](validate=lambda _: False)
    fresh = {**candidate(), "observed_at": (NOW + timedelta(minutes=1)).isoformat(), **change}
    assert channel["run"]([fresh]).suppressed and not channel["calls"]
    assert json.loads(channel["journal"].read_text())["batches"][0]["cancelled"]


def test_position_followup_delivery_does_not_send_when_prepare_write_fails(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any) -> None:
        raise OSError("synthetic prepare failure")

    monkeypatch.setattr(delivery, "_save_batches", fail)
    with pytest.raises(OSError, match="prepare"):
        channel["run"]()
    assert not channel["calls"]


@pytest.mark.parametrize("payload", ["{", "[]", '{"version":true,"batches":[]}', '{"version":1,"batches":[{}]}'])
def test_position_followup_delivery_fails_closed_when_journal_invalid(channel: dict[str, Any], payload: str) -> None:
    channel["journal"].write_text(payload)
    with pytest.raises(DataError):
        channel["run"]()
    assert not channel["calls"]


@pytest.mark.parametrize("payload", ["{", "[]", '{"sent":[]}', '{"pending":{"position-plan|x":{"fingerprint":"wrong"}}}', '{"failed":[]}'])
def test_position_followup_delivery_fails_closed_when_runtime_receipt_invalid(channel: dict[str, Any], payload: str) -> None:
    channel["runtime"].write_text(payload)
    with pytest.raises(DataError):
        channel["run"]()
    assert not channel["calls"]


@pytest.mark.parametrize("journal_marked_sent", [True, False])
def test_position_followup_delivery_fails_closed_when_receipt_content_differs(channel: dict[str, Any], journal_marked_sent: bool) -> None:
    channel["run"]()
    state = json.loads(channel["runtime"].read_text())
    state["sent"][candidate()["key"]]["content_hash"] = "0" * 64
    channel["runtime"].write_text(json.dumps(state))
    if not journal_marked_sent:
        journal = json.loads(channel["journal"].read_text())
        journal["batches"][0]["sent_at"] = None
        channel["journal"].write_text(json.dumps(journal))
    with pytest.raises(DataError, match="冻结内容"):
        channel["run"]()
    assert len(channel["calls"]) == 1


def test_position_followup_delivery_rejects_input_without_send_when_keys_conflict(channel: dict[str, Any]) -> None:
    row = candidate()
    with pytest.raises(ValueError, match="冲突"):
        channel["run"]([row, {**row, "title": "another explanation"}])
    assert not channel["calls"] and not channel["journal"].exists()


def test_position_followup_delivery_escapes_href_and_fields_when_candidate_has_markdown(channel: dict[str, Any]) -> None:
    row = {**candidate(), "position_id": "position/other#?", "name": "[bad](https://evil.invalid)\n# heading"}
    channel["run"]([row])
    text = channel["calls"][0][1]
    assert "https://lh.ifidy.cn/my/holdings/position%2Fother%23%3F/plan" in text
    assert "\n# heading" not in text and "[bad](https://evil.invalid)" not in text


def test_position_followup_delivery_serializes_same_key_when_two_checks_overlap(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release, second_started = Event(), Event(), Event()
    sent: list[str] = []

    def dispatch(markdown: str, **kwargs: Any) -> list[NotifyResult]:
        sent.append(markdown)
        entered.set()
        assert release.wait(5)
        return [NotifyResult("synthetic", True, "stub")]

    def second() -> delivery.FollowupDeliveryResult:
        second_started.set()
        return channel["run"]()

    monkeypatch.setattr(runtime, "dispatch_notification", dispatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(channel["run"])
        assert entered.wait(5)
        other = executor.submit(second)
        assert second_started.wait(5) and not other.done()
        release.set()
        assert not first.result(timeout=5).suppressed
        assert other.result(timeout=5).suppressed
    assert len(sent) == 1


def test_position_followup_delivery_isolates_tenants_when_one_send_blocks(channel: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = Event(), Event()
    sent: list[str] = []

    def dispatch(markdown: str, **kwargs: Any) -> list[NotifyResult]:
        tid = tenant.current_tenant.get()
        sent.append(tid)
        if tid == "u_a":
            entered.set()
            assert release.wait(5)
        return [NotifyResult("synthetic", True, "stub")]

    def run(tid: str) -> delivery.FollowupDeliveryResult:
        token = tenant.current_tenant.set(tid)
        try:
            return channel["run"]()
        finally:
            tenant.current_tenant.reset(token)

    monkeypatch.setattr(runtime, "dispatch_notification", dispatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run, "u_a")
        assert entered.wait(5)
        assert not executor.submit(run, "u_b").result(timeout=3).suppressed
        release.set()
        first.result(timeout=5)
    assert set(sent) == {"u_a", "u_b"}
    assert run("u_a").suppressed and run("u_b").suppressed
    assert len(sent) == 2
    for tid in sent:
        journal = channel["root"] / "users" / tid / "position-plan-delivery.json"
        assert json.loads(journal.read_text())["batches"][0]["sent_at"]
        assert tid not in json.loads(journal.read_text())["batches"][0]["markdown"]


def test_position_followup_delivery_preserves_snapshot_when_validation_mutates_input(channel: dict[str, Any]) -> None:
    row = candidate()
    original = deepcopy(row)

    def validate(value: dict[str, Any]) -> bool:
        value["title"] = "mutated callback input"
        return True

    channel["run"]([row], validate=validate)
    assert row == original and "mutated callback input" not in channel["calls"][0][1]
