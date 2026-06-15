from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from aqsp.core.time import today_shanghai
from aqsp.core.types import PickResult


def test_run_scheduled_persists_decision_audit_log(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import aqsp.cli as cli_mod

    latest = today_shanghai().isoformat()
    frames = {
        "600519": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "open": 1500.0,
                    "high": 1510.0,
                    "low": 1490.0,
                    "close": 1505.0,
                    "volume": 1_000_000,
                    "amount": 1505000000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        ),
        "000300": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "000300",
                    "name": "沪深300",
                    "open": 3500.0,
                    "high": 3510.0,
                    "low": 3490.0,
                    "close": 3505.0,
                    "volume": 1000,
                    "amount": 350500000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                }
            ]
        ),
    }

    monkeypatch.setenv("AQSP_TRADE_LOG_DIR", str(tmp_path / "logs" / "trades"))
    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (True, []),
    )
    monkeypatch.setattr(
        cli_mod,
        "_fetch_frames_for_cli_with_metadata",
        lambda *args, **kwargs: (frames, "eastmoney"),
    )
    monkeypatch.setattr(
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["600519"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="600519",
                name="贵州茅台",
                date=latest,
                close=1505.0,
                score=71.0,
                rating="buy_candidate",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="10%-30%",
                strategies=("ma_pullback",),
                reasons=("趋势回踩",),
                risks=("RSI偏热",),
            )
        ],
    )

    class DummyPipeline:
        def run(self, *_args, **_kwargs):
            return True, ""

    monkeypatch.setattr(cli_mod, "LethalFilterPipeline", lambda: DummyPipeline())
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.filter_t1_held",
        lambda candidates, **_kwargs: (candidates, []),
    )
    monkeypatch.setattr(cli_mod, "validate_predictions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aqsp.data.anomaly.detect_anomalies",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.data.freshness.check_freshness",
        lambda _frames: [],
    )
    monkeypatch.setattr(
        "aqsp.portfolio.sector_check.check_sector_concentration",
        lambda _symbols: MagicMock(warnings=(), sectors=(), is_concentrated=False),
    )
    monkeypatch.setattr(
        "aqsp.portfolio.correlation.compute_correlation",
        lambda *_args, **_kwargs: MagicMock(
            matrix={"600519": {"600519": 1.0}},
            high_corr_pairs=[],
            avg_correlation=0.0,
        ),
    )
    monkeypatch.setattr(cli_mod, "append_predictions", lambda *args, **kwargs: None)
    monkeypatch.setattr("aqsp.ledger.base.read_ledger", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "aqsp.ledger.base.ledger_rows_to_frame", lambda _rows: pd.DataFrame()
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.StrategyDecayDetector.detect",
        lambda self, _df: [],
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: ("healthy", "eastmoney 健康", False),
    )
    monkeypatch.setattr(cli_mod, "notify_markdown", lambda markdown: [])

    args = Namespace(
        mode="close",
        symbols="600519",
        csv="",
        source="auto",
        limit=1,
        max_universe=10,
        min_avg_amount=50_000_000,
        max_data_lag_days=3,
        enable_online_factors=False,
        report=str(tmp_path / "latest.md"),
        output_csv=str(tmp_path / "latest.csv"),
        ledger=str(tmp_path / "predictions.jsonl"),
        horizon_days=3,
        fee_bps=8.0,
        slippage_bps=5.0,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    log_file = tmp_path / "logs" / "trades" / f"{latest}.jsonl"
    assert log_file.exists()
    records = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["type"] == "decision"
    assert record["symbol"] == "600519"
    assert record["action"] == "PAPER_REVIEW"
    assert record["risk_check_passed"] is True
    assert record["context"]["thresholds_version"] == "1.1.1"
    assert record["context"]["actual_source"] == "eastmoney"
    assert record["context"]["portfolio_action"] == "keep"
    assert record["context"]["paper_execution_preview"]["board_lot_shares"] == 100
    assert record["context"]["paper_execution_preview"]["plan_valid"] is True
