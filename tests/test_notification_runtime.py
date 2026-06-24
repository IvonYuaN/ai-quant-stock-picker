from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from aqsp.notification_runtime import (
    dispatch_notification_once,
    dispatch_scheduled_daily_notification,
    finalize_scheduled_notification,
    finalize_scheduled_outputs,
    gate_notification_allowed,
)
from aqsp.notifier import NotifyResult


def test_finalize_scheduled_notification_disables_notify_and_prefixes_markdown(
    monkeypatch,
) -> None:
    seen: list[str] = []
    gate_calls: list[tuple[str, list[str], list[str], str]] = []
    state_calls: list[dict[str, object]] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    artifacts = finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 0/30 个独立信号日"],
        next_actions=["继续按日运行主链。"],
        latest_iso="2026-06-15",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **kwargs: (
            gate_calls.append(
                (
                    kwargs["run_date"],
                    kwargs["gate_reasons"],
                    kwargs["next_actions"],
                    kwargs["mode"],
                    kwargs["reserve_before_send"],
                )
            )
            or []
        ),
        should_send_gate_notification_fn=lambda **kwargs: (
            state_calls.append(kwargs) or True
        ),
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=None,
        print_fn=seen.append,
    )

    assert artifacts.notify_enabled is False
    assert artifacts.markdown.startswith(
        "BLOCK:冷启动未满: 0/30 个独立信号日|继续按日运行主链。"
    )
    assert gate_calls == [
        (
            "2026-06-15",
            ["冷启动未满: 0/30 个独立信号日"],
            ["继续按日运行主链。"],
            "summary",
            True,
        )
    ]
    assert state_calls == [
        {
            "gate_ok": False,
            "gate_reasons": ["冷启动未满: 0/30 个独立信号日"],
            "state_path": None,
            "run_date": "2026-06-15",
        }
    ]
    assert any("双门未达" in line for line in seen)


def test_finalize_scheduled_notification_uses_prebuilt_gate_block_markdown(
    monkeypatch,
) -> None:
    legacy_seen: list[str] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    artifacts = finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 0/30 个独立信号日"],
        next_actions=["继续按日运行主链。"],
        gate_block_markdown="PREBUILT\n",
        latest_iso="2026-06-15",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: [],
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda *_args: "SHOULD_NOT_USE\n",
        legacy_notify_fn=lambda markdown: legacy_seen.append(markdown) or [],
        print_fn=lambda *_args: None,
    )

    assert artifacts.markdown.startswith("PREBUILT\n# 原始报告")
    assert legacy_seen[0].startswith("# 通知未放行-2026-06-15")


def test_finalize_scheduled_notification_prefers_legacy_notify_when_patched(
    monkeypatch,
) -> None:
    seen: list[str] = []
    legacy_seen: list[str] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    artifacts = finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 0/30 个独立信号日"],
        next_actions=["继续按日运行主链。"],
        latest_iso="2026-06-15",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: [],
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=lambda markdown: legacy_seen.append(markdown) or [],
        print_fn=seen.append,
    )

    assert artifacts.notify_enabled is False
    assert legacy_seen
    assert legacy_seen[0].startswith("# 通知未放行-2026-06-15")


def test_finalize_scheduled_notification_suppresses_gate_push_outside_daily(
    monkeypatch,
) -> None:
    seen: list[str] = []
    gate_calls: list[str] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")

    artifacts = finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 14/30 个独立信号日"],
        next_actions=["继续按日运行主链。"],
        latest_iso="2026-06-17",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: gate_calls.append("sent") or [],
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=None,
        print_fn=seen.append,
    )

    assert artifacts.notify_enabled is False
    assert gate_calls == []
    assert "gate notify: skipped outside daily task" in seen


def test_finalize_scheduled_notification_uses_explicit_task_id_over_env(
    monkeypatch,
) -> None:
    seen: list[str] = []
    gate_calls: list[str] = []
    legacy_calls: list[str] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 14/30 个独立信号日"],
        next_actions=["继续按日运行主链。"],
        latest_iso="2026-06-17",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: gate_calls.append("sent") or [],
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=lambda markdown: legacy_calls.append(markdown) or [],
        print_fn=seen.append,
        task_id="intraday",
    )

    assert gate_calls == []
    assert legacy_calls == []
    assert "gate notify: skipped outside daily task" in seen


