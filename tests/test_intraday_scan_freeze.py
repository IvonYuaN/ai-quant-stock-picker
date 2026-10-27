"""Rotating quote coverage must remain independent of the stable candidate view."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import asdict, replace
from datetime import datetime
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from aqsp.core.time import SHANGHAI_TZ
from aqsp.core.types import PickResult
from aqsp.portfolio.parameter_snapshot import build_parameter_snapshot
from aqsp.services import candidate_freeze as freeze
from aqsp.strategies.thresholds import Thresholds
from aqsp.universe.intraday_cursor import IntradayUniverseCursor

DAY = "2026-10-27"
NOW = datetime(2026, 10, 27, 14, 30, tzinfo=SHANGHAI_TZ)


def _pick(symbol: str, score: float = 70) -> PickResult:
    return PickResult(
        symbol, "测试股票", DAY, 10.0, score, "B", "观察", 10, 9, 12, "观察"
    )


def _frame(symbol: str) -> pd.DataFrame:
    frame = pd.DataFrame({"date": [DAY], "symbol": [symbol], "close": [10.0]})
    frame.attrs.update(
        source_name="tencent",
        timestamp_source="bar_time",
        fetched_at=NOW.isoformat(),
        freshness="fresh",
    )
    return frame


@pytest.fixture(autouse=True)
def _isolated_clock(monkeypatch, tmp_path):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(freeze, "now_shanghai", lambda: NOW)
    monkeypatch.setattr(freeze, "today_shanghai", lambda: NOW.date())
    monkeypatch.setattr(freeze, "_current_trading_session_date", lambda: DAY)


def _batch(
    fingerprint: str,
    symbols: list[str],
    picks: list[PickResult],
    thresholds: Thresholds | None = None,
):
    snapshot = freeze.freeze_scored_candidates(
        session_date=DAY,
        request_fingerprint=fingerprint,
        source="tencent",
        data_latest_trade_date=DAY,
        thresholds_version="test",
        regime="trend",
        strategy_weights={"momentum": 1},
        resolved_symbols=symbols,
        min_price=1,
        max_price=1000,
        min_avg_amount=0,
        picks=picks,
        path=freeze.candidate_batch_snapshot_path(fingerprint),
        thresholds=thresholds or Thresholds(version="test", effective_from=DAY),
    )
    return freeze.freeze_final_candidates(
        snapshot, picks, freeze.candidate_batch_snapshot_path(fingerprint)
    )


def test_scan_freeze_preserves_roster_and_records_additions_when_batches_differ(
    tmp_path,
):
    first = _batch(
        "first", ["600001", "600002"], [_pick("600001", 70), _pick("600002", 60)]
    )
    initial = freeze.session_candidate_view(first, final_picks=list(first.final_picks))
    frozen_bytes = freeze.candidate_snapshot_path().read_bytes()
    second = _batch("second", ["600003"], [_pick("600003", 99)])
    updated = freeze.session_candidate_view(
        second, final_picks=list(second.final_picks)
    )
    assert [pick.symbol for pick in initial] == ["600001", "600002"]
    assert [pick.symbol for pick in updated] == ["600001", "600002", "600003"]
    assert [pick.score for pick in updated] == [70, 60, 99]
    assert updated[-1].metrics["candidate_increment_status"] == "added"
    assert freeze.candidate_snapshot_path().read_bytes() == frozen_bytes
    increments = freeze.candidate_increment_path().read_bytes()
    freeze.session_candidate_view(
        second, final_picks=[replace(_pick("600003"), score=100)]
    )
    assert freeze.candidate_increment_path().read_bytes() == increments
    assert freeze.session_candidate_view()[2].score == 99


def test_scan_freeze_keeps_legacy_identity_when_new_batch_arrives(tmp_path):
    thresholds = Thresholds(version="test", effective_from=DAY)
    legacy = replace(
        _batch(
            "old-request-with-symbols",
            ["600001"],
            [_parameter_pick("600001", thresholds)],
        ),
        thresholds_fingerprint="",
    )
    freeze.candidate_snapshot_path().write_text(
        json.dumps(freeze._serialize_snapshot(legacy)) + "\n"
    )
    frozen_bytes = freeze.candidate_snapshot_path().read_bytes()
    next_batch = _batch("different-request", ["600002"], [_pick("600002")])
    result = freeze.session_candidate_view(
        next_batch, final_picks=list(next_batch.final_picks), thresholds=thresholds
    )
    assert [pick.symbol for pick in result] == ["600001", "600002"]
    assert freeze.candidate_snapshot_path().read_bytes() == frozen_bytes


def test_scan_freeze_retains_empty_snapshot_when_later_batch_has_candidates():
    first = _batch("empty", ["600001"], [])
    assert freeze.session_candidate_view(first, final_picks=[]) == []
    second = _batch("later", ["600002"], [_pick("600002")])
    result = freeze.session_candidate_view(second, final_picks=list(second.final_picks))
    assert result[0].metrics["candidate_increment_status"] == "added"
    stable = freeze.load_candidate_snapshot(DAY, "empty")
    assert stable.final_picks == ()
    assert stable.resolved_symbols == ("600001",)


@pytest.mark.parametrize("change_config", [False, True])
def test_scan_freeze_checks_empty_snapshot_config_before_first_added(change_config):
    thresholds = Thresholds(version="test", effective_from=DAY)
    empty = _batch("empty", ["600001"], [], thresholds)
    freeze.session_candidate_view(empty, final_picks=[], thresholds=thresholds)
    frozen_bytes = freeze.candidate_snapshot_path().read_bytes()
    current = (
        replace(
            thresholds,
            composite=replace(
                thresholds.composite,
                min_total_score=thresholds.composite.min_total_score + 1,
            ),
        )
        if change_config
        else thresholds
    )
    second = _batch("second", ["600002"], [_pick("600002")], current)
    if change_config:
        with pytest.raises(freeze.CandidateSnapshotError, match="真实参数指纹冲突"):
            freeze.session_candidate_view(
                second, final_picks=list(second.final_picks), thresholds=current
            )
        assert not freeze.candidate_increment_path().exists()
    else:
        result = freeze.session_candidate_view(
            second, final_picks=list(second.final_picks), thresholds=current
        )
        assert [pick.symbol for pick in result] == ["600002"]
    assert freeze.candidate_snapshot_path().read_bytes() == frozen_bytes


def test_scan_freeze_rejects_first_added_when_legacy_empty_has_no_config_identity():
    empty = replace(_batch("empty", ["600001"], []), thresholds_fingerprint="")
    freeze.session_candidate_view(empty, final_picks=[])
    frozen_bytes = freeze.candidate_snapshot_path().read_bytes()
    assert "thresholds_fingerprint" not in json.loads(frozen_bytes)
    second = _batch("second", ["600002"], [_pick("600002")])
    with pytest.raises(freeze.CandidateSnapshotError, match="旧空候选缺少真实参数身份"):
        freeze.session_candidate_view(second, final_picks=list(second.final_picks))
    assert freeze.candidate_snapshot_path().read_bytes() == frozen_bytes
    assert not freeze.candidate_increment_path().exists()


@pytest.mark.parametrize(
    "change",
    [
        {"thresholds_version": "changed"},
        {"min_price": 2},
        {"max_price": 500},
        {"min_avg_amount": 1},
        {"strategy_weights": {"momentum": 0.9}},
    ],
)
def test_scan_freeze_rejects_changed_parameters_when_symbols_rotate(change):
    first = _batch("first", ["600001"], [_pick("600001")])
    freeze.session_candidate_view(first, final_picks=list(first.final_picks))
    frozen_bytes = freeze.candidate_snapshot_path().read_bytes()
    second = replace(_batch("second", ["600002"], [_pick("600002")]), **change)
    with pytest.raises(freeze.CandidateSnapshotError, match="参数冲突"):
        freeze.session_candidate_view(second, final_picks=list(second.final_picks))
    assert freeze.candidate_snapshot_path().read_bytes() == frozen_bytes
    assert not freeze.candidate_increment_path().exists()


def _parameter_pick(symbol, thresholds, regime="trend"):
    runtime_weights = {
        "regime": regime,
        "strategy_weights": {"momentum": 1.0},
        "thresholds_version": thresholds.version,
    }
    identity = build_parameter_snapshot(thresholds, runtime_weights)
    return replace(
        _pick(symbol),
        metrics={
            "strategy_weight_snapshot": {
                **runtime_weights,
                "parameter_snapshot": asdict(identity),
            }
        },
    )


@pytest.mark.parametrize("regime", ["trend", "range"])
@pytest.mark.parametrize("provide_thresholds", [False, True])
def test_scan_freeze_rejects_real_fingerprint_change_when_version_is_unchanged(
    regime, provide_thresholds
):
    thresholds = Thresholds(version="test", effective_from=DAY)
    changed = replace(
        thresholds,
        composite=replace(
            thresholds.composite,
            min_total_score=thresholds.composite.min_total_score + 1,
        ),
    )
    first = _batch("first", ["600001"], [_parameter_pick("600001", thresholds)])
    freeze.session_candidate_view(first, final_picks=list(first.final_picks))
    frozen_bytes = freeze.candidate_snapshot_path().read_bytes()
    second = replace(
        _batch("second", ["600002"], [_parameter_pick("600002", changed, regime)]),
        regime=regime,
    )
    assert first.thresholds_version == second.thresholds_version
    with pytest.raises(freeze.CandidateSnapshotError, match="真实参数指纹冲突"):
        freeze.session_candidate_view(
            second,
            final_picks=list(second.final_picks),
            thresholds=changed if provide_thresholds else None,
        )
    assert freeze.candidate_snapshot_path().read_bytes() == frozen_bytes
    assert not freeze.candidate_increment_path().exists()


def test_scan_freeze_accepts_source_fallback_and_regime_label_change():
    thresholds = Thresholds(version="test", effective_from=DAY)
    first = _batch("first", ["600001"], [_parameter_pick("600001", thresholds)])
    freeze.session_candidate_view(first, final_picks=list(first.final_picks))
    second = replace(
        _batch(
            "second",
            ["600002"],
            [_parameter_pick("600002", thresholds, regime="range")],
        ),
        source="sina",
        regime="range",
    )
    result = freeze.session_candidate_view(
        second, final_picks=list(second.final_picks), thresholds=thresholds
    )
    assert [pick.symbol for pick in result] == ["600001", "600002"]


def test_scan_freeze_rebuilds_fingerprint_with_exact_weights_and_original_reasons():
    from aqsp.cli import _runtime_weight_snapshot

    thresholds = Thresholds(version="test", effective_from=DAY)
    weights = {"momentum": 0.12345671}
    batches = []
    for index, regime in enumerate(("trend", "range")):
        runtime = _runtime_weight_snapshot(
            thresholds=thresholds,
            regime=regime,
            strategy_weights=weights,
            strategy_weight_reasons={"momentum": f"observation {index}"},
        )
        assert runtime["strategy_weights"] != weights
        symbol = f"60000{index + 1}"
        pick = replace(_pick(symbol), metrics={"strategy_weight_snapshot": runtime})
        batches.append(
            replace(
                _batch(str(index), [symbol], [pick]),
                regime=regime,
                strategy_weights=weights,
            )
        )
    for batch in batches:
        result = freeze.session_candidate_view(
            batch, final_picks=list(batch.final_picks), thresholds=thresholds
        )
    assert [pick.symbol for pick in result] == ["600001", "600002"]


@pytest.mark.parametrize("provide_thresholds", [False, True])
def test_scan_freeze_rejects_changed_config_when_first_identity_is_in_added(
    provide_thresholds,
):
    thresholds = Thresholds(version="test", effective_from=DAY)
    empty = _batch("empty", ["600001"], [])
    freeze.session_candidate_view(empty, final_picks=[], thresholds=thresholds)
    first_added = _batch("A", ["600002"], [_parameter_pick("600002", thresholds)])
    freeze.session_candidate_view(
        first_added, final_picks=list(first_added.final_picks), thresholds=thresholds
    )
    # A pre-upgrade empty v2 snapshot may already have a valid A addition.
    # Its addition must remain a config constraint without the new hash field.
    legacy = replace(
        freeze.load_candidate_snapshot(DAY, "empty"), thresholds_fingerprint=""
    )
    freeze.candidate_snapshot_path().write_text(
        json.dumps(freeze._serialize_snapshot(legacy)) + "\n"
    )
    frozen_bytes = freeze.candidate_snapshot_path().read_bytes()
    increment_bytes = freeze.candidate_increment_path().read_bytes()
    changed = replace(
        thresholds,
        composite=replace(
            thresholds.composite,
            min_total_score=thresholds.composite.min_total_score + 1,
        ),
    )
    third = replace(
        _batch("B", ["600003"], [_parameter_pick("600003", changed, "range")], thresholds=changed),
        regime="range",
    )
    with pytest.raises(freeze.CandidateSnapshotError, match="真实参数指纹冲突"):
        freeze.session_candidate_view(
            third,
            final_picks=list(third.final_picks),
            thresholds=changed if provide_thresholds else None,
        )
    assert freeze.candidate_snapshot_path().read_bytes() == frozen_bytes
    assert freeze.candidate_increment_path().read_bytes() == increment_bytes
    assert [pick.symbol for pick in freeze.session_candidate_view()] == ["600002"]


def test_scan_receipt_rejects_seven_of_256_and_never_counts_benchmark(tmp_path):
    symbols = [f"{i:06d}" for i in range(1, 257)]
    frames = {symbol: _frame(symbol) for symbol in symbols[:7]}
    frames["000300"] = _frame("000300")
    path = str(tmp_path / "batch.csv")
    freeze.write_candidate_scan_receipt(
        path,
        session_date=DAY,
        request_fingerprint="batch",
        symbols=symbols,
        frames=frames,
        missing_symbols=(),
        benchmark_symbol="000300",
    )
    receipt = json.loads((tmp_path / "batch.csv.scan.json").read_text())
    assert len(receipt["scanned_symbols"]) == 256
    assert len(receipt["fetched_symbols"]) == 7
    assert len(receipt["valid_quotes"]) == 7
    assert "000300" not in {item["symbol"] for item in receipt["valid_quotes"]}
    with pytest.raises(freeze.CandidateSnapshotError, match="7/256"):
        freeze.validate_candidate_scan_receipt(
            path, expected_symbols=symbols, minimum_valid_ratio=0.5
        )
    assert len((tmp_path / "batch.csv.scan.json").read_bytes()) < 15000


@pytest.mark.parametrize(
    "mutation",
    [
        "empty", "missing_overlay", "cached", "old_day", "no_source", "no_time",
        "future", "future_bar_naive", "future_bar_aware", "future_bar_utc",
    ],
)
def test_scan_receipt_rejects_invalid_quote_evidence_when_source_is_incomplete(
    tmp_path, mutation
):
    frame = _frame("600001")
    missing = ()
    if mutation == "empty":
        frame = frame.iloc[:0]
    elif mutation == "missing_overlay":
        missing = ("600001",)
    elif mutation == "cached":
        frame.attrs["cache_fallback"] = True
    elif mutation == "old_day":
        frame["date"] = "2026-10-26"
    elif mutation == "no_source":
        frame.attrs.pop("source_name")
    elif mutation == "no_time":
        frame.attrs.pop("fetched_at")
    elif mutation == "future":
        frame.attrs["fetched_at"] = "2026-10-27T15:00:00+08:00"
    elif mutation == "future_bar_naive":
        frame["date"] = "2026-10-27T14:31:00"
    elif mutation == "future_bar_aware":
        frame["date"] = "2026-10-27T14:31:00+08:00"
    elif mutation == "future_bar_utc":
        frame["date"] = "2026-10-27T06:31:00+00:00"
    path = str(tmp_path / "batch.csv")
    freeze.write_candidate_scan_receipt(
        path,
        session_date=DAY,
        request_fingerprint="batch",
        symbols=["600001"],
        frames={"600001": frame},
        missing_symbols=missing,
        benchmark_symbol="000300",
    )
    with pytest.raises(freeze.CandidateSnapshotError, match="最低覆盖"):
        freeze.validate_candidate_scan_receipt(
            path, expected_symbols=["600001"], minimum_valid_ratio=0.5
        )


def test_scan_receipt_matches_exact_batch_and_rejects_tampering(tmp_path):
    path = str(tmp_path / "batch.csv")
    freeze.write_candidate_scan_receipt(
        path,
        session_date=DAY,
        request_fingerprint="batch",
        symbols=["600001"],
        frames={"600001": _frame("600001")},
        missing_symbols=(),
        benchmark_symbol="000300",
    )
    assert (
        freeze.validate_candidate_scan_receipt(
            path, expected_symbols=["600001"], minimum_valid_ratio=0.5
        )
        == 1
    )
    with pytest.raises(freeze.CandidateSnapshotError, match="当前批次"):
        freeze.validate_candidate_scan_receipt(
            path, expected_symbols=["600002"], minimum_valid_ratio=0.5
        )
    receipt_path = tmp_path / "batch.csv.scan.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["scanned_symbols"] = ["600002"]
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(freeze.CandidateSnapshotError, match="校验"):
        freeze.validate_candidate_scan_receipt(
            path, expected_symbols=["600001"], minimum_valid_ratio=0.5
        )


def test_cursor_commit_is_idempotent_and_old_failure_cannot_rewind_next_batch(tmp_path):
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    symbols = ["600001", "600002"]
    first = cursor.select(symbols, trade_date=NOW.date(), batch_size=1)
    cursor.commit_current(scanned_count=1)
    committed = cursor.path.read_bytes()
    cursor.commit_current(scanned_count=1)
    cursor.fail(first, "late timeout")
    assert cursor.path.read_bytes() == committed
    second = cursor.select(symbols, trade_date=NOW.date(), batch_size=1)
    cursor.commit(first, scanned_count=1)
    assert cursor._read()["active_batch_id"] == second.batch_id
    cursor.commit_current(scanned_count=1)
    final = cursor.path.read_bytes()
    cursor.commit_current(scanned_count=1)
    assert cursor.path.read_bytes() == final
    with pytest.raises(ValueError, match="stale"):
        cursor.commit(first, scanned_count=1)
    third = cursor.select(["600003", "600004"], trade_date=NOW.date(), batch_size=1)
    cursor.commit(third, scanned_count=1)
    assert cursor._read()["active_state"] == "committed"


def test_cursor_advances_when_original_pool_returns_after_other_pool_failed(tmp_path):
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    original = ["600001", "600002"]
    first = cursor.select(original, trade_date=NOW.date(), batch_size=1)
    cursor.commit(first, scanned_count=1)
    other = cursor.select(original + ["600003"], trade_date=NOW.date(), batch_size=1)
    cursor.fail(other, "source timeout")
    restored = cursor.select(original, trade_date=NOW.date(), batch_size=1)
    assert restored.batch_id == first.batch_id
    assert restored.symbols == ("600001",)
    cursor.commit_current(scanned_count=1)
    next_batch = cursor.select(original, trade_date=NOW.date(), batch_size=1)
    assert next_batch.symbols == ("600002",)


class _StopAfterProvisional(Exception):
    pass


@pytest.fixture
def _scheduler(monkeypatch, tmp_path):
    import aqsp.cli as cli

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setattr(cli, "today_shanghai", lambda: NOW.date())
    monkeypatch.setattr("aqsp.core.time.is_trading_day", lambda _: True)
    monkeypatch.setattr(
        cli, "_runtime_source_workload_allowed", lambda *a, **k: (True, "")
    )
    monkeypatch.setattr(
        cli, "_runtime_actual_source_workload_allowed", lambda *a, **k: (True, "")
    )
    monkeypatch.setattr(cli, "assert_fresh_data", lambda *a, **k: NOW.date())
    monkeypatch.setattr(cli, "_runtime_data_lag_days", lambda *a: 0)
    monkeypatch.setattr(
        cli,
        "_source_runtime_metadata",
        lambda *a, **k: ("realtime", "multi_dimensional", "not_required"),
    )
    monkeypatch.setattr(
        cli, "describe_source_health", lambda *a: ("healthy", "fixture", False)
    )
    monkeypatch.setattr(cli, "_compute_real_pnl", lambda *a: (0, 0, 0))
    monkeypatch.setattr(
        cli,
        "_build_circuit_breaker",
        lambda *a: SimpleNamespace(
            check=lambda **k: SimpleNamespace(triggered=False, reason="")
        ),
    )
    monkeypatch.setattr(cli, "_count_independent_signal_days", lambda *a: 35)
    monkeypatch.setattr(cli, "_detect_runtime_regime", lambda *a, **k: "trend")
    monkeypatch.setattr(cli, "_runtime_strategy_weights", lambda *a: {"momentum": 1})
    monkeypatch.setattr(cli, "_enrich_pick_names", lambda picks, *a, **k: picks)
    monkeypatch.setattr(
        cli, "_attach_runtime_weight_snapshot", lambda picks, **k: picks
    )
    monkeypatch.setattr(
        "aqsp.ledger.learner.PerformanceLearner.learn_from_ledger", lambda *a: {}
    )
    monkeypatch.setattr(cli, "_runtime_regime_market_context_lines", lambda *a, **k: ())
    captured = {"fetches": [], "scores": [], "valid_count": 256, "candidate_count": 7}

    def fetch(source, symbols, **kwargs):
        captured["fetches"].append(list(symbols))
        frames = {
            symbol: _frame(symbol) for symbol in symbols[: captured["valid_count"]]
        }
        frames["000300"] = _frame("000300")
        for frame in frames.values():
            frame.attrs["intraday_overlay_coverage"] = {
                "status": "complete",
                "missing_symbols": (),
            }
        return frames, "tencent"

    def score(frames, *args):
        captured["scores"].append(list(frames))
        return [_pick(symbol) for symbol in list(frames)[: captured["candidate_count"]]]

    original_provisional = cli._write_high_frequency_provisional_outputs

    def provisional(**kwargs):
        original_provisional(**kwargs)
        captured["metadata"] = kwargs["run_metadata"]
        raise _StopAfterProvisional

    monkeypatch.setattr(
        cli, "_resolve_run_symbols", lambda source, symbols, **k: symbols.split(",")
    )
    monkeypatch.setattr(cli, "_fetch_special_strategy_frames", fetch)
    monkeypatch.setattr(cli, "_screen_universe_with_thresholds", score)
    monkeypatch.setattr(cli, "_write_high_frequency_provisional_outputs", provisional)
    args = Namespace(
        mode="close",
        symbols="",
        csv="",
        source="tencent",
        limit=20,
        max_universe=256,
        min_avg_amount=1,
        max_data_lag_days=1,
        enable_online_factors=False,
        report=str(tmp_path / "batch.md"),
        output_csv=str(tmp_path / "batch.csv"),
        ledger=str(tmp_path / "ledger.jsonl"),
        horizon_days=3,
        fee_bps=None,
        slippage_bps=None,
        benchmark_symbol="000300",
        skip_validation=True,
        notify=False,
        enable_debate=False,
        pool="",
        as_of="",
    )
    return cli, args, captured


def test_scheduled_scan_uses_all_256_symbols_when_seven_candidates_are_frozen(
    _scheduler,
):
    cli, args, seen = _scheduler
    symbols = [f"{i:06d}" for i in range(1, 257)]
    args.symbols = ",".join(symbols)
    for _ in range(2):
        with pytest.raises(_StopAfterProvisional):
            cli._run_scheduled_legacy(args)
    assert seen["fetches"] == [symbols, symbols]
    assert len(seen["scores"]) == 1
    assert seen["metadata"].resolved_symbol_count == 256
    assert seen["metadata"].fetched_frame_count == 257
    assert seen["metadata"].screened_count == 7
    assert (
        freeze.validate_candidate_scan_receipt(
            args.output_csv, expected_symbols=symbols, minimum_valid_ratio=0.5
        )
        == 256
    )
    next_symbols = [f"{i:06d}" for i in range(1001, 1257)]
    args.symbols = ",".join(next_symbols)
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    assert seen["fetches"][-1] == next_symbols
    assert len(seen["scores"]) == 2


def test_scheduled_scan_does_not_freeze_seven_of_256_failed_coverage(_scheduler):
    cli, args, seen = _scheduler
    symbols = [f"{i:06d}" for i in range(1, 257)]
    args.symbols = ",".join(symbols)
    seen["valid_count"] = 7
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    assert seen["metadata"].resolved_symbol_count == 256
    assert seen["metadata"].fetched_frame_count == 8
    with pytest.raises(freeze.CandidateSnapshotError, match="7/256"):
        freeze.validate_candidate_scan_receipt(
            args.output_csv, expected_symbols=symbols, minimum_valid_ratio=0.5
        )
    assert not list(freeze.candidate_snapshot_path().parent.glob("*_batches/*.jsonl"))
    seen["valid_count"] = 256
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    assert len(seen["scores"]) == 2


@pytest.mark.parametrize("task_id", ["daily", "scheduled", ""])
def test_scheduled_close_fetches_own_pool_when_intraday_roster_is_frozen(
    _scheduler, monkeypatch, task_id,
):
    cli, args, seen = _scheduler
    intraday_symbols = [f"{i:06d}" for i in range(1, 257)]
    intraday = _batch("legacy-intraday", intraday_symbols, [_pick("000001")])
    freeze.session_candidate_view(intraday, final_picks=list(intraday.final_picks))
    stable_bytes = freeze.candidate_snapshot_path().read_bytes()
    monkeypatch.setenv("AQSP_RUN_TASK_ID", task_id)
    monkeypatch.setattr(
        cli, "_fetch_frames_for_cli_with_metadata", cli._fetch_special_strategy_frames
    )
    close_symbols = [f"{i:06d}" for i in range(1001, 1601)]
    args.symbols = ",".join(close_symbols)
    seen["valid_count"] = len(close_symbols)
    for _ in range(2):
        with pytest.raises(_StopAfterProvisional):
            cli._run_scheduled_legacy(args)
    assert seen["fetches"] == [close_symbols, close_symbols]
    assert len(seen["scores"]) == 1
    assert seen["metadata"].resolved_symbol_count == 600
    record = json.loads(freeze.candidate_close_snapshot_path().read_text())
    snapshot = freeze.load_candidate_snapshot(DAY, record["request_fingerprint"],
                                              freeze.candidate_close_snapshot_path())
    assert snapshot.resolved_symbols == tuple(close_symbols)
    finalized = freeze.freeze_final_candidates(
        snapshot, list(snapshot.screened_picks), freeze.candidate_close_snapshot_path()
    )
    assert "000001" not in {pick.symbol for pick in finalized.final_picks}
    assert freeze.candidate_snapshot_path().read_bytes() == stable_bytes
    assert not freeze.candidate_increment_path().exists()


@pytest.mark.parametrize("mutation", ["symbols", "limit", "thresholds"])
def test_scheduled_close_rejects_changed_identity_when_same_session_is_cached(
    _scheduler, monkeypatch, capsys, mutation,
):
    cli, args, seen = _scheduler
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")
    monkeypatch.setattr(
        cli, "_fetch_frames_for_cli_with_metadata", cli._fetch_special_strategy_frames
    )
    args.symbols = "600001,600002"
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    close_bytes = freeze.candidate_close_snapshot_path().read_bytes()
    if mutation == "symbols":
        args.symbols = "600003"
    elif mutation == "limit":
        args.limit += 1
    else:
        original = cli.load_thresholds()
        changed = replace(
            original, composite=replace(
                original.composite,
                min_total_score=original.composite.min_total_score + 1,
            ),
        )
        assert changed.version == original.version
        monkeypatch.setattr(cli, "load_thresholds", lambda: changed)
    assert cli._run_scheduled_legacy(args) == 1
    assert "配置身份冲突" in capsys.readouterr().out
    assert len(seen["fetches"]) == 1
    assert freeze.candidate_close_snapshot_path().read_bytes() == close_bytes


def test_close_cache_preserves_corrupt_intraday_record_without_reusing_it(
    _scheduler, monkeypatch,
):
    cli, args, seen = _scheduler
    stable = freeze.candidate_snapshot_path()
    stable.write_text("corrupt legacy record\n")
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "daily")
    monkeypatch.setattr(
        cli, "_fetch_frames_for_cli_with_metadata", cli._fetch_special_strategy_frames
    )
    args.symbols = "600001"
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    assert stable.read_text() == "corrupt legacy record\n"
    assert len(seen["scores"]) == 1
    close = freeze.candidate_close_snapshot_path()
    close.write_text("corrupt closing record\n")
    assert cli._run_scheduled_legacy(args) == 1
    assert len(seen["scores"]) == 1


def test_scheduled_scan_preserves_full_coverage_when_frozen_candidates_are_empty(
    _scheduler,
):
    cli, args, seen = _scheduler
    symbols = [f"{i:06d}" for i in range(1, 257)]
    args.symbols = ",".join(symbols)
    seen["candidate_count"] = 0
    for _ in range(2):
        with pytest.raises(_StopAfterProvisional):
            cli._run_scheduled_legacy(args)
    assert seen["fetches"] == [symbols, symbols]
    assert len(seen["scores"]) == 1
    assert seen["metadata"].screened_count == 0
    assert (
        freeze.validate_candidate_scan_receipt(
            args.output_csv, expected_symbols=symbols, minimum_valid_ratio=0.5
        )
        == 256
    )


def test_scan_freeze_does_not_promote_interrupted_screened_candidates():
    batch = _batch("original", ["600001"], [_pick("600001")])
    unfinished = replace(batch, finalized=False, final_picks=())
    path = freeze.candidate_snapshot_path()
    path.write_text(json.dumps(freeze._serialize_snapshot(unfinished)) + "\n")
    original_bytes = path.read_bytes()
    other = _batch("other", ["600002"], [_pick("600002")])
    with pytest.raises(freeze.CandidateSnapshotError, match="尚未完成最终筛选"):
        freeze.session_candidate_view(other, final_picks=list(other.final_picks))
    with pytest.raises(freeze.CandidateSnapshotError, match="尚未完成最终筛选"):
        freeze.session_candidate_view()
    assert path.read_bytes() == original_bytes
    result = freeze.session_candidate_view(batch, final_picks=[])
    assert result == []
    assert freeze.load_candidate_snapshot(DAY, "original").final_picks == ()


def test_candidate_observation_refreshes_only_proven_source_and_preserves_scoring():
    original = _pick("600001", 88)
    frame = _frame("600001")
    frame["close"] = 11.5
    refreshed = freeze.refresh_candidate_observations([original], {"600001": frame})[0]
    assert refreshed.close == 11.5
    assert refreshed.score == original.score
    assert refreshed.strategies == original.strategies
    assert refreshed.stop_loss == original.stop_loss
    assert refreshed.ideal_buy == original.ideal_buy
    assert refreshed.metrics["data_fetched_at"] == NOW.isoformat()
    assert refreshed.metrics["data_timestamp_source"] == "bar_time"
    assert refreshed.metrics["data_source_time"] == DAY
    assert refreshed.metrics["data_source_time_precision"] == "day"
    assert refreshed.metrics["freshness"] == "fresh"
    missing = freeze.refresh_candidate_observations([refreshed], {})[0]
    assert missing.metrics["freshness"] == "unknown"
    assert missing.metrics["data_fetched_at"] == ""
    assert missing.metrics["data_timestamp_source"] == ""
    assert missing.score == 88
    assert missing.close == 11.5


def test_provisional_stable_view_refreshes_provenance_without_mutating_freeze(
    _scheduler,
):
    cli, args, seen = _scheduler
    stable = _batch("legacy", ["600001"], [_pick("600001", 88)])
    freeze.session_candidate_view(stable, final_picks=list(stable.final_picks))
    original_bytes = freeze.candidate_snapshot_path().read_bytes()
    args.symbols = "600001,600002"
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    rows = pd.read_csv(args.output_csv, dtype=str).fillna("")
    candidates = rows[rows.symbol != "__RUN__"]
    assert candidates.symbol.tolist() == ["600001"]
    assert float(candidates.iloc[0].score) == 88
    assert candidates.iloc[0].data_fetched_at == NOW.isoformat()
    assert candidates.iloc[0].data_timestamp_source == "bar_time"
    assert candidates.iloc[0].freshness == "fresh"
    assert freeze.candidate_snapshot_path().read_bytes() == original_bytes


def test_rotating_scan_follows_stable_candidates_without_counting_them_as_batch_coverage(
    _scheduler,
):
    cli, args, seen = _scheduler
    frozen = _batch("legacy-stable", ["600001"], [_pick("600001", 88)])
    freeze.session_candidate_view(frozen, final_picks=list(frozen.final_picks))
    selected = [f"{index:06d}" for index in range(1001, 1257)]
    args.symbols = ",".join(selected)
    seen["valid_count"] = 300
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    assert seen["fetches"][-1] == [*selected, "600001"]
    assert seen["scores"][-1] == selected
    receipt = json.loads(open(args.output_csv + ".scan.json").read())
    assert receipt["scanned_symbols"] == selected
    assert receipt["supplemental_fetched_symbols"] == ["600001"]
    assert len(receipt["valid_quotes"]) == 256
    assert seen["metadata"].resolved_symbol_count == 256
    assert seen["metadata"].fetched_frame_count == 258
    rows = pd.read_csv(args.output_csv, dtype=str).fillna("")
    stable = rows[rows.symbol == "600001"].iloc[0]
    assert stable.freshness == "fresh"
    assert stable.data_fetched_at == NOW.isoformat()
    assert float(stable.score) == 88


def test_cursor_rejects_stale_same_size_receipt_when_next_batch_is_selected(tmp_path):
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    first = cursor.select(["600001", "600002"], trade_date=NOW.date(), batch_size=1)
    cursor.commit_current(scanned_count=1, expected_symbols=first.symbols)
    second = cursor.select(["600001", "600002"], trade_date=NOW.date(), batch_size=1)
    selected_bytes = cursor.path.read_bytes()
    with pytest.raises(ValueError, match="does not match"):
        cursor.commit_current(scanned_count=1, expected_symbols=first.symbols)
    assert cursor.path.read_bytes() == selected_bytes
    cursor.commit_current(scanned_count=1, expected_symbols=second.symbols)


def test_scheduled_scan_does_not_cache_reduced_resolver_membership(
    _scheduler, monkeypatch
):
    cli, args, seen = _scheduler
    selected = [f"{index:06d}" for index in range(1, 257)]
    args.symbols = ",".join(selected)
    monkeypatch.setattr(cli, "_resolve_run_symbols", lambda *a, **k: selected[:7])
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    assert seen["fetches"][-1] == selected[:7]
    assert not list(freeze.candidate_snapshot_path().parent.glob("*_batches/*.jsonl"))
    monkeypatch.setattr(cli, "_resolve_run_symbols", lambda *a, **k: selected)
    with pytest.raises(_StopAfterProvisional):
        cli._run_scheduled_legacy(args)
    assert seen["fetches"][-1] == selected
    assert len(seen["scores"]) == 2
