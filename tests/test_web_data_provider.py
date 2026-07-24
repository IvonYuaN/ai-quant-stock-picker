from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from aqsp.ledger.base import read_ledger
from aqsp.paper import sync_paper_trades
from aqsp.web.data_provider import (
    DashboardDateOverview,
    DashboardDataProvider,
    DashboardDebateAgentView,
    DashboardDebateSummary,
    DashboardHomeStatus,
    DashboardRuntimeTaskRun,
    DashboardSameDayTaskRow,
    build_debate_conclusion,
)
from aqsp.web.live_candidate_view import (
    LiveArtifactMetadata,
    LiveCandidateViewConfig,
    build_live_candidate_view,
)


def test_dashboard_research_recommendation_remains_actionable_during_protection(
    tmp_path: Path,
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )
    row = {
        "symbol": "000001",
        "rating": "buy_candidate",
        "research_recommendation": True,
        "observation_only": True,
        "paper_review_eligible": False,
        "portfolio_action": "observation_only",
        "candidate_status": "组合保护观察",
    }

    assert provider._is_actionable(row, task_id="intraday") is True
    assert provider._is_watch_candidate(row, task_id="intraday") is False
    assert provider._action_label(row) == "实时推荐"


def test_dashboard_data_provider_home_status_distinguishes_live_watch_and_blocked(
    monkeypatch, tmp_path: Path
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )
    overview = DashboardDateOverview(
        signal_date="2026-07-13",
        task_count=1,
        actionable_total=2,
        watch_total=1,
        blocked_total=0,
        top_task_label="盘中观察",
        top_headline="实时候选已落盘",
        blocker_headline="",
        focus_headline="先看实时候选",
        workflow_summary="",
        archive_summary="",
    )
    runtime = SimpleNamespace(
        effective_source="sina",
        requested_source="online_first",
        data_latest_trade_date="2026-07-13",
        lag_days="0",
        cooldown_until="",
        gate_blocker_line="",
    )
    monkeypatch.setattr(provider, "runtime_overview", lambda _date: runtime)

    status = provider.home_status("2026-07-13", overview=overview)

    assert isinstance(status, DashboardHomeStatus)
    assert status.label == "实时推荐"
    assert status.tone == "focus"
    assert status.actionable_count == 2
    assert "数据日 2026-07-13" in status.detail

    blocked_runtime = SimpleNamespace(
        effective_source="sina",
        requested_source="online_first",
        data_latest_trade_date="2026-07-13",
        lag_days="0",
        cooldown_until="2026-07-15",
        gate_blocker_line="双门 gate: PBO 未过门",
    )
    monkeypatch.setattr(provider, "runtime_overview", lambda _date: blocked_runtime)
    blocked = provider.home_status("2026-07-13", overview=overview)

    assert blocked.label == "阻塞"
    assert blocked.tone == "blocked"


def test_dashboard_data_provider_ignores_cooldown_on_release_date(tmp_path: Path) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )

    assert provider._active_cooldown_until(
        {"cooldown_until": "2026-07-19"}, evaluated_date="2026-07-18"
    ) == "2026-07-19"
    assert provider._active_cooldown_until(
        {"cooldown_until": "2026-07-19"}, evaluated_date="2026-07-19"
    ) == ""


