"""Parameter provenance through real local artifact and dashboard boundaries."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from aqsp.cli import _attach_runtime_weight_snapshot, _runtime_weight_snapshot
from aqsp.core.types import PickResult
from aqsp.portfolio.parameter_snapshot import (
    ParameterSnapshot,
    build_parameter_snapshot,
    parameter_snapshot_from_runtime_weights,
)
from aqsp.portfolio.snapshot import save_snapshot
from aqsp.report import to_dataframe
from aqsp.strategies.thresholds import Thresholds
from aqsp.web.data_provider import DashboardDataProvider
from aqsp.web.home_snapshot import (
    HomeDashboardSnapshot,
    HomeSnapshotCandidate,
    HomeSnapshotColdstart,
    HomeSnapshotDay,
    HomeSnapshotIndex,
    HomeSnapshotSource,
    load_home_dashboard_snapshot,
    load_home_snapshot_index,
    write_home_dashboard_snapshot,
    write_home_snapshot_index,
)
from backend.aqsp_bridge import AQSPSnapshotUnavailable, _parse_snapshot


def _weights(version: str = "test-1") -> dict[str, object]:
    return _runtime_weight_snapshot(
        thresholds=Thresholds(version=version, effective_from="2026-10-13"),
        regime="stable_bull",
        strategy_weights={"momentum": 0.3, "price_volume_corr": 0.3},
        strategy_weight_reasons={},
    )


def _identity(version: str = "test-1") -> ParameterSnapshot:
    result = parameter_snapshot_from_runtime_weights(_weights(version))
    assert result is not None
    return result


def _snapshot(day: str = "2026-10-23") -> HomeDashboardSnapshot:
    return HomeDashboardSnapshot(
        schema_version="v1",
        generated_at=f"{day}T09:15:00+08:00",
        stale_after=f"{day}T23:59:00+08:00",
        selected_date=day,
        available_dates=(day,),
        candidates=(
            HomeSnapshotCandidate("600001", "样本", 70, "研究观察", "复核", "测试"),
        ),
        summaries=(),
        source=HomeSnapshotSource("fixture", day, 0, "fresh"),
        coldstart=HomeSnapshotColdstart("ready", "合成数据"),
        parameter_snapshot=_identity(),
    )


def _provider(tmp_path: Path) -> DashboardDataProvider:
    return DashboardDataProvider(
        ledger_path=str(tmp_path / "ledger.jsonl"),
        intraday_latest_path=str(tmp_path / "latest.csv"),
        intraday_ledger_path=str(tmp_path / "intraday.jsonl"),
        paper_ledger_path=str(tmp_path / "paper.jsonl"),
        logs_path=str(tmp_path / "logs"),
        bt_logs_dir=str(tmp_path / "bt"),
        reports_dir=str(tmp_path / "reports"),
        debate_results_path=str(tmp_path / "debates.jsonl"),
    )


def _row(weights: object = None, **overrides: object) -> dict[str, object]:
    return {
        "symbol": "600001",
        "signal_date": "2026-10-23",
        "task_id": "main_chain",
        "strategy_weight_snapshot": _weights() if weights is None else weights,
        **overrides,
    }


def _write_rows(tmp_path: Path, rows: list[dict[str, object]]) -> None:
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_parameter_snapshot_is_deterministic_when_mapping_order_changes() -> None:
    thresholds = Thresholds(version="test", effective_from="2026-10-13")
    first = build_parameter_snapshot(thresholds, {"a": 0.2, "b": 0.8})
    second = build_parameter_snapshot(thresholds, {"b": 0.8, "a": 0.2})
    assert first == second
    assert first is not None
    assert first.version == "test"
    assert first.effective_from == "2026-10-13"


def test_parameter_snapshot_changes_when_actual_config_or_runtime_weights_change() -> (
    None
):
    thresholds = Thresholds(version="test", effective_from="2026-10-13")
    first = build_parameter_snapshot(thresholds, {"momentum": 0.3})
    assert first != build_parameter_snapshot(thresholds, {"momentum": 0.4})
    changed = replace(
        thresholds,
        composite=replace(
            thresholds.composite,
            momentum_weight=thresholds.composite.momentum_weight + 0.1,
        ),
    )
    assert first != build_parameter_snapshot(changed, {"momentum": 0.3})


def test_parameter_snapshot_fingerprint_changes_when_unrounded_runtime_input_changes() -> (
    None
):
    thresholds = Thresholds(version="test", effective_from="2026-10-13")
    snapshots = [
        _runtime_weight_snapshot(
            thresholds=thresholds,
            regime="stable_bull",
            strategy_weights={"momentum": value},
            strategy_weight_reasons={},
        )
        for value in (0.12345671, 0.12345672)
    ]
    assert snapshots[0]["strategy_weights"] == snapshots[1]["strategy_weights"]
    assert snapshots[0]["parameter_snapshot"] != snapshots[1]["parameter_snapshot"]


def test_parameter_snapshot_is_unverified_when_effective_date_is_missing() -> None:
    assert build_parameter_snapshot(Thresholds(), {}) is None
    assert "parameter_snapshot" not in _runtime_weight_snapshot(
        thresholds=Thresholds(),
        regime="",
        strategy_weights={},
        strategy_weight_reasons={},
    )


def test_parameter_snapshot_is_unverified_when_recorded_version_disagrees() -> None:
    weights = {**_weights(), "thresholds_version": "another-version"}
    assert parameter_snapshot_from_runtime_weights(weights) is None


@pytest.mark.parametrize(
    "payload",
    [
        "not a dictionary",
        "__import__('os').getcwd()",
        "[]",
        "x" * 65537,
        {},
        {"parameter_snapshot": {"version": "test"}},
    ],
)
def test_parameter_snapshot_is_unverified_when_artifact_is_legacy_or_malformed(
    payload: object,
) -> None:
    assert parameter_snapshot_from_runtime_weights(payload) is None


@pytest.mark.parametrize("encoding", [dict, str, json.dumps])
def test_parameter_snapshot_reads_actual_weight_artifact_when_ledger_or_csv(
    encoding,
) -> None:
    assert parameter_snapshot_from_runtime_weights(encoding(_weights())) == _identity()


def test_parameter_snapshot_is_persisted_when_real_candidate_is_rendered_to_csv(
    tmp_path: Path,
) -> None:
    pick = PickResult(
        symbol="600001",
        name="样本",
        date="2026-10-23",
        close=10,
        score=70,
        rating="watch",
        entry_type="next_open",
        ideal_buy=10,
        stop_loss=9.5,
        take_profit=11,
        position="10%",
    )
    picks = _attach_runtime_weight_snapshot(
        [pick],
        thresholds=Thresholds(version="test-1", effective_from="2026-10-13"),
        regime="stable_bull",
        strategy_weights={"momentum": 0.3, "price_volume_corr": 0.3},
        strategy_weight_reasons={},
    )
    assert picks[0].score == pick.score
    to_dataframe(picks).to_csv(tmp_path / "latest.csv", index=False)
    provider = _provider(tmp_path)
    assert (
        provider.parameter_snapshot_for_candidates(
            "intraday", "2026-10-23", ("600001",)
        )
        == _identity()
    )


def test_parameter_snapshot_uses_exact_artifact_when_task_and_date_match(
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path,
        [
            _row(),
            _row(_weights("other"), task_id="morning_breakout"),
            _row(_weights("old"), signal_date="2026-10-22"),
        ],
    )
    assert (
        _provider(tmp_path).parameter_snapshot_for_candidates(
            "main_chain", "2026-10-23", ("600001",)
        )
        == _identity()
    )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [_row({})],
        [_row(), _row(_weights("other"))],
        [_row(symbol="600002")],
        [_row(_weights(), signal_date="2026-10-12")],
    ],
)
def test_parameter_snapshot_is_unverified_when_source_is_missing_mixed_or_wrong_date(
    tmp_path: Path, rows
) -> None:
    _write_rows(tmp_path, rows)
    assert (
        _provider(tmp_path).parameter_snapshot_for_candidates(
            "main_chain", "2026-10-23", ("600001",)
        )
        is None
    )


def test_parameter_snapshot_is_unverified_when_any_candidate_lacks_provenance(
    tmp_path: Path,
) -> None:
    _write_rows(tmp_path, [_row(), _row({}, symbol="600002")])
    assert (
        _provider(tmp_path).parameter_snapshot_for_candidates(
            "main_chain", "2026-10-23", ("600001", "600002")
        )
        is None
    )


def test_parameter_snapshot_preserves_identity_when_same_trading_session_refreshes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    first = _snapshot()
    write_home_dashboard_snapshot(path, first)
    write_home_dashboard_snapshot(
        path,
        replace(
            first,
            generated_at="2026-10-23T14:00:00+08:00",
            parameter_snapshot=_identity("changed"),
        ),
    )
    current = load_home_dashboard_snapshot(path)
    assert current is not None
    assert current.parameter_snapshot == first.parameter_snapshot
    assert current.generated_at.endswith("14:00:00+08:00")
    next_day = replace(_snapshot("2026-10-26"), parameter_snapshot=_identity("changed"))
    write_home_dashboard_snapshot(path, next_day)
    assert (
        load_home_dashboard_snapshot(path).parameter_snapshot
        == next_day.parameter_snapshot
    )


def test_parameter_snapshot_keeps_legacy_unverified_when_same_day_is_rebuilt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    write_home_dashboard_snapshot(path, replace(_snapshot(), parameter_snapshot=None))
    write_home_dashboard_snapshot(path, _snapshot())
    assert load_home_dashboard_snapshot(path).parameter_snapshot is None


def test_parameter_snapshot_roundtrips_when_snapshot_index_and_bridge_read_it(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    path = tmp_path / "index.json"
    index = HomeSnapshotIndex(
        schema_version="v1-index",
        generated_at=snapshot.generated_at,
        days=(HomeSnapshotDay(snapshot.selected_date, snapshot),),
        selected_date=snapshot.selected_date,
        stale_after=snapshot.stale_after,
    )
    write_home_snapshot_index(path, index)
    loaded = load_home_snapshot_index(path)
    assert loaded is not None
    assert loaded.days[0].snapshot.parameter_snapshot == snapshot.parameter_snapshot
    assert (
        _parse_snapshot(
            json.loads(loaded.days[0].snapshot.to_json())
        ).parameter_snapshot
        == snapshot.parameter_snapshot
    )
    legacy = json.loads(snapshot.to_json())
    legacy.pop("parameter_snapshot")
    assert _parse_snapshot(legacy).parameter_snapshot is None


@pytest.mark.parametrize(
    "identity",
    [
        {"version": "v", "effective_from": "2026-10-13", "fingerprint": "fake"},
        {"version": "v", "effective_from": "2026-10-24", "fingerprint": "a" * 64},
        {"version": "v", "effective_from": "2026-99-99", "fingerprint": "a" * 64},
    ],
)
def test_parameter_snapshot_bridge_rejects_metadata_when_invalid_or_not_yet_effective(
    identity,
) -> None:
    payload = json.loads(_snapshot().to_json())
    payload["parameter_snapshot"] = identity
    with pytest.raises(AQSPSnapshotUnavailable, match="参数快照元数据无效"):
        _parse_snapshot(payload)


def test_parameter_snapshot_pick_archive_freezes_identity_when_same_day_repeats(
    tmp_path: Path,
) -> None:
    class Pick:
        symbol = "600001"
        name = "样本"
        score = 70
        metrics = {"strategy_weight_snapshot": _weights()}

    path = tmp_path / "picks.jsonl"
    save_snapshot([Pick()], str(path), "2026-10-23")
    saved = json.loads(path.read_text())
    assert saved["parameter_snapshot"] == asdict(_identity())
    original = path.read_bytes()
    Pick.metrics = {"strategy_weight_snapshot": _weights("changed")}
    save_snapshot([Pick()], str(path), "2026-10-23")
    assert path.read_bytes() == original
