from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from aqsp.paper import sync_paper_trades
from aqsp.web.data_provider import DashboardDataProvider


def test_dashboard_data_provider_reads_real_runtime_files(tmp_path: Path) -> None:
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

    source_status = provider.latest_source_status()
    assert source_status["actual_source"] == "eastmoney"
    assert source_status["health_label"] == "fallback"

    historical_source_status = provider.latest_source_status(signal_date="2026-06-04")
    assert historical_source_status["requested_source"] == "未记录"
    assert historical_source_status["actual_source"] == "未记录"
    assert historical_source_status["health_label"] == "历史记录缺字段"
    assert historical_source_status["data_latest_trade_date"] == "2026-06-04"

    task_snapshots = provider.task_snapshots()
    assert task_snapshots[0].task_id == "main_chain"
    assert task_snapshots[0].status_label == "有推荐"
    assert task_snapshots[-1].task_id == "briefing"
    assert task_snapshots[-1].status_label == "暂无结果"

    paper_summary = provider.paper_summary()
    assert paper_summary.signal_date == ""
    assert paper_summary.open_positions == 1
    assert paper_summary.pending_entries == 1
    assert paper_summary.not_executable == 1
    assert paper_summary.closed_trades == 1
    assert any("贵州茅台" in line for line in paper_summary.open_position_lines)
    assert any("纸面入场假设 1 笔" in line for line in paper_summary.event_lines)
    assert any("不可成交 1 笔" in line for line in paper_summary.event_lines)
    assert any(
        "最近纸面关闭: 000858 五粮液" in line for line in paper_summary.event_lines
    )
    assert any(
        "最近纸面回写: 600519 贵州茅台 | BUY 100 @ 1500.0" in line
        for line in paper_summary.action_summary_lines
    )
    assert any(
        "纸面入场待核对 1 笔" in line
        for line in paper_summary.action_summary_lines
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
    assert open_focus.research_status == "研究结论已落盘"
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
                        "adjustment_reason": "多头略占优",
                        "risk_warnings": ["高位波动放大"],
                        "opportunity_highlights": ["主线延续"],
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
    assert summary.recommended_adjustment_label == "建议上调评分"
    assert summary.consensus == "维持主推，但控制追高节奏"
    assert summary.summary_lines[0] == "建议上调评分: 80.0 -> 82.0"
    assert summary.agent_views[0].role_label in {"技术多头", "风控"}
    assert summary.risk_warnings == ("高位波动放大",)

    summaries = provider.debate_summaries("2026-06-05")
    assert [item.symbol for item in summaries] == ["600519", "002594"]


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
            "# AI 量化选股日报 - 2026-06-05\n\n"
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
            "- thresholds.version: 1.1.1\n"
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
    assert main_view.detail_cards[0].rank_label == "首选"
    assert "上调优先级" in main_view.detail_cards[0].decision_note
    assert main_view.detail_cards[0].reasons == ("量价齐升", "接近新高")
    assert main_view.detail_cards[0].risks == ("追高波动",)
    assert main_view.ranking_lines[0].startswith("首选: 600519 贵州茅台")
    assert any(
        line.startswith("阻塞观察: 000001 平安银行") for line in main_view.ranking_lines
    )
    assert main_view.report_summary_lines == (
        "今日主链 1 只可执行，另有 1 只转观察。",
        "优先跟踪高分主链候选。",
    )
    assert main_view.report_source.endswith("latest.md")
    assert "T" in main_view.report_mtime
    assert main_view.lifecycle_lines[0].startswith(
        "600519 贵州茅台 | 上调优先级 | 延续上升"
    )
    assert any(
        "000001 平安银行 | 当前卡点: 板块集中度过高" in line
        for line in main_view.unlock_lines
    )
    assert main_view.previous_date == "2026-06-04"
    assert main_view.runtime_lines[0] == "数据源: auto -> eastmoney"
    assert main_view.runtime_lines[-1] == "regime: stable_uptrend"
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
    assert "无可执行标的" not in snapshot_map["briefing"].headline

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
    assert [step.action_label for step in journey] == ["上调优先级", "上调优先级"]

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
        "待复核" in date_overview.focus_headline
        or "观察池" in date_overview.focus_headline
        or "核对卡点" in date_overview.focus_headline
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

    assert len(task_view.detail_cards) == 6
    assert len(review_cards) == 9
    assert "000009" not in {card.symbol for card in task_view.detail_cards}
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
            "今日无可执行标的，仅观察。\n\n"
            "## 运行参数\n"
            "- 数据源: auto -> csv\n"
            "- 数据健康: fallback / fallback 到 csv\n"
            "- thresholds.version: 1.1.1\n"
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

    assert task_view.report_summary_lines == ("今日无可执行标的，仅观察。",)
    assert task_view.runtime_lines == (
        "数据源: auto -> csv",
        "数据健康: fallback / fallback 到 csv",
        "thresholds.version: 1.1.1",
        "regime: stable_sideways",
    )


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

    assert focus.research_lines[0] == "研究动作: 维持原排序"
    assert not any("维持原排序 / 维持原排序" in line for line in focus.research_lines)


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
        "研究动作: 上调优先级 / 延续上升",
        "评分 88.0",
        "复核节奏: 高优先级 / 开盘前后",
        "研究下一步: 观察量能是否继续扩张，再决定是否维持主推",
    )


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
        "最近纸面回写: BUY 100 @ 1500.0 / 2026-06-05T10:10:00+08:00",
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
        "最近纸面回写: BUY 100 @ 1500.0 / 2026-06-08T09:35:00+08:00",
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
        "# AI 量化选股日报 - 2026-06-06\n\n错日简报。\n",
        encoding="utf-8",
    )
    (reports_dir / "closing_review-2026-06-05.md").write_text(
        "📊 每日交易复盘\n📅 日期: 2026-06-06\n",
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

    assert spotlights["300750"].action_label == "上调优先级"
    assert spotlights["300750"].blocker == ""
    assert spotlights["300750"].next_step == "早盘观察: 尾盘确认后再跟踪"
    assert spotlights["300750"].reasons == ("早盘观察: 早盘资金回流",)
    assert spotlights["300750"].risks == ("早盘观察: 早盘追高风险",)
    assert spotlights["300750"].task_labels == ("早盘策略", "尾盘策略")
    assert spotlights["002594"].action_label == "降级观察"
    assert spotlights["002594"].status_label == "尾盘降级阻塞"
    assert spotlights["002594"].blocker == "早盘观察: 早盘放量失败"
    assert spotlights["002594"].risks == ("早盘观察: 早盘风险未解除",)
    review_cards = {
        item.symbol: item for item in provider.candidate_review_cards("2026-06-05")
    }
    assert review_cards["300750"].action_label == "上调优先级"
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
    assert review_card.status_label == "尾盘确认"
    assert review_card.reasons == ("尾盘承接确认",)
    assert review_card.risks == ("尾盘高位波动",)


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

    assert focus.research_status == "研究侧待确认"
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

    assert focus.research_status == "研究侧存在阻塞"
    assert focus.research_lines[0] == "研究来源: 尾盘策略 / 尾盘确认"
    assert "研究动作: 降级观察 / 尾盘降级阻塞" in focus.research_lines
    assert "当前卡点: 尾盘放量失败" in focus.research_lines


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
        "000338 潍柴动力 | 当前卡点: 20日均成交额不足，流动性过滤" == line
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
    assert source_status["data_latest_trade_date"] == "2026-06-05"
    assert source_status["lag_days"] == "未记录"
    assert "T" in source_status["updated_at"]
