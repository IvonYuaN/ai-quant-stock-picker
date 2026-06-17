from __future__ import annotations

from aqsp.runtime.gate_notify import (
    build_gate_notification_markdown,
    gate_reason_fingerprint,
    mark_gate_notification_sent,
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
    assert "## 阻塞原因" in markdown
    assert "- 冷启动未满: 0/30 个独立信号日" in markdown
    assert "- DSR 未过门: 0.0（需 >1.0）" in markdown
    assert "## 处理" in markdown
    assert "- 继续按日运行主链" in markdown


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
