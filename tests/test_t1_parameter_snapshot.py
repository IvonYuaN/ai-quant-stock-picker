"""Parameter provenance through real local artifact and dashboard boundaries."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aqsp.cli import _attach_runtime_weight_snapshot, _runtime_weight_snapshot
from aqsp.core.types import PickResult
from aqsp.portfolio.parameter_snapshot import (
    ParameterSnapshot,
    build_parameter_snapshot,
    parameter_snapshot_from_candidate_row,
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
    HomeSnapshotTechnicalMetric,
    load_home_dashboard_snapshot,
    load_home_snapshot_index,
    write_home_dashboard_snapshot,
    write_home_snapshot_index,
)
from backend.aqsp_bridge import AQSPSnapshotUnavailable, _parse_snapshot
from scripts import write_home_snapshot


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
    cards = provider._build_detail_cards(provider._task_signal_rows("intraday"))
    assert cards[0].parameter_snapshot == _identity()
    assert cards[0].score == pick.score


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
    provider = _provider(tmp_path)
    rows = [
        row
        for row in provider._task_signal_rows("main_chain")
        if row.get("signal_date") == "2026-10-23"
    ]
    assert provider._build_detail_cards(rows)[0].parameter_snapshot == _identity()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [_row({})],
        [_row(), _row(_weights("other"), symbol="600002")],
        [_row(_weights(), signal_date="2026-10-12")],
    ],
)
def test_parameter_snapshot_is_unverified_when_source_is_missing_mixed_or_wrong_date(
    tmp_path: Path, rows
) -> None:
    _write_rows(tmp_path, rows)
    provider = _provider(tmp_path)
    cards = provider._build_detail_cards(
        [dict(row, score=70, rating="watch") for row in rows]
    )
    _, identity = write_home_snapshot._snapshot_candidates_with_parameters(
        SimpleNamespace(task_view=SimpleNamespace(detail_cards=cards), spotlights=()),
        selected_date="2026-10-23",
    )
    assert identity is None


def test_parameter_snapshot_is_unverified_when_any_candidate_lacks_provenance(
    tmp_path: Path,
) -> None:
    _write_rows(tmp_path, [_row(), _row({}, symbol="600002")])
    cards = _provider(tmp_path)._build_detail_cards(
        [
            dict(row, score=70, rating="watch")
            for row in (_row(), _row({}, symbol="600002"))
        ]
    )
    _, identity = write_home_snapshot._snapshot_candidates_with_parameters(
        SimpleNamespace(task_view=SimpleNamespace(detail_cards=cards), spotlights=()),
        selected_date="2026-10-23",
    )
    assert identity is None


def test_parameter_snapshot_rejects_identity_when_outer_ledger_version_conflicts() -> (
    None
):
    assert (
        parameter_snapshot_from_candidate_row(_row(thresholds_version="other")) is None
    )
    assert (
        parameter_snapshot_from_candidate_row(_row(thresholds_version="test-1"))
        == _identity()
    )


@pytest.mark.parametrize(
    "dates",
    [
        {"signal_date": ""},
        {"signal_date": None},
        {"signal_date": "invalid"},
        {"signal_date": "2026-02-30"},
        {"signal_date": "2026-10-23", "date": "2026-10-22"},
        {"signal_date": "2026-10-23", "date": "invalid"},
        {"signal_date": "2026-10-23", "date": 20261023},
    ],
)
def test_parameter_snapshot_is_unverified_when_source_date_is_missing_or_conflicting(
    dates: dict[str, object],
) -> None:
    assert parameter_snapshot_from_candidate_row(_row(**dates)) is None


def test_parameter_snapshot_certifies_identity_when_both_source_dates_agree() -> None:
    assert parameter_snapshot_from_candidate_row(_row(date="2026-10-23")) == _identity()


def test_parameter_snapshot_follows_actual_spotlight_when_other_task_wins(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    card = provider._build_detail_cards(
        [_row(score=70, rating="watch", reasons=["旧规则依据"])]
    )[0]
    spotlight = provider._build_same_day_spotlight(
        "600001",
        {
            "row": _row(
                _weights("new-task"),
                task_id="morning_breakout",
                score=99,
                rating="buy",
                research_recommendation=True,
                reasons=["新规则依据"],
            ),
            "task_labels": ("盘前突破",),
        },
    )
    selected, identity = write_home_snapshot._snapshot_candidates_with_parameters(
        SimpleNamespace(
            task_view=SimpleNamespace(detail_cards=(card,)), spotlights=(spotlight,)
        ),
        selected_date="2026-10-23",
    )
    assert selected[0].score == 99
    assert selected[0].deterministic_reasons == spotlight.reasons
    assert identity == _identity("new-task")
    assert identity != card.parameter_snapshot


def test_parameter_snapshot_does_not_certify_backfilled_reasons_from_other_row(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    row = _row(score=99, rating="buy")
    spotlight = provider._build_same_day_spotlight(
        "600001",
        {
            "row": row,
            "task_labels": ("盘前突破",),
            "entries": [
                {
                    "row": _row(_weights("other"), score=70, reasons=["旧规则依据"]),
                    "task_id": "main_chain",
                }
            ],
        },
    )
    assert spotlight.score == 99
    assert spotlight.reasons
    assert spotlight.parameter_snapshot is None


def test_parameter_snapshot_preserves_identity_when_same_trading_session_refreshes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    original = replace(
        _snapshot().candidates[0],
        deterministic_reasons=("首次规则",),
        strategies=("first",),
        score_breakdown=("首次评分明细",),
        technical_metrics=(
            HomeSnapshotTechnicalMetric("close", "现价", "10.00"),
            HomeSnapshotTechnicalMetric("rsi12", "RSI12", "50.0"),
        ),
    )
    first = replace(_snapshot(), candidates=(original,))
    write_home_dashboard_snapshot(path, first)
    write_home_dashboard_snapshot(
        path,
        replace(
            first,
            generated_at="2026-10-23T14:00:00+08:00",
            parameter_snapshot=_identity("changed"),
            candidates=(
                replace(
                    original,
                    score=90,
                    research_status="纸面复核",
                    deterministic_reasons=("新规则",),
                    strategies=("new",),
                    score_breakdown=("新评分明细",),
                    technical_metrics=(
                        HomeSnapshotTechnicalMetric("close", "现价", "12.00"),
                        HomeSnapshotTechnicalMetric("rsi12", "RSI12", "80.0"),
                    ),
                    data_fetched_at="2026-10-23T14:00:00+08:00",
                ),
            ),
        ),
    )
    current = load_home_dashboard_snapshot(path)
    assert current is not None
    assert current.parameter_snapshot == first.parameter_snapshot
    candidate = current.candidates[0]
    assert candidate.score == 70
    assert candidate.deterministic_reasons == original.deterministic_reasons
    assert candidate.strategies == original.strategies
    assert candidate.score_breakdown == original.score_breakdown
    assert [(metric.key, metric.value) for metric in candidate.technical_metrics] == [
        ("close", "12.00"),
        ("rsi12", "50.0"),
    ]
    assert candidate.data_fetched_at.endswith("14:00:00+08:00")
    assert candidate.research_status == "参数身份变化 · 仅观察"
    assert candidate.context == original.context
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


@pytest.mark.parametrize("incoming_identity", [None, _identity(), _identity("changed")])
@pytest.mark.parametrize("pre_frozen", [False, True])
def test_parameter_snapshot_keeps_score_binding_when_refresh_claims_to_be_frozen(
    tmp_path: Path,
    incoming_identity: ParameterSnapshot | None,
    pre_frozen: bool,
) -> None:
    first = _snapshot()
    current = replace(
        first,
        generated_at="2026-10-23T14:00:00+08:00",
        parameter_snapshot=incoming_identity,
        frozen_at=first.generated_at if pre_frozen else "",
        candidates=(
            replace(first.candidates[0], score=90, deterministic_reasons=("新依据",)),
        ),
    )
    single_path, index_path = tmp_path / "home.json", tmp_path / "index.json"
    write_home_dashboard_snapshot(single_path, first)
    write_home_snapshot_index(
        index_path,
        HomeSnapshotIndex(
            schema_version="v1-index",
            generated_at=first.generated_at,
            days=(HomeSnapshotDay(first.selected_date, first),),
            selected_date=first.selected_date,
            stale_after=first.stale_after,
        ),
    )
    write_home_dashboard_snapshot(single_path, current)
    persisted = load_home_dashboard_snapshot(single_path)
    assert persisted is not None
    assert persisted.parameter_snapshot == first.parameter_snapshot
    assert persisted.candidates[0].score == 70
    assert (
        persisted.candidates[0].deterministic_reasons
        == first.candidates[0].deterministic_reasons
    )
    write_home_snapshot_index(
        index_path,
        HomeSnapshotIndex(
            schema_version="v1-index",
            generated_at=persisted.generated_at,
            days=(HomeSnapshotDay(persisted.selected_date, persisted),),
            selected_date=persisted.selected_date,
            stale_after=persisted.stale_after,
        ),
    )
    assert (
        load_home_snapshot_index(index_path).snapshot_for_date(first.selected_date)
        == persisted
    )


def test_parameter_snapshot_preserves_new_risk_when_parameters_change(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    first = _snapshot()
    write_home_dashboard_snapshot(path, first)
    write_home_dashboard_snapshot(
        path,
        replace(
            first,
            parameter_snapshot=None,
            candidates=(
                replace(
                    first.candidates[0],
                    score=90,
                    research_status="数据过期 · 阻塞观察",
                    next_step="等待新行情",
                    freshness="stale",
                ),
            ),
        ),
    )
    current = load_home_dashboard_snapshot(path)
    assert current.candidates[0].score == 70
    assert current.candidates[0].research_status == "数据过期 · 阻塞观察"
    assert current.candidates[0].freshness == "stale"
    assert "等待新行情" in current.candidates[0].next_step


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


def _original_technical_source() -> dict[str, object]:
    return {"source": "original_source", "as_of": "2026-10-23", "precision": "day", "timestamp_source": "bar_date",
            "research_date": "2026-10-23", "parameter_snapshot": None,
            "metrics": {"volume_ratio": 1.424, "ret5_pct": 3.5, "macd_hist": 0.1234}}


def test_parameter_snapshot_transports_original_technical_source_through_actual_csv_and_writer(tmp_path) -> None:
    from scripts import write_home_snapshot
    source = _original_technical_source()
    pick = PickResult("600001", "样本", "2026-10-23", 10, 70, "watch", "next_open", 10, 9.5, 11, "10%",
                      metrics={"technical_source": source, "volume_ratio": 1.424, "ret5_pct": 3.5, "macd_hist": 0.1234,
                               "data_source": "latest_quote_source", "data_fetched_at": "2026-10-23T15:00:00+08:00"})
    picks = _attach_runtime_weight_snapshot([pick], thresholds=Thresholds(version="test-1", effective_from="2026-10-13"),
        regime="stable_bull", strategy_weights={"momentum": 0.3, "price_volume_corr": 0.3}, strategy_weight_reasons={})
    assert source["parameter_snapshot"] is None
    assert picks[0].metrics["technical_source"]["parameter_snapshot"] == asdict(_identity())
    to_dataframe(picks).to_csv(tmp_path / "latest.csv", index=False)
    provider = _provider(tmp_path)
    cards = provider._build_detail_cards(provider._task_signal_rows("intraday"))
    assert len(cards) == 1 and cards[0].technical_basis is not None
    basis = cards[0].technical_basis
    assert basis.parameter_snapshot == _identity()
    assert basis.source == "original_source" and basis.as_of == "2026-10-23" and basis.precision == "day"
    assert {fact.key: fact.value for fact in basis.facts} == {"volume_ratio": 1.42, "ret5_pct": 3.5, "macd_hist": 0.123}
    assert cards[0].macd_hist == 0.1234
    snapshot_candidate = write_home_snapshot._snapshot_candidate(cards[0], selected_date="2026-10-23")
    assert snapshot_candidate.technical_basis == basis
    assert snapshot_candidate.data_source == "latest_quote_source"
    assert snapshot_candidate.data_fetched_at == "2026-10-23T15:00:00+08:00"


@pytest.mark.parametrize("bad", [True, "1.42", float("inf"), float("nan")])
def test_parameter_snapshot_rejects_original_numeric_source_when_typed_value_invalid(bad) -> None:
    from aqsp.web.data_provider import _technical_basis_for_row
    source = _original_technical_source()
    source["parameter_snapshot"] = asdict(_identity())
    source["metrics"]["volume_ratio"] = bad
    assert _technical_basis_for_row(_row(technical_source=source)) is None


def test_parameter_snapshot_does_not_rebind_original_indicator_identity_when_new_runtime_attaches() -> None:
    source = _original_technical_source()
    source["parameter_snapshot"] = asdict(_identity("old"))
    pick = PickResult("600001", "样本", "2026-10-23", 10, 70, "watch", "next_open", 10, 9.5, 11, "10%", metrics={"technical_source": source})
    updated = _attach_runtime_weight_snapshot([pick], thresholds=Thresholds(version="test-1", effective_from="2026-10-13"),
        regime="stable_bull", strategy_weights={"momentum": 0.3, "price_volume_corr": 0.3}, strategy_weight_reasons={})[0]
    assert updated.metrics["technical_source"]["parameter_snapshot"] == asdict(_identity("old"))
    from aqsp.web.data_provider import _technical_basis_for_row
    row = to_dataframe([updated]).iloc[0].to_dict()
    assert _technical_basis_for_row(row) is None
