from __future__ import annotations

import json
from pathlib import Path

from aqsp.web.data_provider import DashboardDataProvider


def test_dashboard_data_provider_reads_real_runtime_files(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True)

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

    paper_events = provider.paper_events_frame(limit=10)
    assert "状态" in paper_events.columns
    assert "open" in set(paper_events["状态"])
    assert "not_executable" in set(paper_events["状态"])

    executions = provider.recent_execution_frame(limit=10)
    assert list(executions["代码"]) == ["600519"]

    source_status = provider.latest_source_status()
    assert source_status["actual_source"] == "eastmoney"
    assert source_status["health_label"] == "fallback"


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
            "candidate_next_step": "等待板块暴露回落后，再重新评估执行顺位",
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
    ]
    ledger_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in ledger_rows) + "\n",
        encoding="utf-8",
    )
    paper_path.write_text("", encoding="utf-8")
    (reports_dir / "briefing-2026-06-05.md").write_text(
        "# AI 量化选股日报 - 2026-06-05\n\n## 明日重点\n\n- **600519 贵州茅台**\n",
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
        "morning_breakout",
        "closing_premium",
        "closing_review",
        "briefing",
    ]

    main_view = provider.build_task_view("main_chain", signal_date="2026-06-05")
    assert main_view.actionable_count == 1
    assert main_view.watch_count == 1
    assert main_view.blocked_count == 1
    assert "贵州茅台" in main_view.headline
    assert any("板块集中度过高" in item for item in main_view.blocker_lines)
    assert any("开盘前后" in item for item in main_view.review_lines)
    assert main_view.detail_cards[0].display_name == "600519 贵州茅台"
    assert main_view.detail_cards[0].rank_label == "首选"
    assert "上调优先级" in main_view.detail_cards[0].decision_note
    assert main_view.detail_cards[0].reasons == ("量价齐升", "接近新高")
    assert main_view.detail_cards[0].risks == ("追高波动",)
    assert main_view.ranking_lines[0].startswith("首选: 600519 贵州茅台")
    assert any(line.startswith("阻塞观察: 000001 平安银行") for line in main_view.ranking_lines)

    morning_view = provider.build_task_view("morning_breakout", signal_date="2026-06-05")
    assert morning_view.task_label == "早盘策略"
    assert morning_view.candidate_count == 1
    assert morning_view.actionable_count == 1

    closing_view = provider.build_task_view("closing_premium", signal_date="2026-06-05")
    assert closing_view.task_label == "尾盘策略"
    assert closing_view.candidate_count == 1

    briefing_view = provider.build_task_view("briefing", signal_date="2026-06-05")
    assert briefing_view.task_label == "简报回看"
    assert "明日重点" in briefing_view.report_markdown

    latest_signals = provider.latest_signal_frame(
        limit=10,
        task_id="main_chain",
        signal_date="2026-06-05",
    )
    assert list(latest_signals["代码"]) == ["600519", "000001"]
    assert latest_signals.iloc[0]["候选状态"] == "延续上升"


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
            "symbol": "600000",
            "name": "测试A",
            "strategies": ["morning_breakout"],
            "sub_strategy": "涨停打板",
            "signal_date": "2025-06-01",
            "entry_price": 10.0,
            "current_price": 9.5,
            "return_pct": -5.0,
            "holding_days": 1,
            "run_requested_source": "auto",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "healthy",
            "run_source_health_message": "eastmoney 健康",
        },
        {
            "symbol": "600001",
            "name": "测试B",
            "strategies": ["closing_premium"],
            "sub_strategy": "量价突破",
            "signal_date": "2025-06-01",
            "entry_price": 20.0,
            "current_price": 21.0,
            "return_pct": 5.0,
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
    paper_path.write_text("", encoding="utf-8")

    provider = DashboardDataProvider(
        ledger_path=str(ledger_path),
        paper_ledger_path=str(paper_path),
        logs_path=str(logs_path),
        reports_dir=str(reports_dir),
    )

    review_view = provider.build_task_view("closing_review", signal_date="2025-06-01")

    assert review_view.market_environment == "震荡市"
    assert any("早盘打板·涨停打板" in item for item in review_view.strategy_breakdown_lines)
    assert any("尾盘溢价·量价突破" in item for item in review_view.strategy_breakdown_lines)
    assert any("打板成功率偏低" in item for item in review_view.lesson_lines)
    assert review_view.improvement_lines == ()