def test_finalize_scheduled_notification_fails_closed_when_gate_state_fails(
    monkeypatch,
) -> None:
    seen: list[str] = []
    gate_calls: list[str] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    artifacts = finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 14/30 个独立信号日"],
        next_actions=["冷启动样本 14/30。"],
        latest_iso="2026-06-17",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: gate_calls.append("sent") or [],
        should_send_gate_notification_fn=lambda **_kwargs: (_ for _ in ()).throw(
            OSError("state locked")
        ),
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=None,
        print_fn=seen.append,
    )

    assert artifacts.notify_enabled is False
    assert gate_calls == []
    assert "gate notify state failed: state locked" in seen


def test_finalize_scheduled_notification_marks_gate_only_after_success(
    monkeypatch,
) -> None:
    marked: list[dict[str, object]] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 14/30 个独立信号日"],
        next_actions=["冷启动样本 14/30。"],
        latest_iso="2026-06-17",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: [
            NotifyResult("serverchan", True, "HTTP 200")
        ],
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=None,
        print_fn=lambda *_args: None,
        gate_state_path="data/gate_notify_state.json",
        mark_gate_notification_sent_fn=lambda **kwargs: marked.append(kwargs),
    )

    assert marked == [
        {
            "gate_reasons": ["冷启动未满: 14/30 个独立信号日"],
            "state_path": "data/gate_notify_state.json",
            "run_date": "2026-06-17",
        }
    ]


def test_finalize_scheduled_notification_marks_gate_failed_when_dispatch_raises(
    monkeypatch,
) -> None:
    failed: list[dict[str, object]] = []
    seen: list[str] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 14/30 个独立信号日"],
        next_actions=["冷启动样本 14/30。"],
        latest_iso="2026-06-17",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("network down")
        ),
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=None,
        print_fn=seen.append,
        gate_state_path="data/gate_notify_state.json",
        mark_gate_notification_failed_fn=lambda **kwargs: failed.append(kwargs),
    )

    assert failed == [
        {
            "gate_reasons": ["冷启动未满: 14/30 个独立信号日"],
            "state_path": "data/gate_notify_state.json",
            "run_date": "2026-06-17",
        }
    ]
    assert "gate notify failed: network down" in seen


def test_finalize_scheduled_notification_marks_legacy_gate_after_success(
    monkeypatch,
) -> None:
    marked: list[dict[str, object]] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 14/30 个独立信号日"],
        next_actions=["冷启动样本 14/30。"],
        latest_iso="2026-06-17",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: [],
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=lambda _markdown: [
            NotifyResult("serverchan", True, "HTTP 200")
        ],
        print_fn=lambda *_args: None,
        gate_state_path="data/gate_notify_state.json",
        mark_gate_notification_sent_fn=lambda **kwargs: marked.append(kwargs),
    )

    assert marked == [
        {
            "gate_reasons": ["冷启动未满: 14/30 个独立信号日"],
            "state_path": "data/gate_notify_state.json",
            "run_date": "2026-06-17",
        }
    ]


def test_finalize_scheduled_notification_reserves_gate_before_failure(
    monkeypatch,
) -> None:
    marked: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")

    finalize_scheduled_notification(
        markdown="# 原始报告",
        args_notify=True,
        gate_ok=False,
        gate_reasons=["冷启动未满: 14/30 个独立信号日"],
        next_actions=["冷启动样本 14/30。"],
        latest_iso="2026-06-17",
        notify_mode="summary",
        dispatch_gate_notification_fn=lambda **_kwargs: [
            NotifyResult("serverchan", False, "HTTP 500")
        ],
        should_send_gate_notification_fn=lambda **_kwargs: True,
        format_notification_gate_block_fn=lambda reasons, actions: (
            f"BLOCK:{reasons[0]}|{actions[0]}\n"
        ),
        legacy_notify_fn=None,
        print_fn=lambda *_args: None,
        gate_state_path="data/gate_notify_state.json",
        mark_gate_notification_sent_fn=lambda **kwargs: marked.append(kwargs),
        mark_gate_notification_failed_fn=lambda **kwargs: failed.append(kwargs),
    )

    assert marked == []
    assert failed == [
        {
            "gate_reasons": ["冷启动未满: 14/30 个独立信号日"],
            "state_path": "data/gate_notify_state.json",
            "run_date": "2026-06-17",
        }
    ]


def test_gate_notification_allowed_only_for_main_tasks(monkeypatch) -> None:
    monkeypatch.delenv("AQSP_RUN_TASK_ID", raising=False)
    assert gate_notification_allowed() is False
    assert gate_notification_allowed("") is False
    assert gate_notification_allowed("daily") is True
    assert gate_notification_allowed("manual") is True
    assert gate_notification_allowed("scheduled") is True
    assert gate_notification_allowed("intraday") is False
    assert gate_notification_allowed("midday") is False


