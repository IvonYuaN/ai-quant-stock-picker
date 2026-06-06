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
