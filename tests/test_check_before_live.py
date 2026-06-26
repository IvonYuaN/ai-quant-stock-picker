from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

from scripts.check_before_live import (
    _strategy_threshold_consistency_blockers,
    check_before_live,
)
from aqsp.strategies.thresholds import (
    CompositeThresholds,
    MeanReversionThresholds,
    Thresholds,
    VolumeThresholds,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_runtime_outputs(root: Path) -> None:
    for rel in (
        "reports/latest.md",
        "reports/briefing.md",
        "reports/closing_review.md",
        "dist/dashboard/index.html",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
    (root / "reports" / "walkforward-grid-latest.md").write_text(
        "### PBO 失败定位\n"
        "CSCV 失败组合占比\n"
        "最差对齐周期\n"
        "训练选中变体\n"
        "测试最优变体\n",
        encoding="utf-8",
    )
    (root / "reports" / "walkforward-grid-raw-production-latest.md").write_text(
        "**标的数量**: 3200\n"
        "### PBO 失败定位\n"
        "CSCV 失败组合占比\n"
        "最差对齐周期\n"
        "训练选中变体\n"
        "测试最优变体\n",
        encoding="utf-8",
    )


def _touch_runtime_db(root: Path, name: str, *, mtime_day: date) -> str:
    path = root / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sqlite placeholder\n", encoding="utf-8")
    timestamp = datetime(
        mtime_day.year, mtime_day.month, mtime_day.day, 18, 0, 0
    ).timestamp()
    os.utime(path, (timestamp, timestamp))
    return str(path.relative_to(root))


def _prepare_ready_runtime(root: Path) -> None:
    raw_db = _touch_runtime_db(root, "astocks_raw.db", mtime_day=date(2026, 6, 12))
    (root / ".env").write_text(
        "AQSP_SOURCE=sqlite_db\n"
        "AQSP_ALLOW_ONLINE_FALLBACK=false\n"
        f"AQSP_SQLITE_DB_PATH={raw_db}\n",
        encoding="utf-8",
    )
    _write_json(
        root / "config/trading_holidays.json",
        {
            "holidays": ["2026-06-19"],
            "makeup_workdays": [],
        },
    )
    _write_json(
        root / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
            "effective_symbols": 3200,
        },
    )
    _write_jsonl(
        root / "data/predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
            for day in range(1, 31)
        ],
    )
    _write_jsonl(
        root / "data/paper_trades.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "entry_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "closed",
                "signal_id": f"sig-{day}",
            }
            for day in range(1, 31)
        ],
    )
    _write_jsonl(
        root / "data/daily_run_history.jsonl",
        [{"date": f"2026-06-{day:02d}", "success": True} for day in range(1, 6)],
    )
    _write_runtime_outputs(root)


def test_check_before_live_passes_when_all_hard_gates_are_met(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        thresholds=Thresholds(
            volume=VolumeThresholds(enabled=False),
            mean_reversion=MeanReversionThresholds(enabled=False),
            composite=CompositeThresholds(
                quality_weight=0.0,
                value_weight=0.0,
                volume_weight=0.0,
                triple_rise_weight=0.3,
            ),
        ),
    )

    assert all(finding.ok for finding in findings)


def test_strategy_threshold_consistency_blocks_enabled_zero_weight() -> None:
    thresholds = Thresholds(
        volume=VolumeThresholds(enabled=True),
        composite=CompositeThresholds(volume_weight=0.0),
    )

    blockers = _strategy_threshold_consistency_blockers(thresholds)

    assert "volume.enabled=true but composite.volume_weight<=0" in blockers


def test_strategy_threshold_consistency_blocks_disabled_positive_weight() -> None:
    thresholds = Thresholds(
        mean_reversion=MeanReversionThresholds(enabled=False),
        composite=CompositeThresholds(mean_reversion_weight=0.2),
    )

    blockers = _strategy_threshold_consistency_blockers(thresholds)

    assert (
        "mean_reversion.enabled=false but composite.mean_reversion_weight>0" in blockers
    )


def test_strategy_threshold_consistency_blocks_invalid_blend_weights() -> None:
    thresholds = Thresholds(
        composite=CompositeThresholds(
            base_blend_weight=0.8,
            regime_blend_weight=0.4,
        ),
    )

    blockers = _strategy_threshold_consistency_blockers(thresholds)

    assert "composite blend weights must sum to 1.0" in blockers[0]


def test_check_before_live_blocks_unblended_regime_screening_weights(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/strategy.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "def strategy_weights_for_regime(thresholds, regime):\n"
        "    return {strategy_id: weight for strategy_id, weight in items}\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "regime_strategy_weight_blending"
    )
    assert finding.ok is False
    assert "same composite regime blend formula" in finding.detail