def test_finalize_scheduled_outputs_writes_report_and_csv(tmp_path: Path) -> None:
    report_path = tmp_path / "latest.md"
    csv_path = tmp_path / "latest.csv"
    printed: list[str] = []

    finalize_scheduled_outputs(
        markdown="# 报告",
        report_path=str(report_path),
        output_csv_path=str(csv_path),
        table=pd.DataFrame([{"symbol": "600519", "score": 71.0}]),
        print_fn=printed.append,
    )

    assert report_path.read_text(encoding="utf-8") == "# 报告"
    assert "600519" in csv_path.read_text(encoding="utf-8")
    assert printed == ["# 报告"]


def test_dispatch_scheduled_daily_notification_builds_summary_and_full() -> None:
    built: list[tuple[str, str]] = []
    dispatched: list[tuple[str, str, str]] = []

    dispatch_scheduled_daily_notification(
        notify_enabled=True,
        notify_mode="fanout",
        latest_iso="2026-06-15",
        tradable=[],
        picks=[],
        portfolio_summary=None,
        debate_results=[],
        actual_source="eastmoney",
        source_health_label="healthy",
        source_health_message="ok",
        requested_source="auto",
        cold_start_days=30,
        cold_start_min_days=30,
        is_cold_start=False,
        circuit_breaker_reason="",
        snapshot_diff=None,
        validation_summary={"checked": 1, "skipped_not_executable": 2},
        title_label="收盘研究日报",
        build_daily_run_notification_fn=lambda **kwargs: (
            built.append(
                (
                    kwargs["mode"],
                    kwargs["run_date"],
                    kwargs["validation_summary"],
                )
            )
            or f"{kwargs['mode']}-{kwargs['run_date']}"
        ),
        dispatch_notification_fn=lambda markdown, **kwargs: (
            dispatched.append(
                (
                    markdown,
                    kwargs["mode"],
                    kwargs["summary_markdown"],
                    kwargs["kind"],
                )
            )
            or []
        ),
        notification_kind="daily:2026-06-15",
    )

    assert built == [
        ("fanout", "2026-06-15", {"checked": 1, "skipped_not_executable": 2}),
        ("summary", "2026-06-15", {"checked": 1, "skipped_not_executable": 2}),
    ]
    assert dispatched == [
        (
            "fanout-2026-06-15",
            "fanout",
            "summary-2026-06-15",
            "daily:2026-06-15",
        )
    ]


def test_dispatch_notification_once_dedupes_same_kind_and_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aqsp import notification_runtime as runtime

    sent: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        runtime,
        "dispatch_notification",
        lambda markdown, **kwargs: (
            sent.append((markdown, kwargs["mode"], kwargs.get("summary_markdown")))
            or [NotifyResult("serverchan", True, "HTTP 200")]
        ),
    )

    first = dispatch_notification_once(
        "# 消息面雷达-2026-06-17\n\n## 结论\n- 无新增风险",
        mode="summary",
        prefix="news notify",
        kind="news-catalysts:2026-06-17",
        state_path=tmp_path / "notify_state.json",
        summary_markdown="summary",
    )
    second = dispatch_notification_once(
        "# 消息面雷达-2026-06-17\n\n## 结论\n- 无新增风险",
        mode="summary",
        prefix="news notify",
        kind="news-catalysts:2026-06-17",
        state_path=tmp_path / "notify_state.json",
        summary_markdown="summary",
    )

    assert len(first) == 1
    assert first[0].ok is True
    assert second == []
    assert sent == [
        (
            "# 消息面雷达-2026-06-17\n\n## 结论\n- 无新增风险",
            "summary",
            "summary",
        )
    ]


