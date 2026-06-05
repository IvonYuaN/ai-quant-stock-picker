from __future__ import annotations

from argparse import Namespace
import sqlite3

import pandas as pd
from unittest.mock import MagicMock

from aqsp.core.time import today_shanghai
from aqsp.core.types import PickResult


def test_run_scheduled_notify_prepends_source_status_banner(
    monkeypatch, tmp_path
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
                    "volume": 1000,
                    "amount": 150500000.0,
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

    # 直接 monkeypatch _check_notification_gate，让它总是返回双门通过
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
    )  # 35 >= 30，冷启动通过
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
    monkeypatch.setattr(
        cli_mod,
        "validate_predictions",
        lambda *_args, **_kwargs: None,
    )

    class DummyBreaker:
        def check(self, **_kwargs):
            return type("Status", (), {"triggered": False, "reason": "正常"})()

    monkeypatch.setattr(cli_mod, "CircuitBreaker", lambda: DummyBreaker())
    monkeypatch.setattr(
        cli_mod,
        "describe_source_health",
        lambda *_args, **_kwargs: (
            "fallback",
            "fallback 到 eastmoney；plan成功/失败 5/1，源成功/失败 5/0",
            True,
        ),
    )
    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

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
        notify=True,
    )

    exit_code = cli_mod.run_scheduled(args)

    assert exit_code == 0
    assert seen
    assert seen[0].startswith("## 数据源状态")
    assert "auto -> eastmoney" in seen[0]
    assert "- 健康: fallback" in seen[0]


def test_run_scheduled_enriches_pick_name_from_symbol_map(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    latest = today_shanghai().isoformat()
    frames = {
        "300750": pd.DataFrame(
            [
                {
                    "date": latest,
                    "symbol": "300750",
                    "name": "300750",
                    "open": 430.0,
                    "high": 435.0,
                    "low": 428.0,
                    "close": 432.0,
                    "volume": 1000,
                    "amount": 432000000.0,
                    "suspended": False,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
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
        cli_mod, "_resolve_run_symbols", lambda *args, **kwargs: ["300750"]
    )
    monkeypatch.setattr(
        cli_mod, "strategy_weights_from_ledger", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 35
    )
    monkeypatch.setattr(
        cli_mod,
        "_load_optional_symbol_name_map",
        lambda symbols: {"300750": "宁德时代"} if "300750" in symbols else {},
    )
    monkeypatch.setattr(
        cli_mod,
        "screen_universe",
        lambda *_args, **_kwargs: [
            PickResult(
                symbol="300750",
                name="300750",
                date=latest,
                close=432.0,
                score=71.0,
                rating="watch",
                entry_type="relative_strength",
                ideal_buy=432.0,
                stop_loss=420.0,
                take_profit=460.0,
                position="watch",
                strategies=("bowl_rebound",),
                reasons=("MACD 动能改善",),
                risks=("流动性过滤",),
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
        symbols="300750",
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
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "300750 宁德时代" in report
    assert "300750 300750" not in report


def test_optional_symbol_name_map_reads_project_env_without_export(
    monkeypatch, tmp_path
) -> None:
    import aqsp.cli as cli_mod

    db_path = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "INSERT INTO stocks (ts_code, name) VALUES (?, ?)",
            ("600036.SH", "招商银行"),
        )
    env_path = tmp_path / ".env"
    env_path.write_text(f"AQSP_SQLITE_DB_PATH={db_path}\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AQSP_SQLITE_DB_PATH", raising=False)

    assert cli_mod._load_optional_symbol_name_map(["600036"]) == {
        "600036": "招商银行"
    }


def test_run_scheduled_report_omits_low_signal_control_sections(
    monkeypatch, tmp_path
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
                    "volume": 1000,
                    "amount": 150500000.0,
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
    monkeypatch.setattr(
        cli_mod,
        "validate_predictions",
        lambda *_args, **_kwargs: None,
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

    report_path = tmp_path / "latest.md"
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
        report=str(report_path),
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
    content = report_path.read_text(encoding="utf-8")
    assert "## 最终决策看板" in content
    assert "## 数据异常检测" not in content
    assert "## 数据新鲜度" not in content
    assert "## 候选股相关性" not in content
    assert "## 策略衰减告警" not in content


def test_run_scheduled_notify_continues_when_benchmark_frame_missing(
    monkeypatch, tmp_path
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
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }

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
        lambda *_args, **_kwargs: ("warning", "benchmark unavailable", False),
    )

    seen: list[str] = []
    monkeypatch.setattr(
        cli_mod,
        "notify_markdown",
        lambda markdown: seen.append(markdown) or [],
    )

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
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert seen
    assert "当前市况" not in report
    assert "- regime: unknown" in report
    assert "## 最终决策看板" in report
    assert "贵州茅台" in report


def test_run_scheduled_gate_block_adds_actionable_unlock_guidance(
    monkeypatch, tmp_path
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
                    "volume": 1000,
                    "amount": 150500000.0,
                    "suspended": False,
                    "limit_up": 1655.5,
                    "limit_down": 1354.5,
                }
            ]
        )
    }

    monkeypatch.setattr(
        cli_mod,
        "_check_notification_gate",
        lambda *, cold_start_days, gate_path=None: (
            False,
            [
                "冷启动未满: 3/30 个独立信号日",
                "双门 sidecar 无有效回测周期（n_periods=0）—— 疑似占位/测试数据，需真正跑 walkforward 后重写",
            ],
        ),
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
        cli_mod, "_count_independent_signal_days", lambda *_args, **_kwargs: 3
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
                rating="watch",
                entry_type="next_open",
                ideal_buy=1505.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="watch",
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
        notify=True,
        enable_debate=False,
        pool="",
    )

    exit_code = cli_mod.run_scheduled(args)
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "解锁建议：" in report
    assert "aqsp walkforward --source sqlite_db --end 2024-12-31" in report
    assert "当前还差 27 天" in report
    assert "。；" not in report


def test_main_accepts_run_scheduled_alias(monkeypatch) -> None:
    from aqsp.cli import main
    import aqsp.cli as cli_mod

    def mock_run_scheduled(args):
        assert args.command == "run-scheduled"
        assert args.symbols == "600519"
        return 0

    monkeypatch.setattr(cli_mod, "run_scheduled", mock_run_scheduled)
    assert main(["run-scheduled", "--symbols", "600519"]) == 0