def test_check_before_live_blocks_missing_runtime_threshold_application(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/strategy.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "def screen_universe(frames, config, thresholds=None):\n"
        "    current_thresholds = thresholds\n"
        "    return score_symbol(symbol, frame, config, scoring, current_thresholds.internet_strategy)\n"
        "def score_symbol(symbol, frame, config, scoring, internet_strategy=None):\n"
        "    for signal in strategy_signals:\n"
        "        weight = config.strategy_weights.get(signal.strategy_id, 1.0)\n"
        "def strategy_weights_for_regime(thresholds, regime):\n"
        "    return {strategy_id: _blend_regime_multiplier(thresholds, weight) for strategy_id, weight in items}\n"
        "def _blend_regime_multiplier(thresholds, weight):\n"
        "    base_blend_weight = thresholds.composite.base_blend_weight\n"
        "    regime_blend_weight = thresholds.composite.regime_blend_weight\n"
        "    return base_blend_weight + regime_blend_weight * weight\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item
        for item in findings
        if item.gate == "strategy_runtime_threshold_application"
    )
    assert finding.ok is False
    assert "full Thresholds" in finding.detail


def test_check_before_live_blocks_small_runtime_universe_cap(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=data/astocks_raw.db\nAQSP_MAX_UNIVERSE=300\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "runtime_universe_cap")
    assert finding.ok is False
    assert "AQSP_MAX_UNIVERSE=300" in finding.detail


def test_check_before_live_blocks_small_runtime_symbol_override(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=data/astocks_raw.db\nAQSP_SYMBOLS=600519,300750,000001\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "runtime_symbol_override")
    assert finding.ok is False
    assert "AQSP_SYMBOLS=3" in finding.detail


def test_check_before_live_blocks_runtime_online_fallback(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SOURCE=auto\n"
        "AQSP_ALLOW_ONLINE_FALLBACK=true\n"
        "AQSP_SQLITE_DB_PATH=data/astocks_raw.db\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "runtime_data_source_config"
    )
    assert finding.ok is False
    assert "AQSP_SOURCE=auto" in finding.detail
    assert "AQSP_ALLOW_ONLINE_FALLBACK" in finding.detail