def test_dispatch_notification_once_dedupes_same_kind_when_content_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aqsp import notification_runtime as runtime

    sent: list[str] = []
    monkeypatch.setattr(
        runtime,
        "dispatch_notification",
        lambda markdown, **_kwargs: (
            sent.append(markdown) or [NotifyResult("serverchan", True, "HTTP 200")]
        ),
    )

    first = dispatch_notification_once(
        "# 收盘研究日报-2026-06-17\n\n## 结论\n- 候选 3 只",
        mode="summary",
        prefix="daily notify",
        kind="daily:2026-06-17",
        state_path=tmp_path / "notify_state.json",
        summary_markdown="# 摘要\n- 候选 3 只",
    )
    second = dispatch_notification_once(
        "# 收盘研究日报-2026-06-17\n\n## 结论\n- 候选 4 只",
        mode="summary",
        prefix="daily notify",
        kind="daily:2026-06-17",
        state_path=tmp_path / "notify_state.json",
        summary_markdown="# 摘要\n- 候选 4 只",
    )

    assert len(first) == 1
    assert second == []
    assert sent == ["# 收盘研究日报-2026-06-17\n\n## 结论\n- 候选 3 只"]
    state = json.loads((tmp_path / "notify_state.json").read_text(encoding="utf-8"))
    entry = state["sent"]["daily:2026-06-17"]
    assert entry["fingerprint"] == "daily:2026-06-17"
    assert len(entry["content_hash"]) == 64


def test_dispatch_notification_once_allows_distinct_notification_kind(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aqsp import notification_runtime as runtime

    sent: list[str] = []
    monkeypatch.setattr(
        runtime,
        "dispatch_notification",
        lambda markdown, **_kwargs: (
            sent.append(markdown) or [NotifyResult("serverchan", True, "HTTP 200")]
        ),
    )

    dispatch_notification_once(
        "# 收盘研究日报-2026-06-17",
        mode="summary",
        prefix="daily notify",
        kind="daily:2026-06-17",
        state_path=tmp_path / "notify_state.json",
    )
    second = dispatch_notification_once(
        "# 收盘研究日报-2026-06-18",
        mode="summary",
        prefix="daily notify",
        kind="daily:2026-06-18",
        state_path=tmp_path / "notify_state.json",
    )

    assert len(second) == 1
    assert sent == [
        "# 收盘研究日报-2026-06-17",
        "# 收盘研究日报-2026-06-18",
    ]


def test_dispatch_notification_once_retries_after_failed_delivery_cooldown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aqsp import notification_runtime as runtime

    attempts: list[str] = []

    def fake_dispatch(markdown, **_kwargs):
        attempts.append(markdown)
        return [NotifyResult("serverchan", False, "HTTP 500")]

    monkeypatch.setattr(runtime, "dispatch_notification", fake_dispatch)

    first = dispatch_notification_once(
        "# 消息面雷达-2026-06-17\n\n## 结论\n- 无新增风险",
        mode="summary",
        prefix="news notify",
        kind="news-catalysts:2026-06-17",
        state_path=tmp_path / "notify_state.json",
        summary_markdown="summary",
    )
    second = dispatch_notification_once(
        "# 消息面雷达-2026-06-17\n\n## 结论\n- 无新增风险",
        mode="summary",
        prefix="news notify",
        kind="news-catalysts:2026-06-17",
        state_path=tmp_path / "notify_state.json",
        summary_markdown="summary",
    )
    state_path = tmp_path / "notify_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["failed"]["news-catalysts:2026-06-17"]["updated_at"] = (
        "2026-06-17T09:00:00+08:00"
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    third = dispatch_notification_once(
        "# 消息面雷达-2026-06-17\n\n## 结论\n- 无新增风险",
        mode="summary",
        prefix="news notify",
        kind="news-catalysts:2026-06-17",
        state_path=state_path,
        summary_markdown="summary",
    )

    assert first[0].ok is False
    assert second == []
    assert third[0].ok is False
    assert len(attempts) == 2
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert (
        state["failed"]["news-catalysts:2026-06-17"]["fingerprint"]
        == "news-catalysts:2026-06-17"
    )
    assert len(state["failed"]["news-catalysts:2026-06-17"]["content_hash"]) == 64


def test_dispatch_notification_once_marks_failed_when_dispatch_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from aqsp import notification_runtime as runtime

    monkeypatch.setattr(
        runtime,
        "dispatch_notification",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    try:
        dispatch_notification_once(
            "# 收盘总览-2026-06-17",
            mode="summary",
            prefix="daily notify",
            kind="daily:2026-06-17",
            state_path=tmp_path / "notify_state.json",
        )
    except RuntimeError:
        pass

    second = dispatch_notification_once(
        "# 收盘总览-2026-06-17",
        mode="summary",
        prefix="daily notify",
        kind="daily:2026-06-17",
        state_path=tmp_path / "notify_state.json",
    )

    assert second == []
    state = json.loads((tmp_path / "notify_state.json").read_text(encoding="utf-8"))
    assert state["failed"]["daily:2026-06-17"]["fingerprint"] == "daily:2026-06-17"