def test_dashboard_data_provider_does_not_reuse_prior_runtime_event_for_selected_day(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "symbol": "__RUN__",
                "signal_date": "2026-07-18",
                "status": "blocked_by_circuit_breaker",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(ledger_path=str(ledger_path))

    assert provider._latest_run_event("2026-07-19") is None


def test_dashboard_data_provider_handles_display_override_without_run_count(
    monkeypatch, tmp_path: Path
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )
    monkeypatch.setenv("AQSP_RESEARCH_DISPLAY_OVERRIDE", "1")
    monkeypatch.setattr(provider, "_read_runtime_risk_state", lambda: {})

    overview = provider.runtime_overview("2026-07-19")

    assert overview.cooldown_until == ""


def test_dashboard_data_provider_reads_real_runtime_files(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "aqsp.web.data_provider.today_shanghai", lambda: date(2026, 6, 6)
    )

    ledger_path = tmp_path / "ledger.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T15:00:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 71,
            "rating": "buy_candidate",
            "status": "pending",
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "fallback",
            "run_source_health_message": "fallback 到 eastmoney",
            "run_data_latest_trade_date": "2026-06-05",
            "run_data_lag_days": 0,
        },
        {
            "signal_date": "2026-06-04",
            "created_at": "2026-06-04T15:00:00+08:00",
            "symbol": "000001",
            "name": "平安银行",
            "score": 62,
            "rating": "watch",
            "status": "validated",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in ledger_rows) + "\n",
        encoding="utf-8",
    )

    paper_rows = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "status": "open",
            "entry_date": "2026-06-06",
            "entry_price": 1500.0,
            "stop_loss": 1450.0,
            "take_profit": 1600.0,
            "horizon_days": 3,
        },
        {
            "symbol": "000001",
            "name": "平安银行",
            "status": "pending_entry",
            "signal_date": "2026-06-05",
        },
        {
            "symbol": "300750",
            "name": "宁德时代",
            "status": "not_executable",
            "signal_date": "2026-06-05",
            "not_executable_reason": "limit_up_at_open",
        },
        {
            "symbol": "000858",
            "name": "五粮液",
            "status": "closed",
            "signal_date": "2026-06-02",
            "exit_date": "2026-06-05",
            "return_pct": 3.2,
        },
    ]
    paper_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in paper_rows) + "\n",
        encoding="utf-8",
    )

    (logs_path / "2026-06-06.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "execution",
                        "timestamp": "2026-06-06T09:35:00+08:00",
                        "symbol": "600519",
                        "action": "BUY",
                        "shares": 100,
                        "price": 1500.0,
                        "cost": 150000.0,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "decision",
                        "timestamp": "2026-06-06T09:30:00+08:00",
                        "symbol": "600519",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    summary = provider.summarize()
    assert summary.signal_count == 2
    assert summary.latest_signal_date == "2026-06-05"
    assert summary.open_positions == 1
    assert summary.pending_entries == 1
    assert summary.not_executable == 1
    assert summary.closed_trades == 1
    assert summary.execution_logs == 1

    latest_signals = provider.latest_signal_frame(limit=10)
    assert list(latest_signals["代码"]) == ["600519"]
    assert latest_signals.iloc[0]["健康度"] == "fallback"

    open_positions = provider.open_positions_frame()
    assert list(open_positions["代码"]) == ["600519"]
    assert provider.open_positions_frame(signal_date="2026-06-05").empty

    paper_events = provider.paper_events_frame(limit=10)
    assert "状态" in paper_events.columns
    assert "open" in set(paper_events["状态"])
    assert "not_executable" in set(paper_events["状态"])

    executions = provider.recent_execution_frame(limit=10)
    assert list(executions["代码"]) == ["600519"]
    assert list(executions["动作"]) == ["纸面入场"]

    source_status = provider.latest_source_status()
    assert source_status["actual_source"] == "eastmoney"
    assert source_status["health_label"] == "fallback"

    historical_source_status = provider.latest_source_status(signal_date="2026-06-04")
    assert historical_source_status["requested_source"] == "未记录"
    assert historical_source_status["actual_source"] == "未记录"
    assert historical_source_status["health_label"] == "历史记录缺字段"
    assert historical_source_status["data_latest_trade_date"] == "未记录"

    task_snapshots = provider.task_snapshots()
    assert task_snapshots[0].task_id == "main_chain"
    assert task_snapshots[0].status_label == "有推荐"
    assert task_snapshots[-1].task_id == "briefing"
    assert task_snapshots[-1].status_label == "未产出"

    paper_summary = provider.paper_summary()
    assert paper_summary.signal_date == ""
    assert paper_summary.open_positions == 1
    assert paper_summary.pending_entries == 1
    assert paper_summary.not_executable == 1
    assert paper_summary.closed_trades == 1
    assert any("贵州茅台" in line for line in paper_summary.open_position_lines)
    assert any("纸面入场假设 1 笔" in line for line in paper_summary.event_lines)
    assert not any("next open" in line for line in paper_summary.event_lines)
    assert any("下一交易日开盘价" in line for line in paper_summary.event_lines)
    assert any("不可成交 1 笔" in line for line in paper_summary.event_lines)
    assert any(
        "最近纸面关闭: 000858 五粮液" in line for line in paper_summary.event_lines
    )
    assert any(
        "最近纸面回写: 600519 贵州茅台 | 纸面入场 100 @ 1500.0" in line
        for line in paper_summary.action_summary_lines
    )
    assert not any("BUY" in line for line in paper_summary.action_summary_lines)
    assert any(
        "纸面入场待核对 1 笔" in line for line in paper_summary.action_summary_lines
    )
    assert not any("next open" in line for line in paper_summary.action_summary_lines)
    assert any(
        "下一交易日开盘价" in line for line in paper_summary.action_summary_lines
    )
    assert any("阻塞队列 1 笔" in line for line in paper_summary.action_summary_lines)

    scoped_paper_summary = provider.paper_summary("2026-06-05")
    assert scoped_paper_summary.signal_date == "2026-06-05"
    assert scoped_paper_summary.open_positions == 1
    assert scoped_paper_summary.pending_entries == 1
    assert scoped_paper_summary.not_executable == 1
    assert scoped_paper_summary.closed_trades == 0
    assert any(
        "2026-06-05 暂无待执行" not in line for line in scoped_paper_summary.event_lines
    )

    open_focus = provider.execution_focus(
        signal_date="2026-06-05",
        symbol="600519",
    )
    assert open_focus.display_name == "600519 贵州茅台"
    assert open_focus.research_status == "已落盘"
    assert open_focus.execution_status == "尚未进入执行"
    assert open_focus.holding_status == "纸面持有未绑定本日"
    assert any(
        "研究已产出，但尚未进入纸面入场或阻塞队列。" in line
        for line in open_focus.readiness_lines
    )
    assert any("未绑定 2026-06-05 信号日" in line for line in open_focus.holding_lines)

    pending_focus = provider.execution_focus(
        signal_date="2026-06-05",
        symbol="000001",
    )
    assert pending_focus.execution_status == "等待开盘验证"
    assert pending_focus.holding_status == "尚未形成纸面持有"
    assert any("纸面入场假设 1 笔" in line for line in pending_focus.readiness_lines)

    blocked_focus = provider.execution_focus(
        signal_date="2026-06-05",
        symbol="300750",
    )
    assert blocked_focus.execution_status == "可成交性受阻"
    assert any("limit_up_at_open" in line for line in blocked_focus.readiness_lines)


def test_dashboard_data_provider_summarizes_latest_bt_task_log_segment(
    tmp_path: Path,
) -> None:
    bt_logs_dir = tmp_path / "logs" / "bt"
    bt_logs_dir.mkdir(parents=True)
    (bt_logs_dir / "bt-coldstart-2026-07-07.log").write_text(
        "\n".join(
            (
                "[2026-07-07 11:52:34] 开始同步代码: origin/main",
                "数据错误: 数据源 sqlite_db 日线获取不完整",
                "[2026-07-07 12:21:29] 开始同步代码: origin/main",
                "[2026-07-07 12:21:29] 开始运行: /opt/aqsp/scripts/coldstart_daily.sh",
                "🛡️ 组合保护已触发，停止新增候选生成: 组合保护冷却期中，至 2026-07-12 解除",
                "[2026-07-07 12:34:56] 冷启动筛选被组合保护正常阻塞；历史库已更新，本次不追加新增候选",
                "冷启动: 34/30",
                "[2026-07-07 12:34:57] 冷启动日跑完成",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "predictions.jsonl"),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(bt_logs_dir),
    )
    (tmp_path / "risk_state.json").write_text(
        json.dumps({"cooldown_until": "2026-07-12"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "gate_notify_state.json").write_text(
        json.dumps(
            {
                "sent_by_date": {
                    "2026-06-21": {
                        "fingerprint": "dsr|pbo",
                        "status": "suppressed",
                    },
                    "2026-07-07": {
                        "fingerprint": "组合保护冷却期中，至 2026-07-12 解除",
                        "status": "suppressed",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "walkforward_production_status.json").write_text(
        json.dumps(
            {
                "status": "timeout",
                "updated_at": "2026-06-30T13:56:09+08:00",
                "effective_symbols": 5209,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "walkforward_gate.json").write_text(
        json.dumps(
            {
                "both_pass": False,
                "data_end": "2024-12-31",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runs = provider.runtime_task_runs("2026-07-07")
    assert len(runs) == 1
    coldstart_run = runs[0]
    assert coldstart_run.action == "coldstart"
    assert coldstart_run.status_label == "风控阻塞"
    assert (
        coldstart_run.headline
        == "冷启动筛选被组合保护正常阻塞；历史库已更新，本次不追加新增候选"
    )
    assert "冷启动: 34/30" in coldstart_run.detail_lines
    assert not any("数据错误" in line for line in coldstart_run.detail_lines)
    overview = provider.runtime_overview("2026-07-07")
    assert overview.task_id == "coldstart"
    assert overview.run_status == "风控阻塞"
    assert overview.coldstart_progress == "34/30"
    assert overview.cooldown_until == "2026-07-12"
    assert overview.gate_blocker_line == "双门 gate: DSR 未过门 / PBO 未过门"
    assert overview.market_context_runtime_line.startswith("跨市规则: ")
    assert "确定性上下文优先级增强" in overview.market_context_runtime_line
    assert "RSS源:" in overview.market_context_runtime_line
    assert "覆盖 " in overview.market_context_runtime_line
    assert (
        overview.walkforward_runtime_line
        == "生产 gate: 生产回测 超时 / 更新 2026-06-30 / 覆盖 5209 标的 / 沿用既有 gate sidecar: DSR/PBO 未过门 / gate 数据至 2024-12-31 / 后续需重跑生产 walk-forward"
    )


def test_dashboard_data_provider_runtime_overview_backfills_coldstart_progress_from_ledger_when_log_missing(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    rows = [
        {
            "signal_date": f"2026-06-{day:02d}",
            "created_at": f"2026-06-{day:02d}T15:00:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 70,
            "rating": "watch",
        }
        for day in range(1, 31)
    ]
    rows.append(
        {
            "symbol": "__RUN__",
            "name": "run_event",
            "status": "blocked_by_circuit_breaker",
            "event_type": "blocked_by_circuit_breaker",
            "signal_date": "2026-07-08",
            "reason": "组合保护冷却期中，至 2026-07-12 解除",
            "run_task_id": "daily",
            "run_actual_source": "sqlite_db",
            "run_circuit_breaker_triggered": True,
            "run_circuit_breaker_reason": "组合保护冷却期中，至 2026-07-12 解除",
        }
    )
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "risk_state.json").write_text(
        json.dumps({"cooldown_until": "2026-07-12"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "gate_notify_state.json").write_text(
        json.dumps(
            {
                "sent_by_date": {
                    "2026-06-21": {
                        "fingerprint": "dsr|pbo",
                        "status": "suppressed",
                    },
                    "2026-07-08": {
                        "fingerprint": "组合保护冷却期中，至 2026-07-12 解除",
                        "status": "suppressed",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(tmp_path / "logs" / "bt"),
    )

    overview = provider.runtime_overview("2026-07-08")

    assert overview.coldstart_progress == "30/30"
    assert overview.gate_blocker_line == "双门 gate: DSR 未过门 / PBO 未过门"
    assert overview.conclusion == "冷启动样本已达标；候选研究继续，组合保护单独展示"


def test_dashboard_data_provider_runtime_overview_uses_coldstart_handoff_state(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text("", encoding="utf-8")
    (tmp_path / "risk_state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "coldstart_handoff_status.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "progress": "34/30",
                "updated_at": "2026-07-09T19:40:00+08:00",
                "next_step": "run_production_walkforward_gate",
                "next_command": "bash scripts/bt_task.sh walkforward-gate",
                "blocker": "冷启动样本门已达标；下一步是生产 walk-forward 双门 gate（DSR/PBO）与组合保护冷却复核",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(tmp_path / "logs" / "bt"),
    )

    overview = provider.runtime_overview("2026-07-09")

    assert overview.coldstart_progress == "34/30"
    assert overview.conclusion == "冷启动样本已达标；候选研究不再等待组合保护"
    assert overview.coldstart_handoff_line.startswith("冷启动交接: 样本门已达标")
    assert "run_production_walkforward_gate" in overview.coldstart_handoff_line
    assert "bt_task.sh walkforward-gate" in overview.coldstart_handoff_line


def test_dashboard_data_provider_walkforward_line_explains_resource_blocker() -> None:
    from aqsp.web.data_provider import DashboardDataProvider

    line = DashboardDataProvider._walkforward_runtime_line_from_state(
        {
            "status": "blocked_resources",
            "updated_at": "2026-07-09T12:20:00+08:00",
            "detail": "server memory 1.6GiB < required 4.0GiB",
        },
        {
            "both_pass": False,
            "data_end": "2024-12-31",
        },
    )

    assert line == (
        "生产 gate: 生产回测 资源不足阻塞 / 更新 2026-07-09 / "
        "沿用既有 gate sidecar: DSR/PBO 未过门 / gate 数据至 2024-12-31 / "
        "后续换更大机器或显式放行后再跑"
    )


def test_dashboard_data_provider_walkforward_line_marks_active_guard_as_existing_sidecar() -> (
    None
):
    from aqsp.web.data_provider import DashboardDataProvider

    line = DashboardDataProvider._walkforward_runtime_line_from_state(
        {
            "status": "blocked_running",
            "updated_at": "2026-07-09T12:30:00+08:00",
            "child_pid": 22222,
        },
        {
            "both_pass": False,
            "data_end": "2024-12-31",
        },
    )

    assert line == (
        "生产 gate: 生产回测 已有生产回测运行中 / 更新 2026-07-09 / "
        "沿用既有 gate sidecar: DSR/PBO 未过门 / gate 数据至 2024-12-31 / "
        "后续需重跑生产 walk-forward"
    )


def test_dashboard_data_provider_intraday_runtime_line_marks_failed_refresh() -> None:
    from aqsp.web.data_provider import DashboardDataProvider

    line = DashboardDataProvider._intraday_runtime_line_from_state(
        {
            "status": "failed",
            "source": "eastmoney",
            "max_universe": 300,
            "updated_at": "2026-07-09T14:51:00+08:00",
            "reason": "盘中选股失败，保留上一版盘中产物",
            "candidate_count": 3,
            "actionable_count": 1,
            "paper_review_count": 1,
            "focus_count": 2,
            "watch_count": 2,
            "blocked_count": 1,
        }
    )

    assert line == (
        "盘中刷新: 失败保留上一版 / 源 eastmoney / 候选池 300 / "
        "输出 3 / 强候选 2 / 纸面复核 1 / 观察 2 / 阻塞 1 / 更新 2026-07-09T14:51 / "
        "盘中选股失败，保留上一版盘中产物"
    )


def test_dashboard_data_provider_intraday_runtime_line_marks_protection_observation() -> (
    None
):
    from aqsp.web.data_provider import DashboardDataProvider

    line = DashboardDataProvider._intraday_runtime_line_from_state(
        {
            "status": "completed",
            "source": "online_first",
            "max_universe": 3,
            "updated_at": "2026-07-10T12:46:42+08:00",
            "reason": "盘中刷新完成；组合保护生效，仅保留观察展示",
            "candidate_count": 3,
            "actionable_count": 0,
            "paper_review_count": 0,
            "focus_count": 0,
            "watch_count": 3,
            "blocked_count": 0,
            "protection_blocked": True,
        }
    )

    assert line == (
        "盘中刷新: 已刷新 / 源 online_first / 候选池 3 / "
        "输出 3 / 强候选 0 / 纸面复核 0 / 观察 3 / 阻塞 0 / 组合保护 / "
        "更新 2026-07-10T12:46 / 盘中刷新完成；组合保护生效，仅保留观察展示"
    )


def test_dashboard_data_provider_intraday_runtime_line_exposes_fetch_telemetry() -> None:
    from aqsp.web.data_provider import DashboardDataProvider

    line = DashboardDataProvider._intraday_runtime_line_from_state(
        {
            "status": "partial_failed",
            "source": "online_first",
            "updated_at": "2026-07-17T14:57:30+08:00",
            "reason": "消息面已处理",
            "execution": {
                "catalyst_fetch_mode": "thread",
                "duration_seconds": 451,
                "timed_out": True,
            },
        }
    )

    assert "消息模式 thread" in line
    assert "耗时 451s" in line
    assert "消息/盘中超时" in line


def test_dashboard_data_provider_runtime_fallback_digest_uses_run_event_without_task_pollution(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "symbol": "__RUN__",
                "name": "run_event",
                "status": "blocked_by_circuit_breaker",
                "event_type": "blocked_by_circuit_breaker",
                "signal_date": "2026-07-07",
                "reason": "组合保护冷却期中，至 2026-07-12 解除",
                "run_task_id": "coldstart",
                "run_actual_source": "sqlite_db",
                "run_data_latest_trade_date": "2026-07-07",
                "run_data_lag_days": 0,
                "run_fetched_frame_count": 3000,
                "run_screened_count": 0,
                "run_final_count": 0,
                "run_circuit_breaker_triggered": True,
                "run_circuit_breaker_reason": "组合保护冷却期中，至 2026-07-12 解除",
                "daily_pnl_pct": -11.4576,
                "monthly_pnl_pct": -19.5162,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(tmp_path / "logs" / "bt"),
    )

    assert provider.same_day_task_rows("2026-07-07") == ()
    assert provider.runtime_fallback_digest_lines("2026-07-07") == (
        "结论: 组合保护生效，候选研究等待新鲜数据",
        "运行状态: 任务 coldstart / 日期 2026-07-07",
        "数据: 当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid） / 数据日 2026-07-07 / 延迟 0 天",
        "风险/阻塞: 组合保护冷却期中，至 2026-07-12 解除",
        "风控读数: 日 -11.46% / 月 -19.52%",
    )
    source_status = provider.latest_source_status(
        task_id="main_chain",
        signal_date="2026-07-07",
    )
    assert source_status["actual_source"] == "sqlite_db"
    assert source_status["data_latest_trade_date"] == "2026-07-07"
    assert source_status["lag_days"] == "0"
    assert provider.latest_signal_frame(
        task_id="main_chain",
        signal_date="2026-07-07",
    ).empty
    briefing_view = provider.build_task_view("briefing", signal_date="2026-07-07")
    assert briefing_view.source_status["actual_source"] == "sqlite_db"
    (tmp_path / "risk_state.json").write_text(
        json.dumps({"cooldown_until": "2026-07-12"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "gate_notify_state.json").write_text(
        json.dumps(
            {
                "sent_by_date": {
                    "2026-07-07": {
                        "fingerprint": "dsr|pbo",
                        "status": "suppressed",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    overview = provider.runtime_overview("2026-07-07")
    assert overview.conclusion == "组合保护生效，候选研究等待新鲜数据"
    assert overview.task_id == "coldstart"
    assert overview.effective_source == "sqlite_db"
    assert (
        overview.source_reason
        == "当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid）"
    )
    assert overview.risk_reason == "组合保护冷却期中，至 2026-07-12 解除"
    assert overview.cooldown_until == "2026-07-12"
    assert overview.gate_blocker_line == "双门 gate: DSR 未过门 / PBO 未过门"


def test_dashboard_data_provider_sorts_runtime_runs_by_mtime_and_ignores_inner_skip(
    tmp_path: Path,
) -> None:
    bt_logs_dir = tmp_path / "logs" / "bt"
    bt_logs_dir.mkdir(parents=True)
    coldstart_log = bt_logs_dir / "bt-coldstart-2026-07-08.log"
    walkforward_log = bt_logs_dir / "bt-walkforward-gate-2026-07-08.log"
    monitor_log = bt_logs_dir / "bt-monitor-2026-07-08.log"
    coldstart_log.write_text(
        "\n".join(
            (
                "[2026-07-08 19:40:00] 开始运行: /opt/aqsp/scripts/coldstart_daily.sh",
                "[2026-07-08 19:40:01] 目标日 2026-07-07 已有 5203 个标的，跳过重复历史库更新",
                "冷启动: 35/30",
                "[2026-07-08 19:40:02] 冷启动日跑完成",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monitor_log.write_text(
        "[2026-07-08 23:45:00] 主链路仍在运行，本次任务正常跳过；这是互斥保护，不是失败\n",
        encoding="utf-8",
    )
    walkforward_log.write_text(
        "[2026-07-08 20:10:00] 生产 walk-forward gate 完成\n",
        encoding="utf-8",
    )
    os.utime(coldstart_log, (100.0, 100.0))
    os.utime(monitor_log, (200.0, 200.0))
    os.utime(walkforward_log, (300.0, 300.0))

    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "predictions.jsonl"),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(bt_logs_dir),
    )

    runs = provider.runtime_task_runs("2026-07-08")
    assert [run.action for run in runs] == [
        "walkforward-gate",
        "monitor",
        "coldstart",
    ]
    assert runs[0].task_label == "生产回测 gate"
    assert runs[1].status_label == "正常跳过"
    assert runs[2].status_label == "完成"
    assert runs[2].headline == "冷启动日跑完成"


def test_dashboard_data_provider_runtime_task_runs_limits_before_parsing(
    tmp_path: Path,
) -> None:
    bt_logs_dir = tmp_path / "logs" / "bt"
    bt_logs_dir.mkdir(parents=True)
    mtimes = {
        "news": 100.0,
        "intraday": 200.0,
        "daily": 400.0,
        "coldstart": 300.0,
        "walkforward-gate": 500.0,
    }
    for action, mtime in mtimes.items():
        path = bt_logs_dir / f"bt-{action}-2026-07-08.log"
        path.write_text(
            f"[2026-07-08 15:00:00] {action} 完成\n",
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))

    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "predictions.jsonl"),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(bt_logs_dir),
    )
    parsed: list[str] = []

    def _parse(action: str, path: Path) -> DashboardRuntimeTaskRun:
        parsed.append(path.name)
        return DashboardRuntimeTaskRun(
            action=action,
            task_label=action,
            log_date="2026-07-08",
            log_mtime=f"2026-07-08T15:0{len(parsed)}:00+08:00",
            status_label="完成",
            headline=f"{action} 完成",
            detail_lines=(),
        )

    provider._parse_runtime_task_log = _parse  # type: ignore[method-assign]

    provider.runtime_task_runs("2026-07-08", limit=2)

    assert parsed == [
        "bt-walkforward-gate-2026-07-08.log",
        "bt-daily-2026-07-08.log",
    ]


def test_dashboard_data_provider_runtime_task_log_reads_recent_tail(
    tmp_path: Path,
) -> None:
    bt_logs_dir = tmp_path / "logs" / "bt"
    bt_logs_dir.mkdir(parents=True)
    log_path = bt_logs_dir / "bt-daily-2026-07-08.log"
    log_path.write_text(
        ("旧失败噪音\n" * 50000)
        + "\n".join(
            (
                "[2026-07-08 15:00:00] 开始运行: /opt/aqsp/scripts/daily_pipeline.py",
                "[2026-07-08 15:01:00] 跑批成功完成",
                "[2026-07-08 15:01:01] 退出码: 0",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "predictions.jsonl"),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(bt_logs_dir),
    )

    runs = provider.runtime_task_runs("2026-07-08", limit=1)

    assert len(runs) == 1
    assert runs[0].action == "daily"
    assert runs[0].status_label == "完成"
    assert runs[0].headline == "跑批成功完成"
    assert not any("旧失败噪音" in line for line in runs[0].detail_lines)


def test_dashboard_data_provider_marks_monitor_exit_zero_as_complete(
    tmp_path: Path,
) -> None:
    bt_logs_dir = tmp_path / "logs" / "bt"
    bt_logs_dir.mkdir(parents=True)
    (bt_logs_dir / "bt-monitor-2026-07-08.log").write_text(
        "\n".join(
            (
                "[2026-07-08 12:45:02] 开始运行: /opt/aqsp/scripts/server_monitor.sh",
                "[2026-07-08 12:45:23] 服务器监控结束",
                "[2026-07-08 12:45:23] 退出码: 0",
                "[2026-07-08 12:45:23] 日志文件: /opt/aqsp/logs/monitor/monitor-2026-07-08.log",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "predictions.jsonl"),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        logs_path=str(tmp_path / "logs" / "trades"),
        reports_dir=str(tmp_path / "reports"),
        bt_logs_dir=str(bt_logs_dir),
    )

    runs = provider.runtime_task_runs("2026-07-08")
    assert len(runs) == 1
    assert runs[0].action == "monitor"
    assert runs[0].status_label == "完成"
    assert runs[0].headline == "服务器监控结束"


def test_dashboard_data_provider_returns_debate_summary_for_symbol_and_day(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    debate_path = tmp_path / "debate_results.jsonl"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text("", encoding="utf-8")
    paper_path.write_text("", encoding="utf-8")
    debate_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "debate_id": "debate-old",
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "related_signal_date": "2026-06-05",
                        "created_at": "2026-06-05T20:00:00",
                        "original_score": 80.0,
                        "adjusted_score": 81.0,
                        "recommended_adjustment": "keep",
                        "disagreement_score": 0.25,
                        "final_consensus": "旧记录",
                        "final_vote": {"bull": "bullish"},
                        "rounds": [],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "debate_id": "debate-new",
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "related_signal_date": "2026-06-05",
                        "created_at": "2026-06-05T21:30:00",
                        "original_score": 80.0,
                        "adjusted_score": 82.0,
                        "recommended_adjustment": "raise",
                        "disagreement_score": 0.33,
                        "final_consensus": "维持主推，但控制追高节奏",
                        "research_verdict": "倾向优先纸面复核，但先卡住 追高回撤风险",
                        "primary_risk_gate": "追高回撤风险",
                        "next_trigger": "先确认次日成交质量",
                        "adjustment_reason": "多头略占优",
                        "risk_warnings": ["高位波动放大"],
                        "opportunity_highlights": ["主线延续"],
                        "viewpoint_buckets": {
                            "bullish": ["量价仍在主升段"],
                            "risk_counterevidence": ["追高回撤风险"],
                        },
                        "disagreement_points": ["是否属于板块共振"],
                        "uncertainty_points": ["次日成交质量未确认"],
                        "final_vote": {
                            "bull": "bullish",
                            "risk_control": "neutral",
                        },
                        "rounds": [
                            {
                                "round_num": 2,
                                "summary": "技术面仍强，但风控建议控制节奏。",
                                "opinions": [
                                    {
                                        "role": "bull",
                                        "final_position": "bullish",
                                        "confidence": 0.88,
                                        "arguments": ["量价仍在主升段"],
                                        "risk_factors": ["高位波动放大"],
                                        "opportunity_factors": ["主线延续"],
                                    },
                                    {
                                        "role": "risk_control",
                                        "final_position": "neutral",
                                        "confidence": 0.72,
                                        "arguments": ["先确认次日成交质量"],
                                        "risk_factors": ["追高回撤风险"],
                                        "opportunity_factors": [],
                                    },
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "debate_id": "debate-other",
                        "symbol": "002594",
                        "name": "比亚迪",
                        "related_signal_date": "2026-06-05",
                        "created_at": "2026-06-05T21:00:00",
                        "original_score": 70.0,
                        "adjusted_score": 69.0,
                        "recommended_adjustment": "lower",
                        "disagreement_score": 0.41,
                        "final_consensus": "等待确认",
                        "final_vote": {"bear": "bearish"},
                        "rounds": [],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
        debate_results_path=str(debate_path),
    )

    summary = provider.debate_summary(signal_date="2026-06-05", symbol="600519")

    assert summary is not None
    assert summary.debate_id == "debate-new"
    assert summary.recommended_adjustment_label == "辩论倾向上调"
    assert summary.research_verdict == "倾向优先纸面复核，但先卡住 追高回撤风险"
    assert summary.primary_risk_gate == "追高回撤风险"
    assert summary.next_trigger == "先确认次日成交质量"
    assert summary.created_at == "2026-06-05T21:30:00"
    assert summary.consensus == "维持主推，但控制追高节奏"
    assert summary.summary_lines[0] == (
        "讨论倾向上调: 系统原始评分 80.0；附件参考分 82.0；不改写系统评分"
    )
    assert summary.agent_views[0].role_label in {"技术多头", "风控"}
    assert summary.risk_warnings == ("高位波动放大",)
    assert summary.viewpoint_buckets["bullish"] == ("量价仍在主升段",)
    assert summary.disagreement_points == ("是否属于板块共振",)
    assert summary.uncertainty_points == ("次日成交质量未确认",)

    summaries = provider.debate_summaries("2026-06-05")
    assert [item.symbol for item in summaries] == ["600519", "002594"]
    prioritized = provider.prioritized_debate_summaries("2026-06-05")
    assert [item.symbol for item in prioritized] == ["600519", "002594"]


def test_dashboard_data_provider_debate_summary_requires_signal_date_not_debate_date(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-late",
                "symbol": "600519",
                "name": "贵州茅台",
                "debate_date": "2026-06-06",
                "created_at": "2026-06-06T21:00:00",
                "original_score": 80.0,
                "adjusted_score": 82.0,
                "recommended_adjustment": "raise",
                "final_consensus": "这是隔日辩论，不应串到 2026-06-06 信号。",
                "final_vote": {"bull": "bullish"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    assert provider.debate_summary(signal_date="2026-06-06", symbol="600519") is None
    assert provider.debate_summaries("2026-06-06") == ()


def test_dashboard_data_provider_debate_summary_drops_empty_evidence_rows(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-empty",
                "symbol": "600519",
                "name": "贵州茅台",
                "related_signal_date": "2026-06-05",
                "created_at": "2026-06-05T21:00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    assert provider.debate_summary(signal_date="2026-06-05", symbol="600519") is None
    assert provider.debate_summaries("2026-06-05") == ()


def test_dashboard_data_provider_prioritized_debate_summaries_prefer_structured_context(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "debate_id": "debate-structured",
                        "symbol": "300750",
                        "name": "宁德时代",
                        "related_signal_date": "2026-06-05",
                        "created_at": "2026-06-05T21:05:00",
                        "original_score": 82.0,
                        "adjusted_score": 82.0,
                        "recommended_adjustment": "raise",
                        "disagreement_score": 0.25,
                        "final_consensus": "分歧可控",
                        "research_verdict": "倾向优先纸面复核",
                        "primary_risk_gate": "先确认承接",
                        "next_trigger": "若放量延续则优先复核",
                        "historical_context_note": "强证据样本 4/5 命中",
                        "role_reliability_lines": ["跨市场: 近21天 7/10 (70%)"],
                        "support_points": ["海外主线仍在扩散。"],
                        "watch_items": ["观察次日承接。"],
                        "final_vote": {"cross_market": "bullish"},
                        "rounds": [
                            {
                                "round_num": 2,
                                "opinions": [
                                    {
                                        "role": "cross_market",
                                        "stance": "bullish",
                                        "confidence": 0.8,
                                        "arguments": ["海外主线继续扩散"],
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "debate_id": "debate-noisy",
                        "symbol": "600036",
                        "name": "招商银行",
                        "related_signal_date": "2026-06-05",
                        "created_at": "2026-06-05T21:10:00",
                        "original_score": 68.0,
                        "adjusted_score": 68.0,
                        "recommended_adjustment": "keep",
                        "disagreement_score": 0.48,
                        "final_consensus": "观点分化，保持原评级",
                        "final_vote": {"bear": "bearish"},
                        "rounds": [{"round_num": 2, "summary": "多空分歧更大"}],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    prioritized = provider.prioritized_debate_summaries("2026-06-05")

    assert [item.symbol for item in prioritized[:2]] == ["300750", "600036"]

    salient = provider.prioritized_debate_summaries(
        "2026-06-05",
        salient_only=True,
    )

    assert [item.symbol for item in salient] == ["300750", "600036"]


def test_dashboard_data_provider_prioritized_debate_summaries_use_created_at_as_tiebreak(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    payload = {
        "symbol": "300750",
        "name": "宁德时代",
        "related_signal_date": "2026-06-05",
        "original_score": 82.0,
        "adjusted_score": 82.0,
        "recommended_adjustment": "raise",
        "disagreement_score": 0.25,
        "final_consensus": "分歧可控",
        "research_verdict": "倾向优先纸面复核",
        "primary_risk_gate": "先确认承接",
        "next_trigger": "若放量延续则优先复核",
        "support_points": ["海外主线仍在扩散。"],
        "watch_items": ["观察次日承接。"],
        "final_vote": {"cross_market": "bullish"},
        "rounds": [
            {
                "round_num": 2,
                "opinions": [
                    {
                        "role": "cross_market",
                        "stance": "bullish",
                        "confidence": 0.8,
                        "arguments": ["海外主线继续扩散"],
                    }
                ],
            }
        ],
    }
    debate_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "debate_id": "debate-older",
                        **payload,
                        "created_at": "2026-06-05T21:05:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "debate_id": "debate-newer",
                        **payload,
                        "created_at": "2026-06-05T21:10:00",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    prioritized = provider.prioritized_debate_summaries("2026-06-05")

    assert [item.debate_id for item in prioritized] == ["debate-newer"]


def test_dashboard_data_provider_debate_summaries_use_latest_rerun_per_symbol(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    base = {
        "symbol": "603019",
        "name": "中科曙光",
        "related_signal_date": "2026-07-10",
        "task_id": "intraday",
        "original_score": 72.0,
        "adjusted_score": 72.0,
        "recommended_adjustment": "keep",
            "final_consensus": "等待确认",
            "final_vote": {"bull": "neutral", "risk_control": "neutral"},
            "opposition_points": ["冲高回落且成交量衰减则失效"],
            "rounds": [{"round_num": 1, "summary": "讨论完成"}],
    }
    debate_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        **base,
                        "debate_id": "older-run",
                        "candidate_fingerprint": "old-fingerprint",
                        "created_at": "2026-07-10T14:30:00+08:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        **base,
                        "debate_id": "latest-run",
                        "candidate_fingerprint": "new-fingerprint",
                        "created_at": "2026-07-10T21:40:00+08:00",
                    },
                    ensure_ascii=False,
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    summaries = provider.prioritized_debate_summaries("2026-07-10")

    assert {item.debate_id for item in summaries} == {"latest-run", "older-run"}


def test_dashboard_data_provider_home_debates_follow_current_candidate_order() -> None:
    def _summary(symbol: str) -> SimpleNamespace:
        return SimpleNamespace(symbol=symbol)

    ordered = DashboardDataProvider._prioritize_debates_for_candidate_symbols(
        (_summary("000938"), _summary("603893"), _summary("603019")),
        candidate_symbols=("603893", "603019", "000048"),
        limit=3,
    )

    assert [item.symbol for item in ordered] == ["603893", "603019", "000938"]


def test_dashboard_data_provider_home_debates_use_same_day_spotlights_when_task_cards_empty() -> (
    None
):
    def _summary(symbol: str) -> SimpleNamespace:
        return SimpleNamespace(symbol=symbol)

    ordered = DashboardDataProvider._prioritize_debates_for_candidate_symbols(
        (_summary("000938"), _summary("603893"), _summary("603019")),
        candidate_symbols=("603893", "603019"),
        limit=1,
    )

    assert [item.symbol for item in ordered] == ["603893"]


def test_dashboard_data_provider_prioritized_debate_summaries_hide_low_signal_noise_when_salient_only(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "debate_id": "debate-strong",
                        "symbol": "300750",
                        "name": "宁德时代",
                        "related_signal_date": "2026-06-05",
                        "created_at": "2026-06-05T21:05:00",
                        "original_score": 82.0,
                        "adjusted_score": 82.0,
                        "recommended_adjustment": "raise",
                        "disagreement_score": 0.31,
                        "final_consensus": "分歧可控",
                        "research_verdict": "倾向优先纸面复核",
                        "primary_risk_gate": "先确认承接",
                        "next_trigger": "若放量延续则优先复核",
                        "historical_context_note": "强证据样本 4/5 命中",
                        "role_reliability_lines": ["跨市场: 近21天 7/10 (70%)"],
                        "support_points": ["海外主线仍在扩散。"],
                        "watch_items": ["观察次日承接。"],
                        "final_vote": {"cross_market": "bullish"},
                        "rounds": [
                            {
                                "round_num": 2,
                                "opinions": [
                                    {
                                        "role": "cross_market",
                                        "stance": "bullish",
                                        "confidence": 0.8,
                                        "arguments": ["海外主线继续扩散"],
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "debate_id": "debate-low",
                        "symbol": "601398",
                        "name": "工商银行",
                        "related_signal_date": "2026-06-05",
                        "created_at": "2026-06-05T21:10:00",
                        "original_score": 60.0,
                        "adjusted_score": 60.0,
                        "recommended_adjustment": "keep",
                        "disagreement_score": 0.12,
                        "final_consensus": "",
                        "final_vote": {"neutral": "neutral"},
                        "rounds": [{"round_num": 1, "summary": "轻微分歧"}],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    prioritized = provider.prioritized_debate_summaries("2026-06-05")
    salient = provider.prioritized_debate_summaries(
        "2026-06-05",
        salient_only=True,
    )

    assert [item.symbol for item in prioritized] == ["300750", "601398"]
    assert [item.symbol for item in salient] == ["300750"]


def test_dashboard_data_provider_prioritized_debate_summaries_returns_empty_when_only_low_signal_and_salient_only(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-low",
                "symbol": "601398",
                "name": "工商银行",
                "related_signal_date": "2026-06-05",
                "created_at": "2026-06-05T21:10:00",
                "original_score": 60.0,
                "adjusted_score": 60.0,
                "recommended_adjustment": "keep",
                "disagreement_score": 0.12,
                "final_vote": {
                    "bull": "bullish",
                    "bear": "bearish",
                    "risk_control": "neutral",
                },
                "rounds": [{"round_num": 1, "summary": "低分歧背景讨论"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    assert provider.prioritized_debate_summaries("2026-06-05") != ()
    assert (
        provider.prioritized_debate_summaries(
            "2026-06-05",
            salient_only=True,
        )
        == ()
    )


def test_dashboard_data_provider_hides_debate_with_any_evidence_gap(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "partial-evidence",
                "symbol": "000001",
                "related_signal_date": "2026-07-16",
                "task_id": "intraday",
                "process_recorded": True,
                "conclusion_recorded": True,
                "advisory_boundary_ok": True,
                "evidence_sufficient": True,
                "debate_quality_issues": ["missing_cross_market_viewpoint"],
                "rounds": [
                    {
                        "round_num": 1,
                        "summary": "技术与风险角色已完成讨论",
                        "opinions": [
                            {
                                "role": "bull",
                                "stance": "bullish",
                                "arguments": ["均线多头"],
                            },
                            {
                                "role": "risk_control",
                                "stance": "neutral",
                                "arguments": ["等待量能"],
                            },
                        ],
                    }
                ],
                "final_vote": {"bull": "bullish", "risk_control": "neutral"},
                "final_consensus": "继续观察",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        debate_results_path=str(debate_path),
    )

    summaries = provider.prioritized_debate_summaries(
        "2026-07-16",
        task_id="intraday",
        symbols=("000001",),
    )

    assert summaries == ()


def test_dashboard_data_provider_debate_summary_keeps_rich_row_over_newer_score_only_row(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    rows = [
        {
            "debate_id": "rich",
            "symbol": "600519",
            "name": "贵州茅台",
            "related_signal_date": "2026-06-05",
            "created_at": "2026-06-05T20:00:00",
            "original_score": 80.0,
            "adjusted_score": 81.0,
            "recommended_adjustment": "keep",
            "final_consensus": "完整辩论证据保留。",
            "final_vote": {"bull": "bullish", "risk_control": "neutral"},
            "rounds": [{"round_num": 2, "summary": "最终轮证据完整。"}],
        },
        {
            "debate_id": "score-only",
            "symbol": "600519",
            "name": "贵州茅台",
            "related_signal_date": "2026-06-05",
            "created_at": "2026-06-05T21:00:00",
            "original_score": 80.0,
            "adjusted_score": 83.0,
        },
    ]
    debate_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    summary = provider.debate_summary(signal_date="2026-06-05", symbol="600519")

    assert summary is not None
    assert summary.debate_id == "rich"
    assert summary.consensus == "完整辩论证据保留。"
    assert summary.adjusted_score == 81.0


def test_dashboard_data_provider_backfills_only_matching_candidate_fingerprint(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    debate_path = tmp_path / "debates.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-07-14",
                "created_at": "2026-07-14T14:00:00+08:00",
                "symbol": "300750",
                "name": "宁德时代",
                "task_id": "intraday",
                "candidate_fingerprint": "candidate-a",
                "score": 72.0,
                "rating": "buy_candidate",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rows = []
    for fingerprint, verdict in (
        ("candidate-b", "错误批次，不应回填"),
        ("candidate-a", "正确批次，应回填"),
    ):
        rows.append(
                {
                    "debate_id": fingerprint,
                "symbol": "300750",
                "related_signal_date": "2026-07-14",
                "candidate_fingerprint": fingerprint,
                "task_id": "intraday",
                "final_vote": {"bull": "bullish"},
                    "final_consensus": verdict,
                    "research_verdict": verdict,
                    "opposition_points": ["高开低走或量价背离则失效"],
                    "rounds": [{"round_num": 1, "opinions": [{"role": "bull"}]}],
            }
        )
    debate_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        debate_results_path=str(debate_path),
    )

    row = provider._same_day_unique_rows("2026-07-14")[0]

    assert row["debate_research_verdict"] == "正确批次，应回填"


def test_dashboard_data_provider_drops_explicitly_incomplete_debate_metadata(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debates.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "incomplete",
                "symbol": "300750",
                "related_signal_date": "2026-07-14",
                "final_vote": {"bull": "bullish"},
                "final_consensus": "看多",
                "process_recorded": False,
                "conclusion_recorded": True,
                "rounds": [{"round_num": 1, "opinions": [{"role": "bull"}]}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        debate_results_path=str(debate_path),
    )

    assert provider.debate_summary(signal_date="2026-07-14", symbol="300750") is None


def test_dashboard_data_provider_build_debate_conclusion_prefers_structured_chain_and_gate() -> (
    None
):
    summary = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-structured",
        rating="A",
        original_score=80.0,
        adjusted_score=82.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.31,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.82,
                key_argument="海外主线仍在扩散。",
                key_risk="",
                key_opportunity="",
            ),
        ),
        cross_market_chain_summary=(
            "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
            "确认 机器人龙头放量上攻且核心零部件同步走强"
        ),
        cross_market_validation_summary="机器人龙头放量上攻且核心零部件同步走强",
        cross_market_invalidation_summary="只有海外叙事但A股机器人板块不共振",
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
        historical_context_note="强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        support_points=("海外主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )

    conclusion = build_debate_conclusion(summary)

    assert conclusion.decision_line == "研究口径: 倾向优先纸面复核；卡点 先确认承接"
    assert conclusion.cross_market_line == "跨市传导: 海外主线仍在扩散。"
    assert (
        conclusion.chain_or_trigger_line
        == "传导链: 产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜确认 机器人龙头放量上攻且核心零部件同步走强；触发 若放量延续则优先复核"
    )
    assert (
        conclusion.validation_line == "确认信号: 机器人龙头放量上攻且核心零部件同步走强"
    )
    assert conclusion.invalidation_line == "失效信号: 只有海外叙事但A股机器人板块不共振"
    assert conclusion.history_line == "历史校验: 强证据样本 4/5 命中"
    assert conclusion.reliability_line == "角色可信度: 跨市场 近21天 7/10 (70%)"


def test_dashboard_data_provider_build_debate_conclusion_keeps_gate_when_verdict_missing() -> (
    None
):
    summary = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-gate-only",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.42,
        consensus="",
        adjustment_reason="",
        bull_count=2,
        bear_count=2,
        neutral_count=1,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        primary_risk_gate="等待指数企稳",
        next_trigger="放量站回 20 日线再复核",
        opposition_points=("系统性风险尚未释放",),
        watch_items=("观察次日承接",),
    )

    conclusion = build_debate_conclusion(summary)

    assert conclusion.decision_line == "核心卡点: 等待指数企稳"
    assert conclusion.chain_or_trigger_line == "下一触发: 放量站回 20 日线再复核"
    assert conclusion.opposition_line == "讨论反对: 系统性风险尚未释放"
    assert conclusion.watch_line == "讨论待确认: 观察次日承接"


def test_dashboard_data_provider_debate_summary_uses_highest_round_for_agent_views(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-unsorted-rounds",
                "symbol": "600519",
                "name": "贵州茅台",
                "related_signal_date": "2026-06-05",
                "created_at": "2026-06-05T21:00:00",
                "final_consensus": "最终轮更谨慎。",
                "rounds": [
                    {
                        "round_num": 2,
                        "opinions": [
                            {
                                "role": "risk_control",
                                "stance": "bearish",
                                "confidence": 0.81,
                                "arguments": ["最终轮要求降温"],
                            }
                        ],
                    },
                    {
                        "round_num": 1,
                        "opinions": [
                            {
                                "role": "risk_control",
                                "stance": "bullish",
                                "confidence": 0.55,
                                "arguments": ["第一轮仍偏乐观"],
                            }
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    summary = provider.debate_summary(signal_date="2026-06-05", symbol="600519")

    assert summary is not None
    assert summary.recommended_adjustment_label == "辩论证据待补全"
    assert summary.agent_views[0].stance == "bearish"
    assert summary.agent_views[0].key_argument == "最终轮要求降温"
    assert not any("0.0 -> 0.0" in line for line in summary.summary_lines)
    assert not any(
        "投票分布: 看多 0 / 看空 0 / 中性 0" in line for line in summary.summary_lines
    )


def test_dashboard_data_provider_debate_summary_surfaces_market_context_line(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-market-context",
                "symbol": "600519",
                "name": "贵州茅台",
                "related_signal_date": "2026-06-05",
                "created_at": "2026-06-05T21:00:00",
                "original_score": 80.0,
                "adjusted_score": 81.0,
                "recommended_adjustment": "keep",
                "final_consensus": "外资偏强，但仍需观察次日承接。",
                "final_vote": {"cross_market": "bullish"},
                "rounds": [{"round_num": 2, "summary": "最终轮证据完整。"}],
                "watch_items": ["观察北向强弱是否在次日延续，避免只是一日交易性流入。"],
                "support_points": ["外资风险偏好改善，对核心权重形成支撑。"],
                "opposition_points": ["如果只是单日脉冲，次日承接可能不足。"],
                "historical_context_note": "强证据样本 4/5 命中，冲突样本 1/3。",
                "role_reliability_lines": ["跨市场: 近21天 7/10 (70%)｜当前权重 0.18"],
                "market_context_lines": [
                    "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
                    "全局雷达: 全市场 偏空｜宏观风险｜海外风险偏好回落。",
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    summary = provider.debate_summary(signal_date="2026-06-05", symbol="600519")

    assert summary is not None
    assert any(
        line == "市场上下文: 北向资金: 偏强（5日 z=1.20），外资风险偏好改善。"
        for line in summary.summary_lines
    )
    assert any(line == "讨论视角: 跨市传导" for line in summary.summary_lines)
    assert summary.support_points == ("外资风险偏好改善，对核心权重形成支撑。",)
    assert summary.opposition_points == ("如果只是单日脉冲，次日承接可能不足。",)
    assert summary.watch_items == (
        "观察北向强弱是否在次日延续，避免只是一日交易性流入。",
    )
    assert summary.historical_context_note == "强证据样本 4/5 命中，冲突样本 1/3。"
    assert summary.role_reliability_lines == (
        "跨市场: 近21天 7/10 (70%)｜当前权重 0.18",
    )


def test_dashboard_data_provider_debate_agent_views_include_cross_market_role(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-cross-market",
                "symbol": "300750",
                "name": "宁德时代",
                "related_signal_date": "2026-06-05",
                "created_at": "2026-06-05T21:00:00",
                "final_vote": {
                    "bull": "bullish",
                    "cross_market": "bullish",
                },
                "rounds": [
                    {
                        "round_num": 2,
                        "opinions": [
                            {
                                "role": "cross_market",
                                "stance": "bullish",
                                "confidence": 0.8,
                                "arguments": [
                                    "海外主线已映射到A股方向: 海外物理AI叙事升温"
                                ],
                                "risk_factors": [
                                    "⚠️ 海外叙事未必立刻传到A股，需确认板块共振"
                                ],
                                "opportunity_factors": [
                                    "✅ 跨市传导匹配: 海外物理AI叙事升温"
                                ],
                            },
                            {
                                "role": "bull",
                                "stance": "bullish",
                                "confidence": 0.7,
                                "arguments": ["技术面强势，趋势延续概率大"],
                                "risk_factors": [],
                                "opportunity_factors": ["✅ 技术面强势"],
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    summary = provider.debate_summary(signal_date="2026-06-05", symbol="300750")

    assert summary is not None
    assert [view.role_label for view in summary.agent_views] == ["技术多头", "跨市传导"]


def test_dashboard_data_provider_dedupes_same_symbol_across_runtime_tasks_and_sanitizes_empty_news(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"

    def row(symbol: str, task_id: str, created_at: str, score: float) -> dict:
        return {
            "debate_id": f"{task_id}-{symbol}-{created_at}",
            "symbol": symbol,
            "name": symbol,
            "task_id": task_id,
            "related_signal_date": "2026-07-14",
            "created_at": created_at,
            "original_score": score,
            "adjusted_score": score,
            "final_vote": {"cross_market": "neutral"},
            "research_verdict": (
                "倾向继续观察，机会在 技术多头: 技术面强势，"
                "但卡点是 跨市传导: ⚠️ 海外叙事未必立刻传到A股，需确认板块共振"
            ),
                "primary_risk_gate": "跨市传导: ⚠️ 海外叙事未必立刻传到A股，需确认板块共振",
                "opposition_points": ["海外叙事未形成板块共振则失效"],
            "next_trigger": "等待量价确认",
            "market_context_lines": [
                "消息状态: 部分可用",
                "消息结果: 无可用新闻记录",
            ],
            "rounds": [
                {
                    "round_num": 1,
                    "opinions": [
                        {
                            "role": "cross_market",
                            "stance": "neutral",
                            "arguments": ["跨市场线索存在"],
                            "risk_factors": [
                                "⚠️ 海外叙事未必立刻传到A股，需确认板块共振"
                            ],
                        }
                    ],
                }
            ],
        }

    debate_path.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in (
                row("002084", "intraday", "2026-07-14T10:00:00+08:00", 70),
                row("002084", "midday", "2026-07-14T12:00:00+08:00", 80),
                row("300604", "intraday", "2026-07-14T11:00:00+08:00", 75),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    summaries = provider.debate_summaries("2026-07-14", limit=10)

    assert [item.symbol for item in summaries] == ["002084", "300604", "002084"]
    assert summaries[0].original_score == 80
    assert summaries[0].cross_market_summary == ""
    assert summaries[0].research_verdict == "倾向继续观察，机会在 技术多头: 技术面强势"
    assert (
        summaries[0].primary_risk_gate == "消息或规则传导证据缺失，跨市视角不形成结论。"
    )
    cross_market_view = next(
        view for view in summaries[0].agent_views if view.role_id == "cross_market"
    )
    assert cross_market_view.key_argument == ""
    assert "海外叙事未必立刻传到A股" not in " ".join(summaries[0].summary_lines)


def test_dashboard_data_provider_home_digest_expands_partial_debate_coverage(
    monkeypatch, tmp_path: Path
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )
    task_view = SimpleNamespace(
        selected_date="2026-07-14",
        latest_date="2026-07-14",
        available_dates=("2026-07-14",),
        detail_cards=(),
    )
    spotlights = tuple(
        SimpleNamespace(symbol=symbol) for symbol in ("002084", "300604", "688981")
    )
    current = tuple(
        SimpleNamespace(symbol=symbol) for symbol in ("002084", "300604", "688981")
    )

    monkeypatch.setattr(
        provider, "build_task_digest_view", lambda *_args, **_kwargs: task_view
    )
    monkeypatch.setattr(
        provider, "same_day_task_rows", lambda *_args, **_kwargs: (SimpleNamespace(),)
    )
    monkeypatch.setattr(
        provider,
        "live_candidate_view",
        lambda **_kwargs: SimpleNamespace(
            candidates=(SimpleNamespace(),), artifact_date="2026-07-14", status="fresh"
        ),
    )
    monkeypatch.setattr(
        provider, "live_candidate_spotlights", lambda *_args, **_kwargs: spotlights
    )
    calls: list[tuple[int, tuple[str, ...]]] = []

    def prioritized(*_args, limit: int, symbols: tuple[str, ...], **_kwargs):
        calls.append((limit, symbols))
        return tuple(item for item in current if item.symbol in symbols)

    monkeypatch.setattr(provider, "prioritized_debate_summaries", prioritized)
    monkeypatch.setattr(
        provider, "date_overview", lambda *_args, **_kwargs: SimpleNamespace()
    )
    monkeypatch.setattr(
        provider, "paper_summary", lambda *_args, **_kwargs: SimpleNamespace()
    )

    payload = provider.home_digest_payload("intraday", signal_date="2026-07-14")

    assert calls == [(3, ("002084", "300604", "688981"))]
    assert payload.debates == current


def test_dashboard_data_provider_splits_strategy_csv_values_for_home_cards(
    tmp_path: Path,
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )

    assert provider._strategy_tuple("ma_pullback,low_vol_trend") == (
        "ma_pullback",
        "low_vol_trend",
    )


def test_dashboard_data_provider_debate_agent_view_flags_vote_and_speech_mismatch(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-conflict",
                "symbol": "600519",
                "name": "贵州茅台",
                "related_signal_date": "2026-06-05",
                "created_at": "2026-06-05T21:00:00",
                "final_consensus": "最终投票和最终轮发言存在冲突。",
                "final_vote": {"bull": "bullish"},
                "rounds": [
                    {
                        "round_num": 2,
                        "opinions": [
                            {
                                "role": "bull",
                                "final_position": "bearish",
                                "confidence": 0.81,
                                "arguments": ["最终轮要求降温"],
                            }
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(debate_path),
    )

    summary = provider.debate_summary(signal_date="2026-06-05", symbol="600519")

    assert summary is not None
    assert summary.bull_count == 1
    assert summary.agent_views[0].stance == "bullish"
    assert summary.agent_views[0].stance_label == "看多（发言冲突）"
    assert summary.agent_views[0].key_argument == (
        "最终投票与发言不一致: 投票看多，发言看空，需人工复核"
    )


def test_dashboard_data_provider_prefers_explicit_task_id_over_strategy_guess(
    tmp_path: Path,
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )

    assert (
        provider._row_task_id(
            {
                "task_id": "closing_premium",
                "strategies": ["morning_breakout", "closing_premium"],
            }
        )
        == "closing_premium"
    )
    assert (
        provider._row_task_id({"strategies": ["morning_breakout", "closing_premium"]})
        == "main_chain"
    )


def test_dashboard_data_provider_surfaces_intraday_paper_review_without_formal_ledger(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    intraday_latest_path = tmp_path / "intraday_latest.csv"
    intraday_ledger_path = tmp_path / "intraday_predictions.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    ledger_path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in [
                {
                    "signal_date": "2026-06-09",
                    "created_at": "2026-06-09T10:05:00+08:00",
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "score": 91,
                    "rating": "strong_buy_candidate",
                    "portfolio_action": "promote",
                    "task_id": "intraday",
                    "run_task_id": "intraday",
                    "run_requested_source": "auto",
                    "run_actual_source": "eastmoney",
                    "run_source_health_label": "healthy",
                    "run_source_health_message": "eastmoney 健康",
                },
                {
                    "signal_date": "2026-06-09",
                    "created_at": "2026-06-09T15:05:00+08:00",
                    "symbol": "000001",
                    "name": "平安银行",
                    "score": 72,
                    "rating": "buy_candidate",
                    "strategies": ["volume_breakout"],
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        intraday_ledger_path=str(intraday_ledger_path),
        intraday_latest_path=str(intraday_latest_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    assert provider._row_task_id({"task_id": "intraday"}) == "intraday"

    main_view = provider.build_task_view("main_chain", signal_date="2026-06-09")
    intraday_view = provider.build_task_view("intraday", signal_date="2026-06-09")
    same_day_map = {
        row.task_id: row for row in provider.same_day_task_rows("2026-06-09")
    }
    timeline = provider.timeline_rows(limit=1)[0]

    assert main_view.candidate_count == 1
    assert main_view.detail_cards[0].symbol == "000001"
    assert intraday_view.candidate_count == 1
    assert intraday_view.actionable_count == 1
    assert intraday_view.watch_count == 0
    assert intraday_view.detail_cards[0].rank_label == "第一顺位"
    assert "未收盘快照" in intraday_view.headline
    assert any("不进入正式 ledger" in line for line in intraday_view.summary_lines)
    assert same_day_map["intraday"].status_label == "有推荐"
    assert same_day_map["intraday"].actionable_count == 1
    assert same_day_map["intraday"].watch_count == 0
    assert timeline.actionable_total == 2
    assert timeline.watch_total == 0


def test_dashboard_data_provider_does_not_fallback_to_main_chain_for_current_intraday(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "aqsp.web.data_provider.today_shanghai",
        lambda: date(2026, 7, 14),
    )
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-07-14",
                "symbol": "600519",
                "name": "贵州茅台",
                "task_id": "main_chain",
                "score": 90,
                "rating": "buy_candidate",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        intraday_latest_path=str(tmp_path / "missing-intraday.csv"),
    )

    payload = provider.home_digest_payload("intraday", signal_date="2026-07-14")

    assert payload.spotlights == ()
    assert payload.debates == ()


def test_dashboard_data_provider_keeps_same_day_intraday_debate_when_artifact_is_stale(
    tmp_path: Path,
) -> None:
    latest_path = tmp_path / "intraday_latest.csv"
    debate_path = tmp_path / "debate_results.jsonl"
    pd.DataFrame(
        [
            {
                "symbol": "000001",
                "name": "平安银行",
                "date": "2026-07-14",
                "signal_date": "2026-07-14",
                "score": 72,
                "rating": "buy_candidate",
                "reasons": "量价趋势改善",
                "task_id": "intraday",
                "created_at": "2026-07-14T11:00:00+08:00",
            }
        ]
    ).to_csv(latest_path, index=False)
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "intraday-000001-1",
                "symbol": "000001",
                "task_id": "intraday",
                "related_signal_date": "2026-07-14",
                "candidate_signal_date": "2026-07-14",
                "candidate_fingerprint": "candidate-from-debate",
                "created_at": "2026-07-14T11:01:00+08:00",
                "original_score": 72,
                "adjusted_score": 72,
                "final_consensus": "观察",
                "final_vote": {"risk_control": "neutral"},
                "research_verdict": "等待承接确认",
                    "primary_risk_gate": "盘中产物已过期",
                    "next_trigger": "重新刷新盘中数据",
                    "opposition_points": ["盘中数据过期，任何追涨判断均失效"],
                "process_recorded": True,
                "conclusion_recorded": True,
                "advisory_boundary_ok": True,
                "evidence_sufficient": True,
                "rounds": [
                    {
                        "round_num": 1,
                        "summary": "看多 1 / 看空 1 / 中性 1",
                        "opinions": [],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        intraday_latest_path=str(latest_path),
        debate_results_path=str(debate_path),
    )

    payload = provider.home_digest_payload("intraday", signal_date="2026-07-14")

    assert payload.spotlights
    assert payload.spotlights[0].status_label == "数据已过期"
    assert [item.symbol for item in payload.debates] == ["000001"]
    assert payload.debates[0].candidate_fingerprint == "candidate-from-debate"


def test_dashboard_data_provider_merges_debate_by_date_task_and_symbol_when_candidate_fingerprint_missing(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    debate_path = tmp_path / "debate_results.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "signal_date": "2026-07-14",
                "task_id": "intraday",
                "score": 70,
                "rating": "buy_candidate",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "task-date-symbol-fallback",
                "symbol": "600519",
                "task_id": "intraday",
                "related_signal_date": "2026-07-14",
                "candidate_fingerprint": "debate-fingerprint",
                "created_at": "2026-07-14T10:00:00+08:00",
                "original_score": 70,
                "adjusted_score": 70,
                    "research_verdict": "同日同任务回退映射",
                    "opposition_points": ["高开低走则失效"],
                "final_vote": {"risk_control": "neutral"},
                "process_recorded": True,
                "conclusion_recorded": True,
                "advisory_boundary_ok": True,
                "evidence_sufficient": True,
                "rounds": [{"round_num": 1, "summary": "有效讨论", "opinions": []}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        debate_results_path=str(debate_path),
    )

    merged = provider._merge_debate_evidence(
        {
            "symbol": "600519",
            "signal_date": "2026-07-14",
            "task_id": "intraday",
            "score": 70,
        }
    )

    assert merged["debate_research_verdict"] == "同日同任务回退映射"


def test_dashboard_data_provider_blocked_candidate_without_reason_surfaces_missing_evidence(
    tmp_path: Path,
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )

    assert (
        provider._candidate_blocker_text({"portfolio_action": "downgrade"})
        == "阻塞原因未记录，需补充风险说明或复核条件"
    )


def test_dashboard_data_provider_counts_promote_with_blocker_as_blocked_only(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 88,
                "rating": "buy_candidate",
                "portfolio_action": "promote",
                "candidate_status": "等待阻塞解除",
                "candidate_blocker": "涨停无法追入",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    overview = provider.date_overview("2026-06-05")

    assert overview.actionable_total == 0
    assert overview.watch_total == 0
    assert overview.blocked_total == 1


def test_dashboard_data_provider_builds_task_views_and_dedupes_latest_rows(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T15:01:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 88,
            "rating": "strong_buy_candidate",
            "portfolio_action": "promote",
            "candidate_status": "延续上升",
            "candidate_next_step": "等待开盘承接确认后，再决定是否保留主仓",
            "candidate_review_window": "开盘前后",
            "candidate_review_priority": "high",
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
            "run_data_latest_trade_date": "2026-06-05",
            "run_data_lag_days": 0,
            "run_market_context_lines": [
                "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
                "全局雷达: 全市场 偏多｜海外风险偏好回暖。",
            ],
            "strategies": ["volume_breakout"],
            "reasons": ["量价齐升", "接近新高"],
            "risks": ["追高波动"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T15:00:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 77,
            "rating": "buy_candidate",
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
            "strategies": ["volume_breakout"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:59:00+08:00",
            "symbol": "000001",
            "name": "平安银行",
            "score": 55,
            "rating": "watch",
            "portfolio_action": "downgrade",
            "candidate_status": "观察阻塞",
            "candidate_blocker": "板块集中度过高，压低银行暴露",
            "candidate_next_step": "等待板块暴露回落后，再重新评估纸面复核优先级",
            "candidate_review_window": "板块分化时",
            "candidate_review_priority": "medium",
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
            "strategies": ["volume_breakout"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T10:05:00+08:00",
            "symbol": "300750",
            "name": "宁德时代",
            "score": 61,
            "rating": "buy_candidate",
            "strategies": ["morning_breakout"],
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
        },
        {
            "signal_date": "2026-06-04",
            "created_at": "2026-06-04T14:55:00+08:00",
            "symbol": "600036",
            "name": "招商银行",
            "score": 49,
            "rating": "watch",
            "portfolio_action": "keep",
            "candidate_status": "等待确认",
            "candidate_next_step": "等量能恢复后再看是否回到候选序列",
            "candidate_review_window": "次日午后",
            "candidate_review_priority": "medium",
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
            "strategies": ["volume_breakout"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:55:00+08:00",
            "symbol": "002594",
            "name": "比亚迪",
            "score": 64,
            "rating": "buy_candidate",
            "strategies": ["closing_premium"],
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:56:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 72,
            "rating": "buy_candidate",
            "portfolio_action": "promote",
            "candidate_status": "尾盘仍在主线",
            "candidate_next_step": "尾盘确认承接后纳入次日复核",
            "strategies": ["closing_premium"],
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in ledger_rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "briefing-2026-06-05.md").write_text(
        (
            "# 每日研究复盘-2026-06-05\n\n"
            "## 市场态势\n\n"
            "当前市场态势: **震荡偏强：等待主线确认**\n\n"
            "## 明日重点\n\n"
            "- **600519 贵州茅台**: 观察量能是否延续\n"
        ),
        encoding="utf-8",
    )
    (reports_dir / "latest.md").write_text(
        (
            "# AI 量化选股报告(close, 数据日期 2026-06-05)\n\n"
            "## 📌 执行摘要\n\n"
            "今日主链 1 只可执行，另有 1 只转观察。\n"
            "优先跟踪高分主链候选。\n\n"
            "## 运行参数\n"
            "- 数据源: auto -> eastmoney\n"
            "- 数据时效: latest=2026-06-05 / lag=0d\n"
            "- 数据健康: healthy / eastmoney 健康\n"
            "- 候选池: 显式 2 / 解析 2 / 取数 2 / 筛选前 2 / 最终 2\n"
            "- 规则版本: 1.1.1\n"
            "- regime: stable_uptrend\n"
        ),
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    assert [item.task_id for item in provider.task_options()] == [
        "main_chain",
        "intraday",
        "morning_breakout",
        "closing_premium",
        "closing_review",
        "briefing",
    ]

    main_view = provider.build_task_view("main_chain", signal_date="2026-06-05")
    assert main_view.actionable_count == 1
    assert main_view.watch_count == 0
    assert main_view.blocked_count == 1
    assert "贵州茅台" in main_view.headline
    assert any("板块集中度过高" in item for item in main_view.blocker_lines)
    assert any("开盘前后" in item for item in main_view.review_lines)
    assert main_view.detail_cards[0].display_name == "600519 贵州茅台"
    assert main_view.detail_cards[0].rank_label == "第一顺位"
    assert "优先级上调" in main_view.detail_cards[0].decision_note
    assert main_view.detail_cards[0].reasons == ("量价齐升", "接近新高")
    assert main_view.detail_cards[0].risks == ("追高波动",)
    assert main_view.ranking_lines[0].startswith("第一顺位: 600519 贵州茅台")
    assert any(
        line.startswith("阻塞观察: 000001 平安银行") for line in main_view.ranking_lines
    )
    assert main_view.report_summary_lines == (
        "今日主链 1 只纸面复核，另有 1 只转观察。",
        "优先跟踪高分今日重点名单。",
    )
    assert any(
        line.startswith("数据链路: 实时源 eastmoney（live_short=primary）")
        for line in main_view.summary_lines
    )
    assert main_view.report_source.endswith("latest.md")
    assert "T" in main_view.report_mtime
    assert main_view.lifecycle_lines[0].startswith(
        "600519 贵州茅台 | 优先级上调 | 延续上升"
    )
    assert any(
        "000001 平安银行 | 当前限制: 板块集中度过高" in line
        for line in main_view.unlock_lines
    )
    assert main_view.previous_date == "2026-06-04"
    assert main_view.runtime_lines[0] == "数据来源: auto -> eastmoney"
    assert "市场标签: stable_uptrend" in main_view.runtime_lines
    assert (
        "市场上下文: 北向资金: 偏强（5日 z=1.20），外资风险偏好改善。"
        in main_view.runtime_lines
    )
    assert (
        "市场上下文: 全局雷达: 全市场 偏多｜海外风险偏好回暖。"
        in main_view.runtime_lines
    )
    assert main_view.delta_lines == (
        "较 2026-06-04 候选 +1",
        "较 2026-06-04 待复核 +1",
        "较 2026-06-04 观察 -1",
        "较 2026-06-04 阻塞 +1",
    )
    assert main_view.agenda_lines[0].startswith("先看推荐: 600519 贵州茅台")
    assert any(
        line.startswith("先核对卡点: 000001 平安银行")
        for line in main_view.agenda_lines
    )
    assert any(
        line.startswith("安排复核: 600519 贵州茅台") for line in main_view.agenda_lines
    )

    morning_view = provider.build_task_view(
        "morning_breakout", signal_date="2026-06-05"
    )
    assert morning_view.task_label == "早盘策略"
    assert morning_view.candidate_count == 1
    assert morning_view.actionable_count == 1

    closing_view = provider.build_task_view("closing_premium", signal_date="2026-06-05")
    assert closing_view.task_label == "尾盘策略"
    assert closing_view.candidate_count == 2

    briefing_view = provider.build_task_view("briefing", signal_date="2026-06-05")
    assert briefing_view.task_label == "简报回看"
    assert "明日重点" in briefing_view.report_markdown
    assert briefing_view.report_source.endswith("briefing-2026-06-05.md")
    assert "T" in briefing_view.report_mtime
    assert briefing_view.market_environment == "震荡偏强：等待主线确认"
    assert (
        "市场上下文: 北向资金: 偏强（5日 z=1.20），外资风险偏好改善。"
        in briefing_view.runtime_lines
    )
    assert briefing_view.lifecycle_lines == main_view.lifecycle_lines
    assert briefing_view.next_day_focus_lines == (
        "**600519 贵州茅台**: 观察量能是否延续",
    )
    assert any(
        line.startswith("明日重点: **600519 贵州茅台**")
        for line in briefing_view.agenda_lines
    )

    latest_signals = provider.latest_signal_frame(
        limit=10,
        task_id="main_chain",
        signal_date="2026-06-05",
    )
    assert list(latest_signals["代码"]) == ["600519", "000001"]
    assert latest_signals.iloc[0]["候选状态"] == "延续上升"

    task_snapshots = provider.task_snapshots()
    snapshot_map = {snapshot.task_id: snapshot for snapshot in task_snapshots}
    assert snapshot_map["main_chain"].status_label == "有推荐"
    assert snapshot_map["morning_breakout"].status_label == "有推荐"
    assert snapshot_map["closing_premium"].status_label == "有推荐"
    assert snapshot_map["briefing"].status_label == "待跟踪"
    assert "无纸面复核对象" not in snapshot_map["briefing"].headline

    history_rows = provider.task_history_rows("main_chain", limit=2)
    assert [row.signal_date for row in history_rows] == ["2026-06-05", "2026-06-04"]
    assert history_rows[0].candidate_count == 2
    assert history_rows[1].watch_count == 1

    history_frame = provider.task_history_frame("main_chain", limit=2)
    assert list(history_frame["日期"]) == ["2026-06-05", "2026-06-04"]
    assert list(history_frame["待复核"]) == [1, 0]

    timeline_rows = provider.timeline_rows(limit=3)
    assert timeline_rows[0].signal_date == "2026-06-05"
    assert "主链推荐" in timeline_rows[0].task_labels
    assert "早盘策略" in timeline_rows[0].task_labels
    assert "尾盘策略" in timeline_rows[0].task_labels
    assert timeline_rows[0].actionable_total == 3
    assert timeline_rows[0].watch_total == 0
    assert timeline_rows[0].blocked_total == 1

    timeline_frame = provider.timeline_frame(limit=2)
    assert list(timeline_frame["日期"]) == ["2026-06-05", "2026-06-04"]
    assert "任务覆盖" in timeline_frame.columns

    same_day_rows = provider.same_day_task_rows("2026-06-05")
    same_day_map = {row.task_id: row for row in same_day_rows}
    assert {
        "main_chain",
        "morning_breakout",
        "closing_premium",
        "closing_review",
        "briefing",
    } <= set(same_day_map)
    assert same_day_map["main_chain"].status_label == "有推荐"
    assert same_day_map["main_chain"].created_at == "2026-06-05T15:01:00+08:00"
    assert same_day_map["morning_breakout"].created_at == "2026-06-05T10:05:00+08:00"
    assert same_day_map["closing_premium"].created_at == "2026-06-05T14:56:00+08:00"
    assert same_day_map["closing_review"].status_label == "待复盘"
    assert same_day_map["briefing"].status_label == "待跟踪"

    same_day_frame = provider.same_day_task_frame("2026-06-05")
    assert "任务" in same_day_frame.columns
    assert "主链推荐" in set(same_day_frame["任务"])

    spotlights = provider.same_day_candidate_spotlights("2026-06-05")
    assert [item.display_name for item in spotlights[:3]] == [
        "600519 贵州茅台",
        "002594 比亚迪",
        "300750 宁德时代",
    ]
    assert spotlights[0].task_labels == ("主链推荐", "尾盘策略")
    assert spotlights[1].task_labels == ("尾盘策略",)
    assert spotlights[2].task_labels == ("早盘策略",)
    assert any(item.blocker == "板块集中度过高，压低银行暴露" for item in spotlights)

    journey = provider.same_day_candidate_journey("2026-06-05", "600519")
    assert [step.task_id for step in journey] == ["main_chain", "closing_premium"]
    assert [step.phase_label for step in journey] == ["盘前主链", "尾盘确认"]
    assert [step.action_label for step in journey] == ["优先级上调", "优先级上调"]

    morning_journey = provider.same_day_candidate_journey("2026-06-05", "300750")
    assert len(morning_journey) == 1
    assert morning_journey[0].task_id == "morning_breakout"
    assert morning_journey[0].phase_label == "早盘观察"

    closing_journey = provider.same_day_candidate_journey("2026-06-05", "002594")
    assert len(closing_journey) == 1
    assert closing_journey[0].task_id == "closing_premium"
    assert closing_journey[0].phase_label == "尾盘确认"

    date_overview = provider.date_overview("2026-06-05")
    assert date_overview.signal_date == "2026-06-05"
    assert date_overview.task_count >= 5
    assert date_overview.actionable_total == 3

    assert date_overview.watch_total == 0
    assert date_overview.blocked_total == 1
    assert date_overview.top_task_label in {"主链推荐", "早盘策略", "尾盘策略"}
    assert (
        "纸面复核" in date_overview.focus_headline
        or "待复核" in date_overview.focus_headline
        or "继续观察" in date_overview.focus_headline
        or "核对卡点" in date_overview.focus_headline
        or "优先级上调" in date_overview.focus_headline
        or "次日复核" in date_overview.focus_headline
    )
    assert any(
        name in date_overview.focus_headline
        for name in ("600519 贵州茅台", "002594 比亚迪", "300750 宁德时代")
    )
    assert "当日流程:" in date_overview.workflow_summary
    assert "本日共覆盖" in date_overview.archive_summary

    assert provider.preferred_task_for_date("2026-06-05") == "main_chain"

    scoped_events = provider.paper_events_frame(
        limit=20,
        signal_date="2026-06-05",
    )
    assert scoped_events.empty

    main_chain_signal_frame = provider.latest_signal_frame(
        limit=20,
        task_id="main_chain",
        signal_date="2026-06-05",
    )
    assert set(main_chain_signal_frame["代码"]) == {"600519", "000001"}
    assert "300750" not in set(main_chain_signal_frame["代码"])
    assert "002594" not in set(main_chain_signal_frame["代码"])


def test_dashboard_data_provider_loads_intraday_latest_as_paper_review(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    intraday_path = tmp_path / "intraday_predictions.jsonl"
    latest_path = tmp_path / "intraday_latest.csv"
    ledger_path.write_text("", encoding="utf-8")
    paper_path.write_text("", encoding="utf-8")
    intraday_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-07-09",
                "symbol": "__RUN__",
                "task_id": "intraday",
                "event_type": "blocked_by_circuit_breaker",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "symbol": "600900",
                "name": "长江电力",
                "date": "2026-07-09",
                "score": 55.38,
                "rating": "avoid",
                "portfolio_action": "keep",
                "candidate_status": "",
                "candidate_blocker": "",
                "candidate_next_step": "",
                "reasons": "低波趋势",
                "risks": "",
            }
        ]
    ).to_csv(latest_path, index=False)

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        intraday_ledger_path=str(intraday_path),
        intraday_latest_path=str(latest_path),
        debate_results_path=str(tmp_path / "debate_results.jsonl"),
        bt_logs_dir=str(tmp_path / "logs" / "bt"),
        reports_dir=str(tmp_path / "reports"),
    )

    assert provider.task_dates("intraday") == ("2026-07-09",)
    assert provider.dashboard_dates()[0] == "2026-07-09"
    assert provider.preferred_task_for_date("2026-07-09") == "intraday"
    intraday_rows = [
        row
        for row in provider.load_signal_rows()
        if str(row.get("symbol", "") or "") == "600900"
    ]
    assert intraday_rows[0]["rating"] == "buy_candidate"
    assert intraday_rows[0]["display_rating_corrected_from_score"] is True

    overview = provider.date_overview("2026-07-09")
    assert overview.actionable_total == 1
    assert overview.watch_total == 0
    assert overview.blocked_total == 0

    spotlights = provider.same_day_candidate_spotlights("2026-07-09")
    assert len(spotlights) == 1
    assert spotlights[0].display_name == "600900 长江电力"
    assert spotlights[0].action_label == "纸面复核"
    assert spotlights[0].task_labels == ("盘中观察",)


def test_task_dates_includes_formal_no_pick_scan_when_main_chain_has_no_rows(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "symbol": "__RUN__",
                "name": "run_event",
                "event_type": "backfill_no_picks",
                "signal_date": "2026-07-23",
                "status": "backfill_no_picks",
                "scanned_symbols": 4612,
                "source": "sqlite_db",
                "reason": "no picks from real sample backfill",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        debate_results_path=str(tmp_path / "debates.jsonl"),
    )

    assert provider.task_dates("main_chain") == ("2026-07-23",)


def test_dashboard_data_provider_runtime_overview_uses_run_event_not_candidate_row(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    intraday_path = tmp_path / "intraday_predictions.jsonl"
    ledger_path.write_text("", encoding="utf-8")
    intraday_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "symbol": "__RUN__",
                        "name": "run_event",
                        "event_type": "runtime_context",
                        "signal_date": "2026-07-10",
                        "run_task_id": "intraday",
                        "run_requested_source": "online_first",
                        "run_actual_source": "sina",
                        "run_data_latest_trade_date": "2026-07-10",
                        "run_data_lag_days": 0,
                        "run_final_count": 0,
                        "run_no_candidate_reason": "策略筛选未产生符合条件的候选",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "symbol": "603019",
                        "name": "中科曙光",
                        "signal_date": "2026-07-10",
                        "run_task_id": "intraday",
                        "score": 72.5,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        intraday_ledger_path=str(intraday_path),
        intraday_latest_path=str(tmp_path / "missing.csv"),
    )

    overview = provider.runtime_overview("2026-07-10")

    assert overview.effective_source == "sina"
    assert overview.data_latest_trade_date == "2026-07-10"
    assert overview.lag_days == "0"
    assert "策略筛选未产生符合条件的候选" in overview.conclusion


def test_runtime_overview_explains_real_no_pick_scan(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "symbol": "__RUN__",
                "name": "run_event",
                "event_type": "backfill_no_picks",
                "signal_date": "2026-07-23",
                "status": "backfill_no_picks",
                "scanned_symbols": 4612,
                "source": "sqlite_db",
                "reason": "no picks from real sample backfill",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper_trades.jsonl"),
        intraday_ledger_path=str(tmp_path / "intraday_predictions.jsonl"),
    )

    overview = provider.runtime_overview("2026-07-23")

    assert overview.effective_source == "sqlite_db"
    assert overview.data_latest_trade_date == "2026-07-23"
    assert "4612" in overview.conclusion
    assert "无候选" in overview.conclusion


def test_dashboard_data_provider_dedupes_intraday_ledger_and_csv_symbols(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    intraday_path = tmp_path / "intraday_predictions.jsonl"
    latest_path = tmp_path / "intraday_latest.csv"
    ledger_path.write_text("", encoding="utf-8")
    paper_path.write_text("", encoding="utf-8")
    intraday_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-07-10",
                "symbol": "000066",
                "name": "中国长城",
                "score": 63.71,
                "rating": "buy_candidate",
                "task_id": "intraday",
                "run_task_id": "intraday",
                "created_at": "2026-07-10T14:24:13+08:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "symbol": "66",
                "name": "中国长城",
                "date": "2026-07-10",
                "score": "63.71",
                "rating": "buy_candidate",
                "created_at": "2026-07-10T14:24:13+08:00",
            }
        ]
    ).to_csv(latest_path, index=False)

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        intraday_ledger_path=str(intraday_path),
        intraday_latest_path=str(latest_path),
    )

    assert provider.preferred_task_for_date("2026-07-10") == "intraday"
    view = provider.build_task_view("intraday", signal_date="2026-07-10")
    assert view.candidate_count == 1
    assert view.detail_cards[0].symbol == "000066"
    assert view.detail_cards[0].display_name == "000066 中国长城"


def test_dashboard_data_provider_build_task_digest_view_stays_lightweight(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:01:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 76,
                "rating": "buy_candidate",
                "task_id": "main_chain",
                "candidate_status": "延续上升",
                "candidate_next_step": "等待开盘承接确认",
                "run_requested_source": "online_first",
                "run_actual_source": "eastmoney",
                "run_source_health_label": "healthy",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(tmp_path / "debates.jsonl"),
        bt_logs_dir=str(tmp_path / "bt"),
    )
    monkeypatch.setattr(
        provider,
        "_build_task_view_core",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("digest view should not build the full task view")
        ),
    )
    monkeypatch.setattr(
        provider,
        "_report_document_for_signal_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("digest view should not read report markdown")
        ),
    )
    monkeypatch.setattr(
        provider,
        "_build_detail_cards",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("digest view should not build detail cards")
        ),
    )

    view = provider.build_task_digest_view("main_chain", signal_date="2026-06-05")

    assert view.selected_date == "2026-06-05"
    assert view.headline
    assert view.delta_lines == ()
    assert view.report_markdown == ""
    assert view.detail_cards == ()
    assert view.ranking_lines == ()
    assert view.candidate_count == 1
    assert view.actionable_count == 1
    assert view.source_status["actual_source"] == "eastmoney"


def test_dashboard_data_provider_homepage_summaries_do_not_build_full_task_views(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:01:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 76,
                "rating": "buy_candidate",
                "task_id": "main_chain",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )
    monkeypatch.setattr(
        provider,
        "_build_task_view_core",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("homepage summaries should stay lightweight")
        ),
    )

    rows = provider.same_day_task_rows("2026-06-05")
    snapshots = provider.task_snapshots("2026-06-05")

    assert {row.task_id for row in rows} >= {"main_chain", "closing_review"}
    assert {snapshot.task_id for snapshot in snapshots} >= {"main_chain"}
    assert rows[0].headline.startswith("主链推荐 2026-06-05")


def test_dashboard_data_provider_date_overview_prefers_debate_focus_headline() -> None:
    provider = DashboardDataProvider.__new__(DashboardDataProvider)
    provider.same_day_task_rows = lambda signal_date: (
        DashboardSameDayTaskRow(
            signal_date=signal_date,
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主链承接",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )
    provider.prioritized_debate_summaries = lambda signal_date, **kwargs: (
        DashboardDebateSummary(
            signal_date=signal_date,
            symbol="300750",
            display_name="300750 宁德时代",
            debate_id="debate-1",
            rating="A",
            original_score=80.0,
            adjusted_score=81.0,
            adjustment_weight=0.1,
            recommended_adjustment="raise",
            recommended_adjustment_label="建议上调评分",
            disagreement_score=0.32,
            consensus="先看承接",
            adjustment_reason="主线延续",
            bull_count=3,
            bear_count=1,
            neutral_count=1,
            round_count=2,
            regime="强势",
            data_source="multi",
            thresholds_version="v1",
            summary_lines=(),
            round_summaries=(),
            risk_warnings=(),
            opportunity_highlights=(),
            agent_views=(),
            research_verdict="倾向优先纸面复核",
            primary_risk_gate="先确认量价延续",
            next_trigger="若放量延续则优先复核",
        ),
    )
    provider.same_day_candidate_spotlights = lambda signal_date, limit=1: ()
    provider._same_day_unique_counts = lambda signal_date: (1, 0, 0)
    provider._workflow_summary = lambda rows: "当日流程: 主链优先"
    provider._archive_summary = lambda rows, focus_row, blocker_row: (
        "本日共覆盖 1 个任务，归档待补。"
    )

    overview = provider.date_overview("2026-06-05")

    assert overview.focus_headline == (
        "300750 宁德时代 | 倾向优先纸面复核 | 若放量延续则优先复核"
    )


def test_dashboard_data_provider_date_overview_reuses_preloaded_home_context() -> None:
    provider = DashboardDataProvider.__new__(DashboardDataProvider)
    provider.same_day_task_rows = lambda signal_date: (
        DashboardSameDayTaskRow(
            signal_date=signal_date,
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主链承接",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )
    provider.prioritized_debate_summaries = lambda *_args, **_kwargs: (
        _ for _ in ()
    ).throw(AssertionError("debates should be reused"))
    provider.same_day_candidate_spotlights = lambda *_args, **_kwargs: (
        _ for _ in ()
    ).throw(AssertionError("spotlights should be reused"))
    provider._same_day_unique_counts = lambda signal_date: (1, 0, 0)
    provider._workflow_summary = lambda rows: "当日流程: 主链优先"
    provider._archive_summary = lambda rows, focus_row, blocker_row: (
        "本日共覆盖 1 个任务，归档待补。"
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-1",
        rating="A",
        original_score=80.0,
        adjusted_score=81.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.32,
        consensus="先看承接",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认量价延续",
        next_trigger="若放量延续则优先复核",
    )

    overview = provider.date_overview("2026-06-05", spotlights=(), debates=(debate,))

    assert overview.focus_headline == (
        "300750 宁德时代 | 倾向优先纸面复核 | 若放量延续则优先复核"
    )


def test_dashboard_data_provider_reuses_signal_runtime_cache_across_homepage_views(
    monkeypatch, tmp_path: Path
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:35:00+08:00",
            "task_id": "main_chain",
            "symbol": "300750",
            "name": "宁德时代",
            "score": 72,
            "rating": "buy_candidate",
            "status": "pending",
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:45:00+08:00",
            "task_id": "morning_breakout",
            "symbol": "002594",
            "name": "比亚迪",
            "score": 66,
            "rating": "watch",
            "status": "pending",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in ledger_rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    read_calls = {"count": 0}
    original_read_ledger = read_ledger

    def counting_read_ledger(path: Path) -> list[dict[str, object]]:
        read_calls["count"] += 1
        return original_read_ledger(path)

    monkeypatch.setattr("aqsp.web.data_provider.read_ledger", counting_read_ledger)

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    provider.same_day_task_rows("2026-06-05")
    provider.task_snapshots("2026-06-05")
    provider.date_overview("2026-06-05")
    provider.timeline_rows(limit=3)

    assert read_calls["count"] == 1


def test_dashboard_data_provider_same_day_rows_fast_home_skips_report_bodies(
    monkeypatch, tmp_path: Path
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 71,
                "rating": "buy_candidate",
                "status": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "briefing-2026-06-05.md").write_text(
        "# briefing\n\n## 下一交易日重点\n- 这行不应在首页快路径被读取\n",
        encoding="utf-8",
    )
    (reports_dir / "closing_review-2026-06-05.md").write_text(
        "# closing review\n\n这行不应在首页快路径被读取\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    def fail_if_body_read(*_args, **_kwargs):
        raise AssertionError("首页快路径不应读取完整报告正文")

    monkeypatch.setattr(provider, "_read_report_document", fail_if_body_read)

    rows = provider.same_day_task_rows(
        "2026-06-05",
        include_report_insights=False,
    )
    row_map = {row.task_id: row for row in rows}

    assert row_map["briefing"].status_label == "已产出"
    assert row_map["briefing"].headline == "简报回看 2026-06-05: 已归档"
    assert row_map["closing_review"].status_label == "已复盘"
    assert row_map["closing_review"].headline == "收盘复盘 2026-06-05: 已归档"


def test_dashboard_data_provider_home_digest_payload_uses_fast_home_context(
    monkeypatch, tmp_path: Path
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 71,
                "rating": "buy_candidate",
                "status": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "briefing-2026-06-05.md").write_text(
        "# briefing\n\n## 下一交易日重点\n- 首页不读取正文\n",
        encoding="utf-8",
    )
    (reports_dir / "closing_review-2026-06-05.md").write_text(
        "# closing review\n\n首页不读取正文\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    def fail_if_body_read(*_args, **_kwargs):
        raise AssertionError("首页聚合入口不应读取完整报告正文")

    monkeypatch.setattr(provider, "_read_report_document", fail_if_body_read)
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        provider,
        "same_day_candidate_spotlights",
        lambda _date, *, limit=8: seen.setdefault("spotlight_limit", limit) and (),
    )
    monkeypatch.setattr(
        provider,
        "prioritized_debate_summaries",
        lambda _date, **kwargs: seen.setdefault("debate_limit", kwargs["limit"]) and (),
    )

    payload = provider.home_digest_payload("main_chain", signal_date="2026-06-05")

    assert payload.task_view.selected_date == "2026-06-05"
    assert payload.same_day_rows
    assert payload.debates == ()
    assert payload.overview.task_count >= 1
    assert payload.paper_summary.signal_date == "2026-06-05"
    assert seen == {"spotlight_limit": 3, "debate_limit": 3}


def test_dashboard_data_provider_same_day_rows_fast_home_accepts_latest_closing_review(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 71,
                "rating": "buy_candidate",
                "status": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "closing_review.md").write_text(
        "# latest closing review\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    row_map = {
        row.task_id: row
        for row in provider.same_day_task_rows(
            "2026-06-05",
            include_report_insights=False,
        )
    }

    assert row_map["closing_review"].status_label == "已复盘"
    assert row_map["closing_review"].headline == "收盘复盘 2026-06-05: 已归档"


def test_dashboard_data_provider_reuses_same_day_unique_rows_cache(
    monkeypatch, tmp_path: Path
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "missing-ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "missing-paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        reports_dir=str(tmp_path / "reports"),
    )
    calls: list[str] = []
    source_rows = {
        "main_chain": [
            {
                "signal_date": "2026-06-05",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 71,
                "rating": "buy_candidate",
                "status": "pending",
            }
        ],
        "morning_breakout": [
            {
                "signal_date": "2026-06-05",
                "symbol": "300750",
                "name": "宁德时代",
                "score": 68,
                "rating": "watch",
                "status": "pending",
            }
        ],
        "closing_premium": [],
    }

    def fake_task_signal_rows(task_id: str) -> list[dict[str, object]]:
        calls.append(task_id)
        return list(source_rows.get(task_id, ()))

    monkeypatch.setattr(provider, "_task_signal_rows", fake_task_signal_rows)

    first = provider._same_day_unique_rows("2026-06-05")
    second = provider._same_day_unique_rows("2026-06-05")
    counts = provider._same_day_unique_counts("2026-06-05")

    assert first == second
    assert len(first) == 2
    assert counts == (1, 1, 0)
    assert calls == ["main_chain", "morning_breakout", "closing_premium", "intraday"]


def test_dashboard_data_provider_reuses_debate_runtime_cache_across_summary_views(
    monkeypatch, tmp_path: Path
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    debate_path = tmp_path / "debate_results.jsonl"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    paper_path.write_text("", encoding="utf-8")
    ledger_path.write_text("", encoding="utf-8")
    debate_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "symbol": "300750",
                "name": "宁德时代",
                "debate_id": "debate-1",
                "score": 80,
                "adjusted_score": 82,
                "recommended_adjustment": "raise",
                "consensus": "先看量价延续",
                "research_verdict": "优先纸面复核",
                "primary_risk_gate": "确认承接",
                "next_trigger": "若继续放量则升级",
                "agent_views": [
                    {
                        "role": "bull",
                        "stance": "bullish",
                        "confidence": 0.8,
                        "arguments": ["主线继续强化。"],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    read_calls = {"count": 0}
    original_read_text = Path.read_text

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == debate_path:
            read_calls["count"] += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
        debate_results_path=str(debate_path),
    )

    provider.debate_summary(signal_date="2026-06-05", symbol="300750")
    provider.debate_summaries("2026-06-05")
    provider.prioritized_debate_summaries("2026-06-05", salient_only=True)

    assert read_calls["count"] == 1


def test_dashboard_data_provider_candidate_review_cards_return_full_same_day_pool(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": f"2026-06-05T15:{index:02d}:00+08:00",
            "symbol": f"00000{index}",
            "name": f"测试{index}",
            "score": 90 - index,
            "rating": "buy_candidate",
            "strategies": ["volume_breakout"],
        }
        for index in range(1, 10)
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    task_view = provider.build_task_view("main_chain", signal_date="2026-06-05")
    review_cards = provider.candidate_review_cards("2026-06-05")

    assert len(task_view.detail_cards) == 9
    assert len(review_cards) == 9
    assert "000009" in {card.symbol for card in task_view.detail_cards}
    assert "000009" in {card.symbol for card in review_cards}


def test_dashboard_data_provider_surfaces_closing_review_sections(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_rows = [
        {
            "id": "sig-a",
            "symbol": "600000",
            "name": "测试A",
            "strategies": ["morning_breakout"],
            "sub_strategy": "涨停打板",
            "signal_date": "2025-06-01",
            "entry_price": 10.0,
            "current_price": 9.5,
            "return_pct": 99.0,
            "holding_days": 1,
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
        },
        {
            "id": "sig-b",
            "symbol": "600001",
            "name": "测试B",
            "strategies": ["closing_premium"],
            "sub_strategy": "量价突破",
            "signal_date": "2025-06-01",
            "entry_price": 20.0,
            "current_price": 21.0,
            "return_pct": -99.0,
            "holding_days": 2,
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in ledger_rows) + "\n",
        encoding="utf-8",
    )
    paper_rows = [
        {
            "signal_id": "sig-a",
            "symbol": "600000",
            "name": "测试A",
            "signal_date": "2025-06-01",
            "entry_date": "2025-06-02",
            "exit_date": "2025-06-03",
            "status": "closed",
            "return_pct": -3.5,
        },
        {
            "signal_id": "sig-b",
            "symbol": "600001",
            "name": "测试B",
            "signal_date": "2025-06-01",
            "entry_date": "2025-06-02",
            "exit_date": "2025-06-04",
            "status": "closed",
            "return_pct": 5.0,
        },
    ]
    paper_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in paper_rows) + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    review_view = provider.build_task_view("closing_review", signal_date="2025-06-01")
    same_day_rows = provider.same_day_task_rows("2025-06-01")
    closing_row = next(row for row in same_day_rows if row.task_id == "closing_review")

    assert review_view.market_environment == "震荡市"
    assert review_view.report_markdown == ""
    assert closing_row.status_label == "已验证未归档"
    assert "2 笔已验证，胜率 50%，总收益 1.50%" in review_view.headline
    assert any(
        "事实来源: paper ledger closed=2" in item for item in review_view.summary_lines
    )
    assert any(
        "早盘打板·涨停打板" in item for item in review_view.strategy_breakdown_lines
    )
    assert any(
        "尾盘溢价·量价突破" in item for item in review_view.strategy_breakdown_lines
    )
    assert any("存在大亏平仓记录" in item for item in review_view.lesson_lines)
    assert review_view.improvement_lines == ()
    assert any(line.startswith("安排复核:") for line in review_view.agenda_lines)


def test_dashboard_data_provider_closing_review_uses_paper_facts_when_signal_rows_have_return_noise(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    signal_rows = [
        {
            "id": "sig-win",
            "status": "pending",
            "signal_date": "2026-05-27",
            "symbol": "600519",
            "name": "贵州茅台",
            "signal_close": 100.0,
            "rating": "buy_candidate",
            "score": 80,
            "strategies": ["momentum"],
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "horizon_days": 2,
            "fee_bps": 0,
            "slippage_bps": 0,
            "return_pct": -99.0,
        },
        {
            "id": "sig-blocked",
            "status": "pending",
            "signal_date": "2026-05-27",
            "symbol": "300750",
            "name": "宁德时代",
            "signal_close": 100.0,
            "rating": "buy_candidate",
            "score": 78,
            "strategies": ["momentum"],
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "horizon_days": 2,
            "fee_bps": 0,
            "slippage_bps": 0,
            "return_pct": 99.0,
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in signal_rows) + "\n",
        encoding="utf-8",
    )
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": "2026-05-27",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 1000,
                },
                {
                    "date": "2026-05-28",
                    "open": 101.0,
                    "high": 104.0,
                    "low": 100.0,
                    "close": 103.0,
                    "volume": 1000,
                },
                {
                    "date": "2026-05-29",
                    "open": 103.0,
                    "high": 105.0,
                    "low": 102.0,
                    "close": 104.0,
                    "volume": 1000,
                },
            ]
        ),
        "300750": pd.DataFrame(
            [
                {
                    "date": "2026-05-27",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 1000,
                },
                {
                    "date": "2026-05-28",
                    "open": 109.9,
                    "high": 109.9,
                    "low": 109.9,
                    "close": 109.9,
                    "volume": 1000,
                    "limit_up": 109.9,
                },
            ]
        ),
    }
    sync_paper_trades(
        signal_ledger=ledger_path,
        paper_ledger=paper_path,
        frames=frames,
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    review_view = provider.build_task_view("closing_review", signal_date="2026-05-27")

    assert "1 笔已验证，胜率 100%，总收益 2.97%" in review_view.headline
    assert review_view.candidate_count == 2
    assert review_view.actionable_count == 1
    assert review_view.blocked_count == 1
    assert any(
        "事实来源: paper ledger closed=1 / not_executable=1 / pending=0" == line
        for line in review_view.summary_lines
    )
    assert any("不可成交样本仅计入阻塞" in line for line in review_view.lesson_lines)
    assert all(
        "-99" not in line and "99" not in line for line in review_view.summary_lines
    )


def test_dashboard_data_provider_extracts_latest_report_when_main_chain_latest(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-06",
                "created_at": "2026-06-06T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 80,
                "rating": "buy_candidate",
                "run_requested_source": "auto",
                "run_actual_source": "csv",
                "run_source_health_label": "fallback",
                "run_source_health_message": "fallback 到 csv",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "latest.md").write_text(
        (
            "# AI 量化选股报告(close, 数据日期 2026-06-06)\n\n"
            "## 📌 执行摘要\n\n"
            "今日无纸面复核对象，仅观察。\n\n"
            "## 运行参数\n"
            "- 数据源: auto -> csv\n"
            "- 数据健康: fallback / fallback 到 csv\n"
            "- 规则版本: 1.1.1\n"
            "- regime: stable_sideways\n"
        ),
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    task_view = provider.build_task_view("main_chain", signal_date="2026-06-06")

    assert task_view.report_summary_lines == ("今日无纸面复核对象，仅观察。",)
    assert (
        "数据链路: 当前实际源 csv 只适合历史验证，盘中短线不可用（live_short=unknown）"
        in task_view.summary_lines
    )
    assert task_view.runtime_lines == (
        "数据来源: auto -> csv",
        "数据状态: 已切换备用源 / 已切换到备用数据源 csv",
        "规则版本: 1.1.1",
        "市场标签: stable_sideways",
    )


def test_dashboard_data_provider_sanitizes_archive_summary_action_words(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 80,
                "rating": "buy_candidate",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "latest.md").write_text(
        (
            "# AI 量化选股报告(close, 数据日期 2026-06-05)\n\n"
            "## 📌 执行摘要\n\n"
            "- 今日建议: 纸面复核对象进入纸面复核名单。\n"
            "- 配仓建议: 参考买点 1500，止损 1420，止盈 1680。\n\n"
            "## 明日重点\n\n"
            "- 首选观察 600519，若放量则新开仓，禁止下单只是测试词。\n"
        ),
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    task_view = provider.build_task_view("main_chain", signal_date="2026-06-05")
    visible_lines = task_view.report_summary_lines + task_view.next_day_focus_lines
    visible_text = "\n".join(visible_lines)

    for forbidden in (
        "今日建议",
        "可执行标的",
        "执行名单",
        "配仓建议",
        "参考买点",
        "止损",
        "止盈",
        "新开仓",
        "下单",
    ):
        assert forbidden not in visible_text
    assert "研究回看" in visible_text
    assert "纸面复核对象" in visible_text
    assert "纸面复核名单" in visible_text
    assert "仓位参考" in visible_text
    assert "参考价" in visible_text
    assert "最多亏到" in visible_text
    assert "先看目标" in visible_text
    assert "纸面新建观察" in visible_text
    assert "纸面记录" in visible_text


def test_dashboard_data_provider_skips_latest_report_when_date_mismatch(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 80,
                "rating": "buy_candidate",
                "run_requested_source": "auto",
                "run_actual_source": "eastmoney",
                "run_source_health_label": "healthy",
                "run_source_health_message": "eastmoney 健康",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "latest.md").write_text(
        (
            "# AI 量化选股报告(close, 数据日期 2026-06-06)\n\n"
            "## 📌 执行摘要\n\n"
            "这是次日最新报告，不应串到 2026-06-05 历史回看。\n"
        ),
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    task_view = provider.build_task_view("main_chain", signal_date="2026-06-05")

    assert task_view.report_markdown == ""
    assert task_view.report_summary_lines == ()


def test_dashboard_data_provider_dedupes_action_status_when_execution_focus_matches_portfolio_action(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 72,
                "rating": "buy_candidate",
                "portfolio_action": "keep",
                "candidate_status": "维持原排序",
                "candidate_review_window": "开盘前后",
                "candidate_review_priority": "high",
                "candidate_next_step": "等待承接确认后再决定是否前移顺位",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    focus = provider.execution_focus(signal_date="2026-06-05", symbol="600519")

    assert focus.research_lines[0] == "研究动作: 结果不变"
    assert not any("结果不变 / 结果不变" in line for line in focus.research_lines)


def test_dashboard_data_provider_formats_execution_focus_research_lines_when_candidate_context_exists(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "300750",
                "name": "宁德时代",
                "score": 88,
                "rating": "strong_buy_candidate",
                "portfolio_action": "promote",
                "candidate_status": "延续上升",
                "candidate_review_window": "开盘前后",
                "candidate_review_priority": "high",
                "candidate_next_step": "观察量能是否继续扩张，再决定是否维持主推",
                "cross_market_primary_theme": "海外物理AI叙事升温",
                "cross_market_action": "优先复核",
                "cross_market_chain_summary": (
                    "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
                    "确认 龙头封单增强｜失效 高开低走且量能背离"
                ),
                "cross_market_validation_signals": ["龙头封单增强"],
                "cross_market_invalidation_signals": ["高开低走且量能背离"],
                "support_points": ["量能承接仍在延续。"],
                "opposition_points": ["高位分歧依然偏大。"],
                "watch_items": ["观察次日承接是否继续。"],
                "role_reliability_lines": ["技术多头: 近21天 7/10 (70%)"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    focus = provider.execution_focus(signal_date="2026-06-05", symbol="300750")

    assert focus.research_lines[:4] == (
        "研究动作: 优先级上调 / 延续上升",
        "评分 88.0",
        "再看时间: 高优先级 / 开盘前后",
        "研究下一步: 观察量能是否继续扩张，再决定是否维持主推",
    )
    assert (
        "跨市逻辑: 海外物理AI叙事升温(优先复核) | 映射 产业映射｜领先窗 隔夜-3日｜先看 机器人整机"
        in focus.research_lines
    )
    assert "确认信号: 龙头封单增强" in focus.research_lines
    assert "失效信号: 高开低走且量能背离" in focus.research_lines
    assert "支持观点: 量能承接仍在延续。" in focus.research_lines
    assert "反对观点: 高位分歧依然偏大。" in focus.research_lines
    assert "待确认: 观察次日承接是否继续。" in focus.research_lines
    assert "角色可信度: 技术多头: 近21天 7/10 (70%)" in focus.research_lines


def test_dashboard_data_provider_execution_focus_uses_same_day_paper_execution_only(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 76,
                "rating": "buy_candidate",
                "portfolio_action": "promote",
                "candidate_status": "进入执行观察",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (logs_path / "2026-06-05.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "execution",
                        "timestamp": "2026-06-05T09:35:00+08:00",
                        "symbol": "000001",
                        "action": "BUY",
                        "shares": 200,
                        "price": 12.0,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "execution",
                        "timestamp": "2026-06-05T10:10:00+08:00",
                        "symbol": "600519",
                        "action": "BUY",
                        "shares": 100,
                        "price": 1500.0,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (logs_path / "2026-06-06.jsonl").write_text(
        json.dumps(
            {
                "type": "execution",
                "timestamp": "2026-06-06T09:40:00+08:00",
                "symbol": "600519",
                "action": "SELL",
                "shares": 100,
                "price": 1510.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    focus = provider.execution_focus(signal_date="2026-06-05", symbol="600519")

    assert focus.execution_status == "已有纸面验证"
    assert focus.execution_lines == (
        "最近纸面回写: 纸面入场 100 @ 1500.0 / 2026-06-05T10:10:00+08:00",
        "同日纸面验证日志 1 条。",
    )
    assert not any("SELL" in line or "000001" in line for line in focus.execution_lines)


def test_dashboard_data_provider_execution_focus_links_t_plus_one_execution_logs_to_signal_date(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 76,
                "rating": "buy_candidate",
                "portfolio_action": "promote",
                "candidate_status": "进入纸面验证",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text(
        json.dumps(
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "status": "open",
                "signal_date": "2026-06-05",
                "entry_date": "2026-06-08",
                "entry_price": 1500.0,
                "stop_loss": 1450.0,
                "take_profit": 1600.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (logs_path / "2026-06-08.jsonl").write_text(
        json.dumps(
            {
                "type": "execution",
                "timestamp": "2026-06-08T09:35:00+08:00",
                "symbol": "600519",
                "action": "BUY",
                "shares": 100,
                "price": 1500.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    focus = provider.execution_focus(signal_date="2026-06-05", symbol="600519")

    assert focus.execution_status == "已有纸面验证"
    assert focus.holding_status == "纸面持有跟踪中"
    assert focus.execution_lines == (
        "最近纸面回写: 纸面入场 100 @ 1500.0 / 2026-06-08T09:35:00+08:00",
        "关联纸面验证日志 1 条。",
    )
    assert any("最近入场: 2026-06-08" in line for line in focus.holding_lines)


def test_dashboard_data_provider_reads_archived_execution_logs_by_exact_date(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text("", encoding="utf-8")
    paper_path.write_text("", encoding="utf-8")
    (logs_path / "2026-05-01.jsonl").write_text(
        json.dumps(
            {
                "type": "execution",
                "timestamp": "2026-05-01T09:35:00+08:00",
                "symbol": "600519",
                "action": "BUY",
                "shares": 100,
                "price": 1500.0,
                "cost": 150000.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    archived_frame = provider.recent_execution_frame(
        limit=10,
        signal_date="2026-05-01",
    )

    assert list(archived_frame["代码"]) == ["600519"]
    assert provider.recent_execution_frame(limit=10, signal_date="2026-05-02").empty


def test_dashboard_data_provider_ignores_execution_log_when_timestamp_date_mismatches_file(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text("", encoding="utf-8")
    paper_path.write_text("", encoding="utf-8")
    (logs_path / "2026-06-05.jsonl").write_text(
        json.dumps(
            {
                "type": "execution",
                "timestamp": "2026-06-06T09:35:00+08:00",
                "symbol": "600519",
                "action": "BUY",
                "shares": 100,
                "price": 1500.0,
                "cost": 150000.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    assert provider.execution_logs_for_date("2026-06-05") == []
    assert provider.recent_execution_frame(limit=10, signal_date="2026-06-05").empty


def test_dashboard_data_provider_caches_execution_log_queries(tmp_path: Path) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    ledger_path.write_text("", encoding="utf-8")
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )
    calls: list[tuple[date, date]] = []

    def fake_query_logs(*, start_date: date, end_date: date):
        calls.append((start_date, end_date))
        return [
            {
                "type": "execution",
                "timestamp": f"{start_date.isoformat()}T09:35:00+08:00",
                "symbol": "600519",
                "action": "BUY",
                "shares": 100,
                "price": 1500.0,
            }
        ]

    provider.logger.query_logs = fake_query_logs  # type: ignore[method-assign]

    assert provider.execution_logs_for_date("2026-06-05")
    assert provider.execution_logs_for_date("2026-06-05")
    assert len(calls) == 1

    provider.get_recent_execution_logs(days=7)
    provider.get_recent_execution_logs(days=7)
    assert len(calls) == 2


def test_dashboard_data_provider_ignores_report_when_body_date_mismatches_filename(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "600519",
                "name": "贵州茅台",
                "score": 76,
                "rating": "buy_candidate",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "briefing-2026-06-05.md").write_text(
        "# 每日研究复盘-2026-06-06\n\n错日简报。\n",
        encoding="utf-8",
    )
    (reports_dir / "closing_review-2026-06-05.md").write_text(
        "📊 每日纸面验证复盘\n📅 日期: 2026-06-06\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    briefing_view = provider.build_task_view("briefing", signal_date="2026-06-05")
    closing_view = provider.build_task_view("closing_review", signal_date="2026-06-05")

    assert briefing_view.report_markdown == ""
    assert briefing_view.report_source == ""
    assert closing_view.report_markdown == ""
    assert closing_view.report_source == ""


def test_dashboard_data_provider_same_day_spotlight_uses_latest_phase_as_final_state(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T10:05:00+08:00",
            "symbol": "300750",
            "name": "宁德时代",
            "score": 60,
            "rating": "watch",
            "portfolio_action": "downgrade",
            "candidate_status": "观察阻塞",
            "candidate_blocker": "早盘量能不足",
            "candidate_next_step": "尾盘确认后再跟踪",
            "reasons": ["早盘资金回流"],
            "risks": ["早盘追高风险"],
            "strategies": ["morning_breakout"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:55:00+08:00",
            "symbol": "300750",
            "name": "宁德时代",
            "score": 73,
            "rating": "buy_candidate",
            "portfolio_action": "promote",
            "candidate_status": "尾盘恢复",
            "strategies": ["closing_premium"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T10:06:00+08:00",
            "symbol": "002594",
            "name": "比亚迪",
            "score": 70,
            "rating": "buy_candidate",
            "portfolio_action": "downgrade",
            "candidate_status": "早盘观察阻塞",
            "candidate_blocker": "早盘放量失败",
            "risks": ["早盘风险未解除"],
            "strategies": ["morning_breakout"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:56:00+08:00",
            "symbol": "002594",
            "name": "比亚迪",
            "score": 58,
            "rating": "watch",
            "portfolio_action": "downgrade",
            "candidate_status": "尾盘降级阻塞",
            "strategies": ["closing_premium"],
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    spotlights = {
        item.symbol: item
        for item in provider.same_day_candidate_spotlights("2026-06-05")
    }
    overview = provider.date_overview("2026-06-05")

    assert spotlights["300750"].action_label == "优先级上调"
    assert spotlights["300750"].blocker == ""
    assert spotlights["300750"].next_step == "早盘观察: 尾盘确认后再跟踪"
    assert spotlights["300750"].reasons == ("早盘观察: 早盘资金回流",)
    assert spotlights["300750"].risks == ("早盘观察: 早盘追高风险",)
    assert spotlights["300750"].task_labels == ("早盘策略", "尾盘策略")
    assert spotlights["002594"].action_label == "优先级下调"
    assert spotlights["002594"].status_label == "尾盘降级阻塞"
    assert spotlights["002594"].blocker == "早盘观察: 早盘放量失败"
    assert spotlights["002594"].risks == ("早盘观察: 早盘风险未解除",)
    review_cards = {
        item.symbol: item for item in provider.candidate_review_cards("2026-06-05")
    }
    assert review_cards["300750"].action_label == "优先级上调"
    assert review_cards["300750"].blocker == ""
    assert review_cards["300750"].reasons == ("早盘观察: 早盘资金回流",)
    assert review_cards["002594"].status_label == "尾盘降级阻塞"
    assert review_cards["002594"].blocker == "早盘观察: 早盘放量失败"
    assert overview.actionable_total == 1
    assert overview.blocked_total == 1


def test_dashboard_data_provider_same_day_spotlight_keeps_final_phase_evidence_unprefixed(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T10:05:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 78,
            "rating": "buy_candidate",
            "portfolio_action": "promote",
            "candidate_status": "早盘主线",
            "candidate_next_step": "早盘继续观察",
            "reasons": ["早盘资金回流"],
            "risks": ["早盘追高风险"],
            "strategies": ["morning_breakout"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:56:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 83,
            "rating": "buy_candidate",
            "portfolio_action": "promote",
            "candidate_status": "尾盘确认",
            "candidate_next_step": "尾盘确认后纳入次日复核",
            "news_catalyst_judgement": "supports",
            "news_catalyst_lead": "600519 贵州茅台 偏多｜消费复苏验证｜高端酒动销改善",
            "news_catalyst_source": "财联社",
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "重点跟踪",
            "reasons": ["尾盘承接确认"],
            "risks": ["尾盘高位波动"],
            "strategies": ["closing_premium"],
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    spotlight = provider.same_day_candidate_spotlights("2026-06-05")[0]
    review_card = provider.candidate_review_cards("2026-06-05")[0]

    assert spotlight.status_label == "尾盘确认"
    assert spotlight.next_step == "尾盘确认后纳入次日复核"
    assert spotlight.reasons == ("尾盘承接确认",)
    assert spotlight.risks == ("尾盘高位波动",)
    assert (
        spotlight.news_catalyst_summary
        == "消息支持: 600519 贵州茅台 偏多｜消费复苏验证｜高端酒动销改善｜财联社"
    )
    assert spotlight.cross_market_summary == "海外物理AI叙事升温(纸面复核)"
    assert review_card.status_label == "尾盘确认"
    assert (
        review_card.news_catalyst_summary
        == "消息支持: 600519 贵州茅台 偏多｜消费复苏验证｜高端酒动销改善｜财联社"
    )
    assert review_card.cross_market_summary == "海外物理AI叙事升温(纸面复核)"
    assert "跨市线索 海外物理AI叙事升温(纸面复核)" in review_card.decision_note
    assert review_card.reasons == ("尾盘承接确认",)
    assert review_card.risks == ("尾盘高位波动",)


def test_dashboard_data_provider_same_day_merge_carries_debate_summary_fields(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:30:00+08:00",
            "symbol": "600036",
            "name": "招商银行",
            "score": 74,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "main_chain",
            "portfolio_action": "keep",
            "candidate_fingerprint": "same-candidate",
            "debate_research_verdict": "倾向优先纸面复核",
            "debate_primary_risk_gate": "先确认银行板块承接",
            "debate_next_trigger": "先确认次日成交质量",
            "support_points": ["外盘风险偏好改善，对银行权重形成支撑"],
            "opposition_points": ["若只是单日脉冲，次日承接可能不足"],
            "watch_items": ["观察北向强弱是否在次日延续"],
            "role_reliability_lines": ["跨市场: 近21天 7/10 (70%)｜当前权重 0.18"],
            "debate_historical_context_note": "历史校验: 强证据 4/5 (80%)；冲突主导 1/3",
            "debate_historical_context_bucket": "strong_supportive",
            "debate_historical_context_sample_count": 5,
            "debate_historical_context_accuracy": 0.8,
            "cross_market_primary_theme": "美股风险偏好修复",
            "cross_market_action": "重点跟踪",
            "reasons": ["权重股修复"],
            "risks": ["承接分歧"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:55:00+08:00",
            "symbol": "600036",
            "name": "招商银行",
            "score": 76,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "closing_premium",
            "portfolio_action": "keep",
            "candidate_fingerprint": "same-candidate",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    spotlight = provider.same_day_candidate_spotlights("2026-06-05")[0]
    review_card = provider.candidate_review_cards("2026-06-05")[0]

    assert spotlight.next_step == "盘前主链: 先确认次日成交质量"
    assert spotlight.reasons == ("盘前主链: 权重股修复",)
    assert spotlight.risks == ("盘前主链: 承接分歧",)
    assert spotlight.support_points == (
        "盘前主链: 外盘风险偏好改善，对银行权重形成支撑",
    )
    assert spotlight.opposition_points == (
        "盘前主链: 若只是单日脉冲，次日承接可能不足",
    )
    assert spotlight.watch_items == ("盘前主链: 观察北向强弱是否在次日延续",)
    assert spotlight.cross_market_summary == "美股风险偏好修复(纸面复核)"
    assert spotlight.cross_market_validation_summary == ""
    assert spotlight.cross_market_invalidation_summary == ""
    assert (
        review_card.decision_note
        == "盘前主链: 倾向优先纸面复核，但先卡住 先确认银行板块承接；跨市线索 美股风险偏好修复(纸面复核)；历史校验: 强证据 4/5 (80%)；冲突主导 1/3"
    )
    assert review_card.next_step == "盘前主链: 先确认次日成交质量"
    unique_rows = provider._same_day_unique_rows("2026-06-05")
    assert unique_rows[0]["support_points"] == [
        "盘前主链: 外盘风险偏好改善，对银行权重形成支撑"
    ]
    assert unique_rows[0]["opposition_points"] == [
        "盘前主链: 若只是单日脉冲，次日承接可能不足"
    ]
    assert unique_rows[0]["watch_items"] == ["盘前主链: 观察北向强弱是否在次日延续"]
    assert unique_rows[0]["role_reliability_lines"] == [
        "盘前主链: 跨市场: 近21天 7/10 (70%)｜当前权重 0.18"
    ]


def test_dashboard_data_provider_same_day_merge_does_not_backfill_stale_debate_fields(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:30:00+08:00",
            "symbol": "600036",
            "name": "招商银行",
            "score": 74,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "main_chain",
            "portfolio_action": "keep",
            "debate_research_verdict": "倾向优先纸面复核",
            "debate_primary_risk_gate": "先确认银行板块承接",
            "debate_next_trigger": "先确认次日成交质量",
            "support_points": ["外盘风险偏好改善，对银行权重形成支撑"],
            "role_reliability_lines": ["跨市场: 近21天 7/10 (70%)"],
            "reasons": ["权重股修复"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:55:00+08:00",
            "symbol": "600036",
            "name": "招商银行",
            "score": 76,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "closing_premium",
            "portfolio_action": "keep",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    unique_rows = provider._same_day_unique_rows("2026-06-05")

    assert unique_rows[0]["reasons"] == ["盘前主链: 权重股修复"]
    assert "debate_research_verdict" not in unique_rows[0]
    assert "debate_primary_risk_gate" not in unique_rows[0]
    assert "debate_next_trigger" not in unique_rows[0]
    assert "support_points" not in unique_rows[0]
    assert "role_reliability_lines" not in unique_rows[0]


def test_dashboard_data_provider_same_day_spotlights_prioritize_discussion_ready_context(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:58:00+08:00",
            "symbol": "300750",
            "name": "宁德时代",
            "score": 82,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "closing_premium",
            "portfolio_action": "keep",
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:30:00+08:00",
            "symbol": "688256",
            "name": "寒武纪",
            "score": 79,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "main_chain",
            "portfolio_action": "keep",
            "debate_research_verdict": "倾向优先纸面复核",
            "debate_primary_risk_gate": "先确认算力链量能扩散",
            "debate_next_trigger": "若英伟达物理AI表述继续扩散则优先复核",
            "debate_historical_context_note": "历史校验: 强证据 4/5 (80%)；冲突主导 1/3",
            "debate_historical_context_bucket": "strong_supportive",
            "debate_historical_context_sample_count": 5,
            "debate_historical_context_accuracy": 0.8,
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "纸面复核",
            "cross_market_priority_score": 2.4,
            "cross_market_chain_summary": "英伟达物理AI -> 算力链映射 -> A股弹性标的扩散",
            "cross_market_evidence_stack_summary": "同向 2 条｜反向 1 条",
            "cross_market_validation_signals": ["龙头封单增强"],
            "cross_market_invalidation_signals": ["高开低走且量能背离"],
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    spotlights = provider.same_day_candidate_spotlights("2026-06-05")

    assert [item.symbol for item in spotlights[:2]] == ["688256", "300750"]
    assert spotlights[0].cross_market_validation_summary == "龙头封单增强"
    assert spotlights[0].cross_market_invalidation_summary == "高开低走且量能背离"
    assert (
        "同向 2 条｜反向 1 条"
        in provider.candidate_review_cards("2026-06-05")[0].decision_note
    )


def test_dashboard_data_provider_same_day_spotlights_prioritize_cross_market_promote(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:58:00+08:00",
            "symbol": "300750",
            "name": "宁德时代",
            "score": 88,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "closing_premium",
            "portfolio_action": "keep",
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:30:00+08:00",
            "symbol": "688256",
            "name": "寒武纪",
            "score": 70,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "main_chain",
            "portfolio_action": "promote",
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "优先复核",
            "cross_market_priority_score": 3,
            "cross_market_evidence_stack_summary": "同向 2 条｜反向 0 条",
            "cross_market_validation_signals": ["机器人龙头放量上攻"],
            "cross_market_invalidation_signals": ["只有海外叙事但A股不共振"],
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    spotlights = provider.same_day_candidate_spotlights("2026-06-05")
    review_cards = provider.candidate_review_cards("2026-06-05")

    assert [item.symbol for item in spotlights[:2]] == ["688256", "300750"]
    assert spotlights[0].action_label == "优先级上调"
    assert spotlights[0].cross_market_summary == (
        "海外物理AI叙事升温(优先复核)｜同向 2 条｜反向 0 条"
    )
    assert [card.symbol for card in review_cards[:2]] == ["688256", "300750"]
    assert review_cards[0].action_label == "优先级上调"


def test_dashboard_data_provider_same_day_spotlights_derive_validation_and_invalidation_from_chain_summary(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:30:00+08:00",
            "symbol": "688256",
            "name": "寒武纪",
            "score": 79,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "main_chain",
            "portfolio_action": "keep",
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "纸面复核",
            "cross_market_chain_summary": (
                "英伟达物理AI -> 算力链映射 -> A股弹性标的扩散｜"
                "确认 龙头封单增强｜"
                "失效 高开低走且量能背离｜"
                "同向 2 条｜反向 1 条"
            ),
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    spotlight = provider.same_day_candidate_spotlights("2026-06-05")[0]
    review_card = provider.candidate_review_cards("2026-06-05")[0]

    assert spotlight.cross_market_validation_summary == "龙头封单增强"
    assert spotlight.cross_market_invalidation_summary == "高开低走且量能背离"
    assert "确认信号 龙头封单增强" in review_card.decision_note
    assert "失效信号 高开低走且量能背离" in review_card.decision_note


def test_dashboard_data_provider_candidate_review_cards_prioritize_discussion_ready_context(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:56:00+08:00",
            "symbol": "300750",
            "name": "宁德时代",
            "score": 82,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "closing_premium",
            "portfolio_action": "keep",
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:32:00+08:00",
            "symbol": "688256",
            "name": "寒武纪",
            "score": 79,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "main_chain",
            "portfolio_action": "keep",
            "debate_research_verdict": "倾向优先纸面复核",
            "debate_primary_risk_gate": "先确认算力链量能扩散",
            "debate_next_trigger": "若英伟达物理AI表述继续扩散则优先复核",
            "debate_historical_context_note": "历史校验: 强证据 4/5 (80%)；冲突主导 1/3",
            "debate_historical_context_bucket": "strong_supportive",
            "debate_historical_context_sample_count": 5,
            "debate_historical_context_accuracy": 0.8,
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "纸面复核",
            "cross_market_priority_score": 2.4,
            "cross_market_chain_summary": "英伟达物理AI -> 算力链映射 -> A股弹性标的扩散",
            "cross_market_evidence_stack_summary": "同向 2 条｜反向 1 条",
            "cross_market_validation_signals": ["龙头封单增强"],
            "cross_market_invalidation_signals": ["高开低走且量能背离"],
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    review_cards = provider.candidate_review_cards("2026-06-05")
    task_view = provider.build_task_view("main_chain", signal_date="2026-06-05")

    assert [card.symbol for card in review_cards[:2]] == ["688256", "300750"]
    assert task_view.ranking_lines[0].startswith("第一顺位: 688256 寒武纪")
    assert "同向 2 条｜反向 1 条" in review_cards[0].decision_note
    assert (
        "传导链 英伟达物理AI -> 算力链映射 -> A股弹性标的扩散"
        in review_cards[0].decision_note
    )
    assert "确认信号 龙头封单增强" in review_cards[0].decision_note
    assert "失效信号 高开低走且量能背离" in review_cards[0].decision_note
    assert "历史校验: 强证据 4/5 (80%)；冲突主导 1/3" in review_cards[0].decision_note


def test_dashboard_data_provider_execution_focus_falls_back_to_same_day_signal_tasks_when_closing_review_context(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T10:05:00+08:00",
                "symbol": "300750",
                "name": "宁德时代",
                "score": 88,
                "rating": "strong_buy_candidate",
                "portfolio_action": "promote",
                "candidate_status": "延续上升",
                "candidate_review_window": "开盘前后",
                "candidate_review_priority": "high",
                "candidate_next_step": "观察量能是否继续扩张，再决定是否维持主推",
                "strategies": ["morning_breakout"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    focus = provider.execution_focus(
        signal_date="2026-06-05",
        symbol="300750",
        task_id="closing_review",
    )

    assert focus.research_status == "待确认"
    assert focus.research_lines[0] == "研究来源: 早盘策略 / 早盘观察"
    assert "研究链路缺席" not in focus.research_status


def test_dashboard_data_provider_execution_focus_uses_final_signal_state_when_closing_review_has_multiple_phases(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:20:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 82,
            "rating": "buy_candidate",
            "portfolio_action": "promote",
            "candidate_status": "早盘上调",
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:55:00+08:00",
            "symbol": "600519",
            "name": "贵州茅台",
            "score": 58,
            "rating": "watch",
            "portfolio_action": "downgrade",
            "candidate_status": "尾盘降级阻塞",
            "candidate_blocker": "尾盘放量失败",
            "strategies": ["closing_premium"],
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    focus = provider.execution_focus(
        signal_date="2026-06-05",
        symbol="600519",
        task_id="closing_review",
    )

    assert focus.research_status == "存在阻塞"
    assert focus.research_lines[0] == "研究来源: 尾盘策略 / 尾盘确认"
    assert "研究动作: 优先级下调 / 尾盘降级阻塞" in focus.research_lines
    assert "当前限制: 尾盘放量失败" in focus.research_lines


def test_dashboard_data_provider_execution_focus_uses_paper_context_when_signal_row_missing(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text("", encoding="utf-8")
    paper_path.write_text(
        json.dumps(
            {
                "signal_id": "sig-x",
                "symbol": "300750",
                "name": "宁德时代",
                "signal_date": "2026-06-05",
                "status": "pending_entry",
                "portfolio_action": "downgrade",
                "candidate_status": "观察阻塞",
                "candidate_blocker": "涨停无法追入",
                "candidate_next_step": "等待开板后再评估",
                "candidate_review_window": "次日开盘",
                "candidate_review_priority": "high",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    focus = provider.execution_focus(signal_date="2026-06-05", symbol="300750")

    assert focus.research_status == "存在阻塞"
    assert "研究动作: 优先级下调 / 观察阻塞" in focus.research_lines
    assert "再看时间: 高优先级 / 次日开盘" in focus.research_lines
    assert "研究下一步: 等待开板后再评估" in focus.research_lines
    assert any("涨停无法追入" in line for line in focus.readiness_lines)


def test_dashboard_data_provider_derives_blocker_from_risks_for_downgraded_candidate(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T15:00:00+08:00",
                "symbol": "000338",
                "name": "潍柴动力",
                "score": 58,
                "rating": "buy_candidate",
                "portfolio_action": "downgrade",
                "candidate_status": "降级观察",
                "risks": ["20日均成交额不足，流动性过滤", "MACD 动能走弱"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    view = provider.build_task_view("main_chain", signal_date="2026-06-05")
    focus = provider.execution_focus(signal_date="2026-06-05", symbol="000338")

    assert view.detail_cards[0].blocker == "20日均成交额不足，流动性过滤"
    assert view.detail_cards[0].decision_note == "20日均成交额不足，流动性过滤"
    assert any(
        "000338 潍柴动力 | 当前限制: 20日均成交额不足，流动性过滤" == line
        for line in view.unlock_lines
    )
    assert any(
        "研究已产出，但当前被20日均成交额不足，流动性过滤拦住，暂不进入纸面入场验证链路。"
        == line
        for line in focus.readiness_lines
    )


def test_dashboard_data_provider_candidate_research_context_prefers_matching_task_then_falls_back(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-06-05",
                        "created_at": "2026-06-05T15:00:00+08:00",
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "score": 80,
                        "rating": "buy_candidate",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "signal_date": "2026-06-05",
                        "created_at": "2026-06-05T10:05:00+08:00",
                        "symbol": "300750",
                        "name": "宁德时代",
                        "score": 88,
                        "rating": "strong_buy_candidate",
                        "strategies": ["morning_breakout"],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    main_chain_context = provider.candidate_research_context(
        signal_date="2026-06-05",
        symbol="600519",
        preferred_task_id="main_chain",
    )
    fallback_context = provider.candidate_research_context(
        signal_date="2026-06-05",
        symbol="300750",
        preferred_task_id="main_chain",
    )

    assert main_chain_context is not None
    assert main_chain_context["task_id"] == "main_chain"
    assert fallback_context is not None
    assert fallback_context["task_id"] == "morning_breakout"


def test_dashboard_data_provider_candidate_research_context_merges_same_day_debate_fields_across_tasks(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    rows = [
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T09:30:00+08:00",
            "symbol": "600036",
            "name": "招商银行",
            "score": 74,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "main_chain",
            "portfolio_action": "keep",
            "candidate_fingerprint": "same-candidate",
            "debate_research_verdict": "倾向优先纸面复核",
            "debate_primary_risk_gate": "先确认银行板块承接",
            "debate_next_trigger": "先确认次日成交质量",
            "support_points": ["外盘风险偏好改善，对银行权重形成支撑"],
            "opposition_points": ["若只是单日脉冲，次日承接可能不足"],
            "watch_items": ["观察北向强弱是否在次日延续"],
            "role_reliability_lines": ["跨市场: 近21天 7/10 (70%)｜当前权重 0.18"],
        },
        {
            "signal_date": "2026-06-05",
            "created_at": "2026-06-05T14:55:00+08:00",
            "symbol": "600036",
            "name": "招商银行",
            "score": 76,
            "rating": "buy_candidate",
            "status": "pending",
            "task_id": "closing_premium",
            "portfolio_action": "keep",
            "candidate_fingerprint": "same-candidate",
        },
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    context = provider.candidate_research_context(
        signal_date="2026-06-05",
        symbol="600036",
        preferred_task_id="closing_premium",
    )

    assert context is not None
    assert context["task_id"] == "closing_premium"
    assert context["row"]["support_points"] == [
        "盘前主链: 外盘风险偏好改善，对银行权重形成支撑"
    ]
    assert context["row"]["opposition_points"] == [
        "盘前主链: 若只是单日脉冲，次日承接可能不足"
    ]
    assert context["row"]["watch_items"] == ["盘前主链: 观察北向强弱是否在次日延续"]
    assert context["row"]["role_reliability_lines"] == [
        "盘前主链: 跨市场: 近21天 7/10 (70%)｜当前权重 0.18"
    ]


def test_dashboard_data_provider_backfills_debate_active_roles_from_debate_results(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    debate_path = tmp_path / "debate_results.jsonl"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-06-05",
                "created_at": "2026-06-05T14:55:00+08:00",
                "symbol": "300750",
                "name": "宁德时代",
                "score": 82,
                "rating": "buy_candidate",
                "status": "pending",
                "task_id": "closing_premium",
                "portfolio_action": "keep",
                "debate_research_verdict": "倾向优先纸面复核",
                "debate_primary_risk_gate": "先确认机器人量能扩散",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "debate-roles",
                "symbol": "300750",
                "name": "宁德时代",
                "related_signal_date": "2026-06-05",
                "created_at": "2026-06-05T21:00:00+08:00",
                "final_vote": {
                    "bull": "bullish",
                    "risk_control": "neutral",
                    "cross_market": "bullish",
                },
                "rounds": [
                    {
                        "round_num": 2,
                        "opinions": [
                            {
                                "role": "bull",
                                "stance": "bullish",
                                "confidence": 0.8,
                                "arguments": ["量价承接继续强化。"],
                            },
                            {
                                "role": "risk_control",
                                "stance": "neutral",
                                "confidence": 0.7,
                                "arguments": ["先确认追高回撤风险。"],
                            },
                            {
                                "role": "cross_market",
                                "stance": "bullish",
                                "confidence": 0.82,
                                "arguments": ["海外物理AI主线仍在扩散。"],
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
        debate_results_path=str(debate_path),
    )

    context = provider.candidate_research_context(
        signal_date="2026-06-05",
        symbol="300750",
        preferred_task_id="closing_premium",
    )
    focus = provider.execution_focus(
        signal_date="2026-06-05",
        symbol="300750",
        task_id="closing_premium",
    )

    assert context is not None
    assert context["row"]["debate_active_role_summary"] == "技术多头、风控、跨市传导"
    assert "讨论视角: 技术多头、风控、跨市传导" in focus.research_lines


def test_dashboard_data_provider_returns_readable_historical_source_fallback_when_selected_day_has_no_meta(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    logs_path.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    ledger_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-06-05",
                        "created_at": "2026-06-05T10:05:00+08:00",
                        "symbol": "300750",
                        "name": "宁德时代",
                        "score": 61,
                        "rating": "buy_candidate",
                        "strategies": ["morning_breakout"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "signal_date": "2026-06-04",
                        "created_at": "2026-06-04T15:00:00+08:00",
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "score": 80,
                        "rating": "buy_candidate",
                        "run_requested_source": "auto",
                        "run_actual_source": "eastmoney",
                        "run_source_health_label": "healthy",
                        "run_source_health_message": "eastmoney 健康",
                        "run_data_latest_trade_date": "2026-06-04",
                        "run_data_lag_days": 0,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    source_status = provider.latest_source_status(
        task_id="morning_breakout",
        signal_date="2026-06-05",
    )

    assert source_status["requested_source"] == "未记录"
    assert source_status["actual_source"] == "未记录"
    assert source_status["health_label"] == "历史记录缺字段"
    assert "未写入数据源元信息" in source_status["health_message"]
    assert source_status["data_latest_trade_date"] == "未记录"
    assert source_status["lag_days"] == "未记录"
    assert "T" in source_status["updated_at"]


def test_dashboard_data_provider_invalidates_cache_when_intraday_csv_changes(
    tmp_path: Path,
) -> None:
    latest_path = tmp_path / "intraday_latest.csv"
    pd.DataFrame(
        [{"symbol": "600900", "name": "长江电力", "date": "2026-07-10", "score": 55}]
    ).to_csv(latest_path, index=False)
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        intraday_ledger_path=str(tmp_path / "intraday.jsonl"),
        intraday_latest_path=str(latest_path),
        debate_results_path=str(tmp_path / "debates.jsonl"),
    )

    assert {row["symbol"] for row in provider.load_signal_rows()} == {"600900"}

    pd.DataFrame(
        [
            {
                "symbol": "__RUN__",
                "name": "run_event",
                "date": "2026-07-10",
                "run_task_id": "intraday",
                "run_market_context_lines": "美股科技走强；A股算力链观察承接",
            },
            {"symbol": "603019", "name": "中科曙光", "date": "2026-07-10", "score": 69},
        ]
    ).to_csv(latest_path, index=False)

    refreshed = provider.load_signal_rows()

    assert {row["symbol"] for row in refreshed} == {"__RUN__", "603019"}
    run_row = next(row for row in refreshed if row["symbol"] == "__RUN__")
    assert run_row["run_market_context_lines"] == "美股科技走强；A股算力链观察承接"


def test_dashboard_data_provider_merges_debate_into_intraday_cards_and_dates(
    tmp_path: Path,
) -> None:
    latest_path = tmp_path / "intraday_latest.csv"
    debate_path = tmp_path / "debates.jsonl"
    pd.DataFrame(
        [
            {
                "symbol": "603019",
                "name": "中科曙光",
                "date": "2026-07-10",
                "score": 69,
                "rating": "buy_candidate",
                "reasons": "量价趋势改善",
            }
        ]
    ).to_csv(latest_path, index=False)
    debate_path.write_text(
        json.dumps(
            {
                "debate_id": "intraday-603019",
                "symbol": "603019",
                "name": "中科曙光",
                "related_signal_date": "2026-07-10",
                "created_at": "2026-07-10T14:30:00+08:00",
                "original_score": 69,
                "adjusted_score": 69,
                "recommended_adjustment": "keep",
                "final_consensus": "先观察承接",
                "final_vote": {"cross_market": "bullish", "risk_control": "neutral"},
                "research_verdict": "海外算力映射增强，但不追高",
                "primary_risk_gate": "竞价承接不足",
                "next_trigger": "放量站稳日内高点后复核",
                "support_points": ["海外算力主线继续扩散"],
                "opposition_points": ["高开低走风险"],
                "watch_items": ["观察量能承接"],
                "market_context_lines": ["纳指科技板块走强"],
                "debate_context_quality": "structured_context",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        intraday_latest_path=str(latest_path),
        intraday_ledger_path=str(tmp_path / "intraday.jsonl"),
        debate_results_path=str(debate_path),
    )

    assert "2026-07-10" in provider.dashboard_dates()
    card = provider.build_task_view("intraday", "2026-07-10").detail_cards[0]
    spotlight = provider.same_day_candidate_spotlights("2026-07-10")[0]

    assert card.next_step == "放量站稳日内高点后复核"
    assert "海外算力映射增强" in card.decision_note
    assert "竞价承接不足" in card.decision_note
    assert spotlight.support_points == ("海外算力主线继续扩散",)
    assert spotlight.opposition_points == ("高开低走风险",)


def test_live_candidate_view_dedupes_and_orders_actionable_watch_blocked() -> None:
    rows = [
        {
            "symbol": "1",
            "name": "甲",
            "score": "81",
            "rating": "buy_candidate",
            "created_at": "2026-07-13T14:59:00+08:00",
            "reasons": "量价齐升",
            "candidate_next_step": "确认承接",
            "run_actual_source": "sina",
        },
        {
            "symbol": "000001",
            "name": "甲",
            "score": "82",
            "rating": "buy_candidate",
            "created_at": "2026-07-13T15:00:00+08:00",
            "reasons": "量价齐升",
            "candidate_next_step": "确认承接",
            "run_actual_source": "sina",
        },
        {
            "symbol": "000002",
            "name": "乙",
            "score": "90",
            "rating": "buy_candidate",
            "candidate_blocker": "涨停无法追入",
            "created_at": "2026-07-13T15:00:00+08:00",
        },
        {
            "symbol": "000003",
            "name": "丙",
            "score": "79",
            "rating": "watch",
            "created_at": "2026-07-13T15:00:00+08:00",
        },
        {
            "symbol": "000004",
            "name": "丁",
            "score": "78",
            "rating": "watch",
            "created_at": "2026-07-13T15:00:00+08:00",
        },
    ]

    view = build_live_candidate_view(
        rows,
        metadata=LiveArtifactMetadata(
            artifact_date="2026-07-13",
            updated_at="2026-07-13T15:05:00+08:00",
        ),
        now=datetime(2026, 7, 13, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert view.status == "fresh"
    assert len(view.candidates) == 3
    assert [item.symbol for item in view.candidates] == [
        "000001",
        "000003",
        "000004",
    ]
    assert view.candidates[0].score == 82.0
    assert view.candidates[0].rating == "buy_candidate"
    assert view.actionable_count == 1
    assert view.watch_count == 2
    assert view.blocked_count == 1


def test_live_candidate_view_marks_old_artifact_stale_without_relabeling_score() -> (
    None
):
    view = build_live_candidate_view(
        (
            {
                "symbol": "600519",
                "score": "91",
                "rating": "strong_buy_candidate",
            },
        ),
        metadata=LiveArtifactMetadata(
            artifact_date="2026-07-12",
            updated_at="2026-07-13T14:59:00+08:00",
        ),
        now=datetime(2026, 7, 13, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        config=LiveCandidateViewConfig(),
    )

    assert view.status == "stale"
    assert "产物日期为 2026-07-12" in view.stale_reason
    assert view.candidates[0].score == 91.0
    assert view.candidates[0].rating == "strong_buy_candidate"
    assert view.candidates[0].freshness_label == "数据已过期"


def test_live_candidate_view_filters_rows_to_requested_date_when_csv_contains_multiple_days() -> None:
    view = build_live_candidate_view(
        (
            {
                "symbol": "600001",
                "date": "2026-07-13",
                "score": 90,
                "rating": "buy_candidate",
            },
            {
                "symbol": "600002",
                "date": "2026-07-12",
                "score": 99,
                "rating": "strong_buy_candidate",
            },
        ),
        metadata=LiveArtifactMetadata(
            artifact_date="2026-07-13",
            updated_at="2026-07-13T15:00:00+08:00",
        ),
        now=datetime(2026, 7, 13, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        requested_date="2026-07-13",
    )

    assert [candidate.symbol for candidate in view.candidates] == ["600001"]


def test_live_candidate_view_ignores_old_run_failure_when_current_rows_are_fresh() -> None:
    view = build_live_candidate_view(
        (
            {
                "symbol": "600001",
                "date": "2026-07-13",
                "score": 90,
                "rating": "buy_candidate",
            },
            {
                "symbol": "__RUN__",
                "date": "2026-07-12",
                "source_status": "failed",
            },
        ),
        metadata=LiveArtifactMetadata(
            artifact_date="2026-07-13",
            updated_at="2026-07-13T15:00:00+08:00",
        ),
        now=datetime(2026, 7, 13, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        requested_date="2026-07-13",
    )

    assert view.status == "fresh"
    assert view.candidates[0].status == "actionable"


def test_dashboard_detail_card_exposes_deterministic_score_breakdown(
    tmp_path: Path,
) -> None:
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        intraday_ledger_path=str(tmp_path / "intraday.jsonl"),
    )

    cards = provider._build_detail_cards(
        [
            {
                "symbol": "600001",
                "name": "示例",
                "score": 72.0,
                "rating": "buy_candidate",
                "reasons": ["量价确认"],
                "strategies": ["rps_momentum"],
                "score_breakdown": {
                    "rps_momentum": {
                        "raw_score": 18.0,
                        "weight": 0.5,
                        "weighted_score": 9.0,
                    }
                },
            }
        ],
        task_id="intraday",
    )

    assert cards[0].score_breakdown == ("rps_momentum +9.0 (原始 18.0 × 0.50)",)


def test_dashboard_data_provider_live_view_caps_intraday_csv_and_exposes_card_evidence(
    tmp_path: Path,
) -> None:
    latest_path = tmp_path / "intraday_latest.csv"
    rows = [
        {
            "symbol": f"600{index:03d}",
            "name": f"候选{index}",
            "date": "2026-07-13",
            "score": 90 - index,
            "rating": "buy_candidate" if index < 4 else "watch",
            "reasons": "量价确认" if index < 4 else "",
            "candidate_blocker": "组合保护" if index == 3 else "",
            "close": 52.31 if index == 0 else "",
            "ret5_pct": 4.25 if index == 0 else "",
            "ret20_pct": 12.8 if index == 0 else "",
            "volume_ratio": 1.42 if index == 0 else "",
            "rsi12": 63.7 if index == 0 else "",
            "bias20_pct": 3.1 if index == 0 else "",
            "stop_loss": 48.6 if index == 0 else "",
            "take_profit": 59.4 if index == 0 else "",
            "data_source": "eastmoney" if index == 0 else "",
            "data_fetched_at": ("2026-07-13T15:09:00+08:00" if index == 0 else ""),
            "data_timestamp_source": "bar_time" if index == 0 else "",
            "freshness": "fresh" if index == 0 else "",
            "run_actual_source": "multi",
        }
        for index in range(10)
    ]
    pd.DataFrame(rows).to_csv(latest_path, index=False)
    now = datetime(2026, 7, 13, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    os.utime(latest_path, (now.timestamp(), now.timestamp()))
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        intraday_ledger_path=str(tmp_path / "intraday.jsonl"),
        intraday_latest_path=str(latest_path),
    )

    view = provider.live_candidate_view(now=now)
    spotlights = provider.live_candidate_spotlights(now=now)
    cards = provider._build_detail_cards(rows[:1], task_id="intraday")

    assert len(view.candidates) == 3
    assert [item.status for item in view.candidates] == [
        "actionable",
        "actionable",
        "actionable",
    ]
    assert len(spotlights) == 3
    assert all("新鲜度: 新鲜" in item.review_meta for item in spotlights)
    assert all("证据质量:" in item.review_meta for item in spotlights)
    assert spotlights[0].data_source == "eastmoney"
    assert spotlights[0].data_fetched_at == "2026-07-13T15:09:00+08:00"
    assert spotlights[0].data_timestamp_source == "bar_time"
    assert spotlights[0].freshness == "fresh"
    assert spotlights[0].close == 52.31
    assert spotlights[0].ret5_pct == 4.25
    assert spotlights[0].ret20_pct == 12.8
    assert spotlights[0].volume_ratio == 1.42
    assert spotlights[0].rsi12 == 63.7
    assert spotlights[0].bias20_pct == 3.1
    assert spotlights[0].stop_loss == 48.6
    assert spotlights[0].take_profit == 59.4
    assert spotlights[1].ret5_pct is None
    assert cards[0].data_source == "eastmoney"
    assert cards[0].data_fetched_at == "2026-07-13T15:09:00+08:00"
    assert cards[0].data_timestamp_source == "bar_time"
    assert cards[0].freshness == "fresh"


def test_dashboard_data_provider_uses_nested_freshness_when_sidecar_partially_fails(
    monkeypatch, tmp_path: Path
) -> None:
    latest_path = tmp_path / "intraday_latest.csv"
    pd.DataFrame(
        [
            {
                "symbol": "600276",
                "name": "恒瑞医药",
                "date": "2026-07-16",
                "score": 82,
                "rating": "buy_candidate",
                "reasons": "量价确认",
            }
        ]
    ).to_csv(latest_path, index=False)
    status_path = tmp_path / "intraday_refresh_status.json"
    status_path.write_text(
        json.dumps({"status": "partial_failed", "freshness": {"status": "fresh"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_INTRADAY_STATUS", str(status_path))
    now = datetime(2026, 7, 16, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    os.utime(latest_path, (now.timestamp(), now.timestamp()))
    provider = DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        intraday_ledger_path=str(tmp_path / "intraday.jsonl"),
        intraday_latest_path=str(latest_path),
    )

    assert provider.live_candidate_view(now=now).status == "fresh"
    assert provider.live_candidate_spotlights(now=now)[0].action_label != "数据不可用"


def test_live_candidate_view_quality_gate_blocks_failed_high_rating_rows() -> None:
    rows = [
        {
            "symbol": f"60100{index}",
            "score": 90,
            "rating": "strong_buy_candidate",
            "data_quality_status": status,
        }
        for index, status in enumerate(("blocked", "error", "failed"))
    ]

    view = build_live_candidate_view(
        rows,
        metadata=LiveArtifactMetadata(
            artifact_date="2026-07-13",
            updated_at="2026-07-13T15:05:00+08:00",
        ),
        now=datetime(2026, 7, 13, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert [item.status for item in view.candidates] == [
        "blocked",
        "blocked",
        "blocked",
    ]
    assert all(item.blocker.startswith("数据质量状态:") for item in view.candidates)


def test_live_candidate_view_quality_watch_and_execution_risk_override_rating() -> None:
    rows = [
        {
            "symbol": "601001",
            "score": 95,
            "rating": "strong_buy_candidate",
            "data_quality_status": "watch",
        },
        {
            "symbol": "601002",
            "score": 94,
            "rating": "strong_buy_candidate",
            "data_quality_alerts": ["盘口延迟"],
        },
        {
            "symbol": "601003",
            "score": 93,
            "rating": "strong_buy_candidate",
            "risks": "涨停风险",
        },
        {
            "symbol": "601004",
            "score": 92,
            "rating": "strong_buy_candidate",
            "executable": False,
        },
    ]

    view = build_live_candidate_view(
        rows,
        metadata=LiveArtifactMetadata(
            artifact_date="2026-07-13",
            updated_at="2026-07-13T15:05:00+08:00",
        ),
        now=datetime(2026, 7, 13, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
        config=LiveCandidateViewConfig(max_candidates=4),
    )

    by_symbol = {item.symbol: item for item in view.candidates}
    assert by_symbol["601001"].status == "watch"
    assert by_symbol["601002"].status == "watch"
    assert by_symbol["601003"].status == "blocked"
    assert by_symbol["601004"].status == "blocked"
    assert "数据质量告警" in by_symbol["601002"].blocker
    assert "涨停风险" in by_symbol["601003"].blocker
    assert by_symbol["601004"].blocker == "数据标记不可成交"


def test_live_candidate_view_quality_gate_observe_is_watch_not_blocked() -> None:
    view = build_live_candidate_view(
        [
            {
                "symbol": "600879",
                "score": 78,
                "rating": "strong_buy_candidate",
                "portfolio_action": "downgrade",
                "quality_gate_action": "observe",
                "candidate_status": "质量观察",
            }
        ],
        metadata=LiveArtifactMetadata(
            artifact_date="2026-07-13",
            updated_at="2026-07-13T15:05:00+08:00",
        ),
        now=datetime(2026, 7, 13, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert view.candidates[0].status == "watch"