def test_check_before_live_blocks_runtime_qfq_sqlite_db(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _touch_runtime_db(tmp_path, "astocks_qfq.db", mtime_day=date(2026, 6, 12))
    (tmp_path / ".env").write_text(
        "AQSP_SOURCE=sqlite_db\n"
        "AQSP_ALLOW_ONLINE_FALLBACK=false\n"
        "AQSP_SQLITE_DB_PATH=data/astocks_qfq.db\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "runtime_sqlite_price_mode"
    )
    assert finding.ok is False
    assert "astocks_qfq.db" in finding.detail


def test_check_before_live_blocks_runtime_ledger_path_drift(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SOURCE=sqlite_db\n"
        "AQSP_ALLOW_ONLINE_FALLBACK=false\n"
        "AQSP_SQLITE_DB_PATH=data/astocks_raw.db\n"
        "AQSP_LEDGER=data/coldstart_predictions.jsonl\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "runtime_ledger_paths")
    assert finding.ok is False
    assert "ledger path drift" in finding.detail
    assert "AQSP_LEDGER=data/coldstart_predictions.jsonl" in finding.detail


def test_check_before_live_blocks_coldstart_db_drift_for_sqlite_runtime(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _touch_runtime_db(tmp_path, "coldstart_qfq.db", mtime_day=date(2026, 6, 12))
    (tmp_path / ".env").write_text(
        "AQSP_SOURCE=sqlite_db\n"
        "AQSP_ALLOW_ONLINE_FALLBACK=false\n"
        "AQSP_SQLITE_DB_PATH=data/astocks_raw.db\n"
        "AQSP_COLDSTART_DB_PATH=data/coldstart_qfq.db\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "coldstart_runtime_alignment"
    )
    assert finding.ok is False
    assert "AQSP_COLDSTART_DB_PATH" in finding.detail


def test_check_before_live_blocks_legacy_coldstart_update_script_override(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SOURCE=sqlite_db\n"
        "AQSP_ALLOW_ONLINE_FALLBACK=false\n"
        "AQSP_SQLITE_DB_PATH=data/astocks_raw.db\n"
        "AQSP_COLDSTART_UPDATE_SCRIPT=/opt/market-data/update_daily.py\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "coldstart_runtime_alignment"
    )
    assert finding.ok is False
    assert "legacy qfq updater" in finding.detail


def test_check_before_live_blocks_stale_runtime_sqlite_db(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _touch_runtime_db(tmp_path, "astocks_raw.db", mtime_day=date(2026, 6, 10))

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "runtime_sqlite_freshness")
    assert finding.ok is False
    assert "mtime=2026-06-10" in finding.detail
    assert "require >= 2026-06-12" in finding.detail


def test_check_before_live_blocks_partial_runtime_sqlite_db_coverage(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    db_path = tmp_path / "data" / "astocks_raw.db"
    db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS daily_qfq")
        conn.execute(
            "CREATE TABLE daily_qfq (ts_code TEXT NOT NULL, trade_date TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO daily_qfq(ts_code, trade_date) VALUES(?, ?)",
            [(f"{idx:06d}.SH", "20260612") for idx in range(2999)],
        )
        conn.commit()
    timestamp = datetime(2026, 6, 12, 18, 0, 0).timestamp()
    os.utime(db_path, (timestamp, timestamp))

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "runtime_sqlite_freshness")
    assert finding.ok is False
    assert "2026-06-12 rows=2999/3000 symbols" in finding.detail


def test_check_before_live_blocks_missing_executability_runtime_feedback(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    runtime_path = tmp_path / "src/aqsp/ledger/runtime.py"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text(
        "def _run_scheduled_legacy(args):\n    pass\n", encoding="utf-8"
    )
    runtime_path.write_text(
        "def count_independent_signal_days(path):\n    return 30\n", encoding="utf-8"
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item
        for item in findings
        if item.gate == "strategy_executability_runtime_feedback"
    )
    assert finding.ok is False
    assert "not_executable" in finding.detail


def test_check_before_live_blocks_executability_feedback_applied_to_weights(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    runtime_path = tmp_path / "src/aqsp/ledger/runtime.py"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text(
        "def _run_scheduled_legacy(args):\n"
        "    executability_adjustments, executability_reasons = (\n"
        "        strategy_executability_weight_adjustments(args.ledger)\n"
        "    )\n"
        "    for strategy_id, multiplier in executability_adjustments.items():\n"
        "        weights[strategy_id] = weights.get(strategy_id, 1.0) * multiplier\n",
        encoding="utf-8",
    )
    runtime_path.write_text(
        "def strategy_executability_weight_adjustments(path):\n    return {}, {}\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item
        for item in findings
        if item.gate == "strategy_executability_runtime_feedback"
    )
    assert finding.ok is False
    assert "proposal-only" in finding.detail


def test_check_before_live_blocks_small_symbol_walkforward_gate(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "reports" / "walkforward-grid-raw-production-latest.md").write_text(
        "**标的数量**: 300\n",
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
            "effective_symbols": 300,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(
        item for item in findings if item.gate == "walkforward_market_coverage"
    )

    assert finding.ok is False
    assert "300/3000 effective symbols" in finding.detail
    assert "smoke tests only" in finding.detail


def test_check_before_live_blocks_when_gate_and_report_symbol_counts_diverge(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "reports" / "walkforward-grid-raw-production-latest.md").write_text(
        "**标的数量**: 300\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(
        item for item in findings if item.gate == "walkforward_market_coverage"
    )

    assert finding.ok is False
    assert "symbol count mismatch" in finding.detail
    assert "report=300" in finding.detail
    assert "gate=3200" in finding.detail


def test_check_before_live_does_not_use_diagnostic_effective_symbols_as_report_count(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "reports" / "walkforward-grid-raw-production-latest.md").write_text(
        "| 项目 | 值 |\n|------|-----|\n| effective_symbols | 3200 |\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(
        item for item in findings if item.gate == "walkforward_market_coverage"
    )

    assert finding.ok is False
    assert "production report missing actual symbol count" in finding.detail


def test_check_before_live_blocks_when_walkforward_gate_failed(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.75,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": False,
            "both_pass": False,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "walkforward_gate" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_when_walkforward_metrics_are_invalid(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": "not-a-number",
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "walkforward_gate" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_non_boolean_gate_flags(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": "true",
            "dsr_pass": "true",
            "pbo_pass": "true",
            "both_pass": "true",
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "pbo_valid flag missing/invalid/false" in finding.detail
    assert "both_pass flag missing/invalid/false" in finding.detail


def test_check_before_live_blocks_non_integer_period_count(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": True,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "n_periods missing/invalid" in finding.detail


def test_check_before_live_blocks_boolean_or_nan_metrics(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": True,
            "pbo": "NaN",
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "deflated_sharpe missing/invalid" in finding.detail
    assert "pbo missing/invalid" in finding.detail


def test_check_before_live_blocks_string_numeric_metrics(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": "1.2",
            "pbo": "0.24",
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "deflated_sharpe missing/invalid" in finding.detail
    assert "pbo missing/invalid" in finding.detail


def test_check_before_live_explains_zero_period_walkforward_block(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.24,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": True,
            "both_pass": True,
            "n_periods": 0,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))
    finding = next(item for item in findings if item.gate == "walkforward_gate")

    assert finding.ok is False
    assert "n_periods=FAIL(0)" in finding.detail
    assert "blockers: n_periods=0" in finding.detail


def test_check_before_live_blocks_when_signal_samples_are_too_small(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_jsonl(
        tmp_path / "data/predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
            for day in range(1, 10)
        ],
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "signal_sample_size" and not finding.ok for finding in findings
    )


def test_check_before_live_ignores_simulated_and_strategy_grouped_samples(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    rows = [
        {
            "signal_date": f"2026-05-{day:02d}",
            "signal_day_group": f"2026-05-{day:02d}_volume_breakout",
            "symbol": "600519",
            "status": "watch_only",
        }
        for day in range(1, 16)
    ]
    rows.extend(
        {
            "signal_date": f"2026-05-{day:02d}",
            "signal_day_group": f"2026-05-{day:02d}_mock",
            "symbol": "000001",
            "is_simulated": True,
        }
        for day in range(16, 31)
    )
    rows.append(
        {
            "signal_date": "2026-05-01",
            "signal_day_group": "2026-05-01_rps_momentum",
            "symbol": "300750",
            "status": "watch_only",
        }
    )
    _write_jsonl(tmp_path / "data/predictions.jsonl", rows)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "signal_sample_size")
    assert finding.ok is False
    assert finding.detail == "15/30 real independent signal days"


def test_check_before_live_counts_runtime_signal_date_aliases(tmp_path: Path) -> None:
    _prepare_ready_runtime(tmp_path)
    rows = [
        {
            "signal_day_group": f"2026-05-{day:02d}_ma_pullback",
            "symbol": "600519",
            "status": "watch_only",
        }
        for day in range(1, 11)
    ]
    rows.extend(
        {
            "created_at": f"2026-05-{day:02d}T18:00:00+08:00",
            "symbol": "000001",
            "rating": "watch",
        }
        for day in range(11, 21)
    )
    rows.extend(
        {
            "date": f"2026-05-{day:02d}",
            "symbol": "601318",
            "score": 42.0,
        }
        for day in range(21, 31)
    )
    _write_jsonl(tmp_path / "data/predictions.jsonl", rows)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "signal_sample_size")
    assert finding.ok is True
    assert finding.detail == "30/30 real independent signal days"


def test_check_before_live_blocks_when_paper_tracking_samples_are_too_small(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_jsonl(
        tmp_path / "data/paper_trades.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "closed",
            }
            for day in range(1, 10)
        ],
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "paper_tracking_sample_size"
    )
    assert finding.ok is False
    assert finding.detail == "9/30 real paper tracking days"


def test_check_before_live_counts_real_paper_tracking_statuses(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    rows = [
        {
            "signal_date": f"2026-05-{day:02d}",
            "symbol": "600519",
            "status": "closed",
        }
        for day in range(1, 11)
    ]
    rows.extend(
        {
            "signal_date": f"2026-05-{day:02d}",
            "symbol": "000001",
            "status": "open",
        }
        for day in range(11, 21)
    )
    rows.extend(
        {
            "signal_date": f"2026-05-{day:02d}",
            "symbol": "300750",
            "status": "not_executable",
        }
        for day in range(21, 31)
    )
    rows.extend(
        [
            {
                "signal_date": "2026-05-01",
                "symbol": "600519",
                "status": "closed",
            },
            {
                "signal_date": "2026-05-31",
                "symbol": "688981",
                "status": "closed",
                "is_simulated": True,
            },
            {
                "signal_date": "2026-06-01",
                "symbol": "601318",
                "status": "watch_only",
            },
        ]
    )
    _write_jsonl(tmp_path / "data/paper_trades.jsonl", rows)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "paper_tracking_sample_size"
    )
    assert finding.ok is True
    assert finding.detail == "30/30 real paper tracking days"


def test_check_before_live_blocks_signal_samples_counting_paper_statuses(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    runtime_path = tmp_path / "src/aqsp/ledger/runtime.py"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(
        'REAL_SIGNAL_STATUSES = frozenset({"pending", "open", "closed"})\n'
        'PAPER_TRACKING_STATUSES = frozenset({"open", "closed"})\n'
        "def count_independent_signal_days(path):\n    return 30\n"
        "def count_paper_tracking_days(path):\n    return 30\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "signal_sample_status_boundary"
    )
    assert finding.ok is False
    assert "paper-only statuses" in finding.detail


def test_check_before_live_blocks_high_strategy_not_executable_rate(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    rows = [
        {
            "signal_date": f"2026-05-{idx + 1:02d}",
            "entry_date": f"2026-05-{idx + 1:02d}",
            "symbol": "600519",
            "status": "not_executable" if idx < 4 else "closed",
            "strategies": ["limit_up_ladder"],
        }
        for idx in range(6)
    ]
    _write_jsonl(tmp_path / "data/paper_trades.jsonl", rows)

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        thresholds=Thresholds(
            volume=VolumeThresholds(enabled=False),
            mean_reversion=MeanReversionThresholds(enabled=False),
            composite=CompositeThresholds(
                quality_weight=0.0,
                value_weight=0.0,
                volume_weight=0.0,
                triple_rise_weight=0.3,
            ),
        ),
    )

    finding = next(
        item for item in findings if item.gate == "strategy_executability_feedback"
    )
    assert finding.ok is False
    assert "limit_up_ladder=67%" in finding.detail


def test_check_before_live_excludes_pending_entry_from_executability_rate(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    rows = [
        {
            "signal_date": f"2026-05-{idx + 1:02d}",
            "entry_date": f"2026-05-{idx + 1:02d}",
            "symbol": "600519",
            "status": "not_executable" if idx < 4 else "closed",
            "strategies": ["limit_up_ladder"],
        }
        for idx in range(6)
    ]
    rows.extend(
        {
            "signal_date": f"2026-05-{idx + 7:02d}",
            "symbol": "000001",
            "status": "pending_entry",
            "strategies": ["limit_up_ladder"],
        }
        for idx in range(20)
    )
    _write_jsonl(tmp_path / "data/paper_trades.jsonl", rows)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "strategy_executability_feedback"
    )
    assert finding.ok is False
    assert "limit_up_ladder=67% (4/6)" in finding.detail


def test_check_before_live_blocks_when_daily_run_history_is_missing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "data/daily_run_history.jsonl").unlink()

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "successful_daily_runs" and not finding.ok
        for finding in findings
    )


def test_check_before_live_merges_history_with_legacy_pipeline_logs(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_jsonl(
        tmp_path / "data/daily_run_history.jsonl",
        [{"date": f"2026-06-{day:02d}", "success": True} for day in range(15, 19)]
        + [{"date": "2026-06-19", "success": False, "exit_code": 1}],
    )
    pipeline_dir = tmp_path / "logs" / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        pipeline_dir / "2026-06-12.json",
        {
            "started_at": "2026-06-12T18:00:00+08:00",
            "finished_at": "2026-06-12T18:01:00+08:00",
            "overall_success": True,
            "steps": [{"name": "策略运行", "success": True}],
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 20))

    finding = next(item for item in findings if item.gate == "successful_daily_runs")
    assert finding.ok is True
    assert (
        finding.detail
        == "5/5 successful daily run days (daily_run_history+pipeline_logs)"
    )


def test_check_before_live_counts_legacy_pipeline_logs_when_history_is_missing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "data/daily_run_history.jsonl").unlink()
    pipeline_dir = tmp_path / "logs" / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    for day in range(1, 6):
        _write_json(
            pipeline_dir / f"2026-06-{day:02d}.json",
            {
                "started_at": f"2026-06-{day:02d}T18:00:00+08:00",
                "finished_at": f"2026-06-{day:02d}T18:01:00+08:00",
                "overall_success": True,
                "steps": [
                    {"name": "数据更新", "success": True},
                    {"name": "策略运行", "success": True},
                ],
            },
        )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "successful_daily_runs")
    assert finding.ok is True
    assert finding.detail == "5/5 successful daily run days (pipeline_logs)"


def test_check_before_live_blocks_when_dashboard_output_is_missing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "dist/dashboard/index.html").unlink()

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    assert any(
        finding.gate == "dashboard_html" and not finding.ok for finding in findings
    )


def test_check_before_live_blocks_every_10_minute_notify_cron(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron = tmp_path / "cron.txt"
    cron.write_text(
        "*/10 9-14 * * 1-5 /bin/bash /opt/aqsp/scripts/bt_task.sh daily --notify\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_path=cron,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "high-frequency notify risk" in finding.detail


def test_check_before_live_blocks_every_10_minute_daily_even_without_notify_flag(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron = tmp_path / "cron.txt"
    cron.write_text(
        "*/10 9-14 * * 1-5 /bin/bash /opt/aqsp/scripts/bt_task.sh daily\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_path=cron,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "bt_task.sh daily" in finding.detail


def test_check_before_live_allows_intraday_without_notify(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron = tmp_path / "cron.txt"
    cron.write_text(
        "*/10 9-14 * * 1-5 /bin/bash /opt/aqsp/scripts/bt_task.sh intraday\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_path=cron,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is True


def test_check_before_live_blocks_system_cron_installer_without_noop_guard(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "scripts/install_server_cron.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        "echo '*/15 * * * 1-5 /bin/bash /opt/aqsp/scripts/bt_task.sh monitor' | crontab -\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "system_cron_install_guard"
    )
    assert finding.ok is False
    assert "double-run" in finding.detail


def test_check_before_live_blocks_unstable_gate_notify_state_path(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_GATE_NOTIFY_STATE_PATH=/tmp/gate_notify_state.json\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "gate_notify_state_path")
    assert finding.ok is False
    assert "unstable external path" in finding.detail


def test_check_before_live_blocks_unstable_monitor_notify_state_path(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_MONITOR_NOTIFY_STATE_PATH=/tmp/monitor_notify_state.json\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "monitor_notify_state_path"
    )
    assert finding.ok is False
    assert "AQSP_MONITOR_NOTIFY_STATE_PATH" in finding.detail


def test_check_before_live_blocks_unstable_regular_notify_state_path(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_NOTIFY_STATE_PATH=/tmp/notify_state.json\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "notify_state_path")
    assert finding.ok is False
    assert "AQSP_NOTIFY_STATE_PATH" in finding.detail


def test_check_before_live_blocks_direct_cli_subcommand_notify(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        "def _notify_via_config(markdown, *, mode):\n"
        "    return []\n"
        "def run_child(args):\n"
        "    return _notify_via_config('x', mode='summary')\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "cli_subcommand_notify_dedupe"
    )
    assert finding.ok is False
    assert "bypass notification state" in finding.detail


def test_check_before_live_blocks_news_catalysts_failed_notify_without_guard(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        "def run_news_catalysts(args):\n"
        "    report = build_catalyst_report()\n"
        "    if args.notify:\n"
        "        _dispatch_notification_once('x')\n"
        '    if report.source_status == "failed":\n'
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "news_catalysts_failed_notify_guard"
    )
    assert finding.ok is False
    assert "source_status=failed" in finding.detail


def test_check_before_live_blocks_run_scheduled_ignoring_env_notify(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        "def _run_scheduled_legacy(args):\n"
        "    notification_artifacts = finalize_scheduled_notification(\n"
        "        markdown='x', args_notify=args.notify,\n"
        "    )\n"
        "    return 0\n"
        "def run_monitor(args):\n"
        "    if not args.notify_critical_only:\n"
        "        warning_targets = []\n"
        "        notify_targets.extend(warning_targets)\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "run_scheduled_env_notify_guard"
    )
    assert finding.ok is False
    assert "AQSP_NOTIFY=true" in finding.detail


def test_check_before_live_blocks_monitor_warning_notify_suppression(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        "def _run_scheduled_legacy(args):\n"
        "    runtime_config = load_runtime_config()\n"
        "    notify_requested = bool(args.notify or runtime_config.notify)\n"
        "    finalize_scheduled_notification(args_notify=notify_requested)\n"
        "def run_monitor(args):\n"
        "    notify_targets = [r for r in triggered if r.severity == 'critical']\n"
        "    if not args.notify_critical_only:\n"
        "        print('monitor notify: warning-only alerts suppressed')\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "monitor_warning_notify_guard"
    )
    assert finding.ok is False
    assert "AQSP_MONITOR_NOTIFY_WARNINGS" in finding.detail


def test_check_before_live_blocks_monitor_wrapper_without_critical_only_default(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "scripts/server_monitor.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'MONITOR_ARGS=( -m aqsp monitor --config "${MONITOR_CONFIG}" --notify )\n',
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item
        for item in findings
        if item.gate == "monitor_wrapper_critical_only_default"
    )
    assert finding.ok is False
    assert "critical-only" in finding.detail


def test_check_before_live_blocks_pipeline_gate_block_summary_skip(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "scripts/daily_pipeline.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "def _send_pipeline_digest(config, result, logger):\n"
        "    if not _pipeline_strategy_gate_ok(result):\n"
        "        return\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "pipeline_gate_block_summary_notify"
    )
    assert finding.ok is False
    assert "blocked digest" in finding.detail


def test_check_before_live_blocks_cli_concrete_data_source_constructor(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        "from aqsp.data.eastmoney_source import EastmoneySource\n"
        "def run(args):\n"
        "    return EastmoneySource()\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "cli_data_source_boundary")
    assert finding.ok is False
    assert "EastmoneySource(" in finding.detail


def test_check_before_live_blocks_data_source_empty_frame_skip(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    source_path = tmp_path / "src/aqsp/data/eastmoney_source.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "def fetch_daily(self, symbols, start, end):\n"
        "    out = {}\n"
        "    for symbol in symbols:\n"
        "        df = self._fetch(symbol)\n"
        "        if df is not None and not df.empty:\n"
        "            out[symbol] = df\n"
        "    require_non_empty_fetch_result(self.name, '日线', symbols, out)\n"
        "    return out\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "data_source_fail_closed_contract"
    )
    assert finding.ok is False
    assert "eastmoney_source.py: skips empty frame" in finding.detail


def test_check_before_live_blocks_daily_pipeline_concrete_data_source_constructor(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    pipeline_path = tmp_path / "scripts/daily_pipeline.py"
    pipeline_path.parent.mkdir(parents=True)
    pipeline_path.write_text(
        "from aqsp.data.tdx_vipdoc_source import TdxVipdocSource\n"
        "def build():\n"
        "    return TdxVipdocSource()\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "cli_data_source_boundary")
    assert finding.ok is False
    assert "scripts/daily_pipeline.py:TdxVipdocSource(" in finding.detail


def test_check_before_live_blocks_walkforward_source_branches_in_cli(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    service_path = tmp_path / "src/aqsp/services/walkforward_data.py"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text(
        "def run_walkforward(args):\n"
        '    if args.source == "baostock":\n'
        "        return 0\n",
        encoding="utf-8",
    )
    service_path.write_text(
        "def fetch_walkforward_frames():\n    return None\n", encoding="utf-8"
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "walkforward_service_boundary"
    )
    assert finding.ok is False
    assert 'args.source == "baostock"' in finding.detail


def test_check_before_live_blocks_news_binding_concrete_akshare_source(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/news/catalysts.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from aqsp.data.news_source import AkshareNewsSource\n", encoding="utf-8"
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "business_layer_source_abstractions"
    )
    assert finding.ok is False
    assert "AkshareNewsSource" in finding.detail


def test_check_before_live_blocks_global_quantile_return_leakage(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/strategies/factor_backtest.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "pd.qcut(x, 5)\ny.groupby(quantile_labels).mean()\n", encoding="utf-8"
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "backtest_no_global_quantile_leakage"
    )
    assert finding.ok is False
    assert "factor_backtest.py" in finding.detail


def test_check_before_live_blocks_factor_backtest_without_multiindex_guard(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/strategies/factor_backtest.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "def backtest_factor(factor_values, returns):\n    return returns.mean()\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "backtest_no_global_quantile_leakage"
    )
    assert finding.ok is False
    assert "factor_backtest.py" in finding.detail


def test_check_before_live_blocks_briefing_direct_notify_without_dedupe(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/briefing/notifier.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def send():\n    notify_markdown('x')\n", encoding="utf-8")

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "notification_runtime_boundaries"
    )
    assert finding.ok is False
    assert "briefing/notifier.py" in finding.detail


def test_check_before_live_blocks_unstable_notification_fingerprint(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/notification_runtime.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import hashlib\n"
        "def notification_fingerprint(*, kind, markdown):\n"
        "    digest = hashlib.sha256(markdown.strip().encode()).hexdigest()\n"
        "    return f'{kind}:{digest}'\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "notification_runtime_boundaries"
    )
    assert finding.ok is False
    assert "unstable fingerprint" in finding.detail


def test_check_before_live_blocks_monitor_direct_notify_routing(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/monitor/notifier.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "def send_alerts(results):\n    notify_markdown('x')\n", encoding="utf-8"
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "notification_runtime_boundaries"
    )
    assert finding.ok is False
    assert "monitor/notifier.py" in finding.detail


def test_check_before_live_blocks_implicit_summary_fallback(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/notifier.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "def notify_markdown_via_config(markdown, *, mode):\n"
        '    if normalized_mode == "summary":\n'
        "        results.extend(_notify_with_senders(markdown, _full_senders()))\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "notification_runtime_boundaries"
    )
    assert finding.ok is False
    assert "implicit summary fallback" in finding.detail


def test_check_before_live_blocks_auto_evolution_threshold_write(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    path = tmp_path / "src/aqsp/strategies/auto_evolution.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "def _apply_evolution(self, result):\n"
        "    content = self.thresholds_path.read_text(encoding='utf-8')\n"
        "    self.thresholds_path.write_text(content, encoding='utf-8')\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "auto_evolution_proposal_only"
    )
    assert finding.ok is False
    assert "proposals only" in finding.detail


def test_check_before_live_blocks_missing_strategy_weight_snapshot_audit(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    ledger_path = tmp_path / "src/aqsp/ledger/base.py"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("def run_scheduled(args):\n    score = 1\n", encoding="utf-8")
    ledger_path.write_text(
        "def append_predictions(path, picks):\n    return None\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "strategy_weight_snapshot_audit"
    )
    assert finding.ok is False
    assert "runtime ranking is not reproducible" in finding.detail


def test_check_before_live_blocks_missing_scheduled_service_boundary(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cli_path = tmp_path / "src/aqsp/cli.py"
    service_path = tmp_path / "src/aqsp/services/scheduled.py"
    cli_path.parent.mkdir(parents=True)
    service_path.parent.mkdir(parents=True)
    cli_path.write_text("def run_scheduled(args):\n    return 0\n", encoding="utf-8")
    service_path.write_text("def run_other(args):\n    return 0\n", encoding="utf-8")

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(
        item for item in findings if item.gate == "scheduled_service_boundary"
    )
    assert finding.ok is False
    assert "services.scheduled" in finding.detail


def test_check_before_live_blocks_bt_wrapper_high_frequency_notify(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron_dir = tmp_path / "bt-cron"
    cron_dir.mkdir()
    (cron_dir / "aqsp-intraday").write_text(
        "#!/bin/bash\n"
        "cd /opt/aqsp\n"
        "/bin/bash /opt/aqsp/scripts/bt_task.sh intraday --notify\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_dir=cron_dir,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "intraday" in finding.detail


def test_check_before_live_blocks_bt_wrapper_that_bypasses_bt_task(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron_dir = tmp_path / "bt-cron"
    cron_dir.mkdir()
    (cron_dir / "aqsp-daily-direct").write_text(
        "#!/bin/bash\ncd /opt/aqsp\n/bin/bash /opt/aqsp/scripts/daily_pipeline.sh\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_dir=cron_dir,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "bypasses bt_task.sh" in finding.detail


def test_check_before_live_allows_bt_wrapper_through_unified_entry(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron_dir = tmp_path / "bt-cron"
    cron_dir.mkdir()
    (cron_dir / "aqsp-daily").write_text(
        "#!/bin/bash\ncd /opt/aqsp\n/bin/bash /opt/aqsp/scripts/bt_task.sh daily\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_dir=cron_dir,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is True


def test_check_before_live_blocks_wrapper_enabling_daily_notify(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    cron_dir = tmp_path / "bt-cron"
    cron_dir.mkdir()
    (cron_dir / "aqsp-daily-notify").write_text(
        "#!/bin/bash\n"
        "export AQSP_NOTIFY=true\n"
        "cd /opt/aqsp\n"
        "/bin/bash /opt/aqsp/scripts/bt_task.sh daily\n",
        encoding="utf-8",
    )

    findings = check_before_live(
        root=tmp_path,
        today=date(2026, 6, 14),
        cron_dir=cron_dir,
    )

    finding = next(item for item in findings if item.gate == "scheduler_notify_cadence")
    assert finding.ok is False
    assert "AQSP_NOTIFY" in finding.detail


def test_check_before_live_requires_pbo_diagnostics_when_gate_failed(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.75,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": False,
            "both_pass": False,
            "n_periods": 12,
        },
    )
    (tmp_path / "reports" / "walkforward-grid-latest.md").write_text(
        "### PBO 失败定位\nCSCV 失败组合占比\n",
        encoding="utf-8",
    )
    (tmp_path / "reports" / "walkforward-grid-raw-production-latest.md").write_text(
        "**标的数量**: 3200\n### PBO 失败定位\nCSCV 失败组合占比\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "pbo_diagnostics")
    assert finding.ok is False
    assert "训练选中变体" in finding.detail


def test_check_before_live_accepts_pbo_diagnostics_when_gate_failed(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    _write_json(
        tmp_path / "data/walkforward_gate.json",
        {
            "run_date": "2026-06-10",
            "deflated_sharpe": 1.2,
            "pbo": 0.75,
            "pbo_valid": True,
            "dsr_pass": True,
            "pbo_pass": False,
            "both_pass": False,
            "n_periods": 12,
        },
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "pbo_diagnostics")
    assert finding.ok is True


def test_check_before_live_accepts_production_pbo_diagnostics_report(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "reports" / "walkforward-grid-latest.md").unlink()
    (tmp_path / "reports" / "walkforward-grid-raw-production-latest.md").write_text(
        "### PBO 失败定位\n"
        "CSCV 失败组合占比\n"
        "最差对齐周期\n"
        "训练选中变体\n"
        "测试最优变体\n",
        encoding="utf-8",
    )
    gate_path = tmp_path / "data/walkforward_gate.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload.update({"pbo_pass": False, "both_pass": False, "pbo": 0.75})
    _write_json(gate_path, payload)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "pbo_diagnostics")
    assert finding.ok is True


def test_check_before_live_accepts_separate_production_diagnostic_report(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / "reports" / "walkforward-grid-latest.md").unlink()
    (tmp_path / "reports" / "walkforward-grid-raw-production-latest.md").unlink()
    (
        tmp_path / "reports" / "walkforward-grid-raw-production-diagnostic-latest.md"
    ).write_text(
        "### PBO 失败定位\n"
        "CSCV 失败组合占比\n"
        "最差对齐周期\n"
        "训练选中变体\n"
        "测试最优变体\n",
        encoding="utf-8",
    )
    gate_path = tmp_path / "data/walkforward_gate.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload.update({"pbo_pass": False, "both_pass": False, "pbo": 0.75})
    _write_json(gate_path, payload)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "pbo_diagnostics")
    assert finding.ok is True


def test_check_before_live_blocks_qfq_walkforward_price_mode(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_qfq.db\n",
        encoding="utf-8",
    )

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "walkforward_price_mode")
    assert finding.ok is False
    assert "qfq historical database" in finding.detail


def test_check_before_live_blocks_unknown_walkforward_price_mode(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks.db\n",
        encoding="utf-8",
    )
    gate_path = tmp_path / "data" / "walkforward_gate.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload.pop("price_mode", None)
    payload.pop("sqlite_db_path", None)
    _write_json(gate_path, payload)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "walkforward_price_mode")
    assert finding.ok is False
    assert "price_mode is unknown" in finding.detail


def test_check_before_live_allows_raw_gate_metadata_over_qfq_env(
    tmp_path: Path,
) -> None:
    _prepare_ready_runtime(tmp_path)
    (tmp_path / ".env").write_text(
        "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_qfq.db\n",
        encoding="utf-8",
    )
    gate_path = tmp_path / "data" / "walkforward_gate.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "source": "sqlite_db",
            "sqlite_db_path": "/opt/market-data/astocks_raw.db",
            "price_mode": "raw",
        }
    )
    _write_json(gate_path, payload)

    findings = check_before_live(root=tmp_path, today=date(2026, 6, 14))

    finding = next(item for item in findings if item.gate == "walkforward_price_mode")
    assert finding.ok is True
