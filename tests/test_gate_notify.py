from __future__ import annotations

import pytest

from aqsp.runtime.gate_notify import (
    build_gate_notification_markdown,
    gate_reason_fingerprint,
    mark_gate_notification_failed,
    mark_gate_notification_sent,
    reserve_gate_notification,
    should_send_gate_notification,
)


def test_gate_reason_fingerprint_normalizes_progressing_cold_start_counts() -> None:
    first = gate_reason_fingerprint(
        ["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"]
    )
    second = gate_reason_fingerprint(
        ["冷启动未满: 1/30 个独立信号日", "DSR 未过门: 0.2（需 >1.0）"]
    )
    assert first == second == "cold_start|dsr"


def test_build_gate_notification_markdown_keeps_compact_summary() -> None:
    markdown = build_gate_notification_markdown(
        run_date="2026-06-15",
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        next_actions=["继续按日运行主链，先把冷启动样本积累到 30 个独立信号日。"],
    )

    assert markdown.startswith("# 通知未放行-2026-06-15")
    assert "本次正常通知未放行" in markdown
    assert "## 状态" in markdown
    assert "## 阻塞" in markdown
    assert "- 冷启动未满: 0/30 个独立信号日" in markdown
    assert "- DSR 未过门: 0.0（需 >1.0）" in markdown
    assert "## 处理" not in markdown
    assert "继续按日运行主链" not in markdown


def test_should_send_gate_notification_dedupes_same_normalized_reasons(
    tmp_path,
) -> None:
    state_path = tmp_path / "gate_notify_state.json"

    first = should_send_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )
    second = should_send_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 1/30 个独立信号日", "DSR 未过门: 0.2（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )
    mark_gate_notification_sent(
        gate_reasons=["冷启动未满: 1/30 个独立信号日", "DSR 未过门: 0.2（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )
    duplicate_after_sent = should_send_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 2/30 个独立信号日", "DSR 未过门: 0.3（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )
    next_day = should_send_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 2/30 个独立信号日", "DSR 未过门: 0.2（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-18",
    )
    cleared = should_send_gate_notification(
        gate_ok=True,
        gate_reasons=[],
        state_path=state_path,
    )

    assert first is True
    assert second is True
    assert duplicate_after_sent is False
    assert next_day is True
    assert cleared is False
    assert not state_path.exists()


def test_reserve_gate_notification_dedupes_before_delivery(tmp_path) -> None:
    state_path = tmp_path / "gate_notify_state.json"

    first = reserve_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )
    second = reserve_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 1/30 个独立信号日", "DSR 未过门: 0.2（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )

    assert first is True
    assert second is False


def test_failed_gate_notification_blocks_same_day_retry(tmp_path) -> None:
    state_path = tmp_path / "gate_notify_state.json"
    reasons = ["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"]

    assert reserve_gate_notification(
        gate_ok=False,
        gate_reasons=reasons,
        state_path=state_path,
        run_date="2026-06-17",
    )
    mark_gate_notification_failed(
        gate_reasons=reasons,
        state_path=state_path,
        run_date="2026-06-17",
    )

    assert (
        should_send_gate_notification(
            gate_ok=False,
            gate_reasons=[
                "冷启动未满: 8/30 个独立信号日",
                "DSR 未过门: 0.2（需 >1.0）",
            ],
            state_path=state_path,
            run_date="2026-06-17",
        )
        is False
    )
    assert reserve_gate_notification(
        gate_ok=False,
        gate_reasons=reasons,
        state_path=state_path,
        run_date="2026-06-18",
    )


def test_gate_notification_blocks_same_day_retry_when_fingerprint_changes(
    tmp_path,
) -> None:
    state_path = tmp_path / "gate_notify_state.json"

    assert reserve_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )
    mark_gate_notification_failed(
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )

    assert (
        should_send_gate_notification(
            gate_ok=False,
            gate_reasons=["PBO 未过门: 60.0%（需 <50%）"],
            state_path=state_path,
            run_date="2026-06-17",
        )
        is False
    )


def test_gate_notification_blocks_same_day_retry_globally_after_sent(
    tmp_path,
) -> None:
    state_path = tmp_path / "gate_notify_state.json"

    assert reserve_gate_notification(
        gate_ok=False,
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )
    mark_gate_notification_sent(
        gate_reasons=["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"],
        state_path=state_path,
        run_date="2026-06-17",
    )

    assert (
        should_send_gate_notification(
            gate_ok=False,
            gate_reasons=["PBO 未过门: 60.0%（需 <50%）"],
            state_path=state_path,
            run_date="2026-06-17",
        )
        is False
    )


def test_failed_gate_notification_blocks_same_day_retry_after_pending_ttl(
    tmp_path,
    monkeypatch,
) -> None:
    from aqsp.runtime import gate_notify as mod

    state_path = tmp_path / "gate_notify_state.json"
    reasons = ["冷启动未满: 0/30 个独立信号日", "DSR 未过门: 0.0（需 >1.0）"]

    assert reserve_gate_notification(
        gate_ok=False,
        gate_reasons=reasons,
        state_path=state_path,
        run_date="2026-06-17",
    )
    mark_gate_notification_failed(
        gate_reasons=reasons,
        state_path=state_path,
        run_date="2026-06-17",
    )

    class _FakeNow:
        def __init__(self, iso: str) -> None:
            self._iso = iso

        def isoformat(self, timespec: str = "seconds") -> str:
            del timespec
            return self._iso

        def __sub__(self, other):
            from datetime import datetime

            return datetime.fromisoformat(self._iso) - other

    monkeypatch.setattr(mod, "now_shanghai", lambda: _FakeNow("2026-06-17T23:30:00+08:00"))

    assert (
        should_send_gate_notification(
            gate_ok=False,
            gate_reasons=[
                "冷启动未满: 20/30 个独立信号日",
                "DSR 未过门: 0.8（需 >1.0）",
            ],
            state_path=state_path,
            run_date="2026-06-17",
        )
        is False
    )


def test_gate_notify_state_path_rejects_tmp_path(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_PROJECT_ROOT", "/Users/ivon/Documents/AI量化选股")

    with pytest.raises(OSError):
        should_send_gate_notification(
            gate_ok=False,
            gate_reasons=["冷启动未满: 0/30 个独立信号日"],
            state_path="/tmp/gate_notify_state.json",
            run_date="2026-06-17",
        )
