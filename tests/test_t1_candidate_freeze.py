"""Trading-session roster invariants on the actual homepage write/read boundary."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aqsp.portfolio.snapshot import load_snapshot, save_snapshot, snapshot_trading_date
from aqsp.web.home_snapshot import (
    HomeDashboardSnapshot,
    HomeSnapshotCandidate,
    HomeSnapshotColdstart,
    HomeSnapshotDay,
    HomeSnapshotIndex,
    HomeSnapshotRecommendationGate,
    HomeSnapshotSource,
    HomeSnapshotDebate,
    is_home_recommendation,
    load_home_dashboard_snapshot,
    load_home_snapshot_index,
    write_home_dashboard_snapshot,
    write_home_snapshot_index,
)
from backend.aqsp_bridge import _parse_snapshot
from scripts import daily_pipeline, write_home_snapshot


def _candidate(symbol: str, score: float = 70.0) -> HomeSnapshotCandidate:
    return HomeSnapshotCandidate(
        symbol=symbol,
        display_name=f"样本 {symbol}",
        score=score,
        research_status="研究观察",
        next_step="等待证据",
        context="合成测试数据",
        deterministic_reasons=("规则证据",),
    )


def _snapshot(day: str, time: str, symbols: tuple[str, ...]) -> HomeDashboardSnapshot:
    return HomeDashboardSnapshot(
        schema_version="v1",
        generated_at=f"{day}T{time}:00+08:00",
        selected_date=day,
        available_dates=(day,),
        candidates=tuple(_candidate(symbol) for symbol in symbols),
        summaries=(),
        source=HomeSnapshotSource("fixture", day, 0, "fresh"),
        coldstart=HomeSnapshotColdstart("ready", "合成数据"),
        stale_after=f"{day}T23:59:00+08:00",
    )


def _index(snapshot: HomeDashboardSnapshot) -> HomeSnapshotIndex:
    return HomeSnapshotIndex(
        schema_version="v1-index",
        generated_at=snapshot.generated_at,
        stale_after=snapshot.stale_after,
        days=(HomeSnapshotDay(snapshot.selected_date, snapshot),),
        selected_date=snapshot.selected_date,
    )


def test_home_freeze_keeps_roster_when_three_intraday_runs_differ(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    first = _snapshot("2026-10-23", "09:15", ("600001", "600002"))
    write_home_dashboard_snapshot(path, first)
    for time, symbols in (
        ("11:00", ("600003", "600002")),
        ("14:00", ("600002", "600001", "600004")),
    ):
        write_home_dashboard_snapshot(path, _snapshot("2026-10-23", time, symbols))
        loaded = load_home_dashboard_snapshot(path)
        assert loaded is not None
        assert [candidate.symbol for candidate in loaded.candidates] == [
            "600001",
            "600002",
        ]
        assert loaded.frozen_at == first.generated_at
        assert loaded.generated_at == f"2026-10-23T{time}:00+08:00"
    assert [(item.symbol, item.status) for item in loaded.candidate_changes] == [
        ("600004", "added")
    ]


def test_home_freeze_preserves_absent_candidate_and_latest_blocking_gate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    write_home_dashboard_snapshot(
        path, _snapshot("2026-10-23", "09:15", ("600001", "600002"))
    )
    updated = replace(
        _snapshot("2026-10-23", "11:00", ("600002", "600003")),
        recommendation_gate=HomeSnapshotRecommendationGate(
            False, "blocked", ("freshness_not_ready",)
        ),
    )
    write_home_dashboard_snapshot(path, updated)
    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    assert loaded.recommendation_gate == updated.recommendation_gate
    assert loaded.candidates[0].research_status == "研究观察"
    assert loaded.candidates[0].coverage_note.startswith("本轮无新研究")
    assert [(item.symbol, item.status) for item in loaded.candidate_changes] == [
        ("600003", "added"),
    ]
    bridge = _parse_snapshot(json.loads(path.read_text(encoding="utf-8")))
    assert bridge.frozen_at == "2026-10-23T09:15:00+08:00"
    assert bridge.candidate_changes[0].status == "added"
    assert bridge.candidates[0].coverage_note == loaded.candidates[0].coverage_note
    assert [candidate.symbol for candidate in bridge.candidates] == ["600001", "600002"]


def test_home_freeze_restarts_when_next_trading_session_arrives(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    write_home_dashboard_snapshot(path, _snapshot("2026-10-23", "14:00", ("600001",)))
    write_home_dashboard_snapshot(path, _snapshot("2026-10-24", "11:00", ("600002",)))
    weekend = load_home_dashboard_snapshot(path)
    assert weekend is not None
    assert weekend.candidates[0].symbol == "600001"
    monday = _snapshot("2026-10-26", "09:30", ("600003",))
    write_home_dashboard_snapshot(path, monday)
    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    assert loaded.candidates[0].symbol == "600003"
    assert loaded.frozen_at == monday.generated_at
    assert loaded.candidate_changes == ()


def test_home_freeze_keeps_original_discussion_and_time_when_candidate_is_absent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    first = _snapshot("2026-10-23", "09:15", ("600001", "600002"))
    original = replace(
        first.candidates[0], research_status="优先复核", data_source="fixture",
        data_fetched_at=first.generated_at, data_timestamp_source="bar_time", freshness="fresh",
    )
    debate = HomeSnapshotDebate("600001", "样本", "原讨论", "原风险", "原条件", ("趋势", "风控"), round_count=2, neutral_count=2)
    first = replace(first, candidates=(original, first.candidates[1]), debates=(debate,))
    write_home_dashboard_snapshot(path, first)
    for time in ("11:00", "14:00"):
        write_home_dashboard_snapshot(path, _snapshot("2026-10-23", time, ("600002",)))
        loaded = load_home_dashboard_snapshot(path)
        assert loaded.debates == (debate,)
        assert loaded.candidates[0] == replace(original, coverage_note=loaded.candidates[0].coverage_note)
        assert loaded.candidates[0].coverage_note
        assert not is_home_recommendation(loaded.candidates[0])
        assert not loaded.candidate_changes
    returned = replace(
        original, data_fetched_at="2026-10-23T14:30:00+08:00", score=99,
    )
    write_home_dashboard_snapshot(path, replace(
        _snapshot("2026-10-23", "14:30", ("600001", "600002")),
        candidates=(returned, first.candidates[1]), debates=(debate,),
    ))
    loaded = load_home_dashboard_snapshot(path)
    assert loaded.candidates[0].score == original.score
    assert loaded.candidates[0].coverage_note == ""
    assert loaded.candidates[0].data_fetched_at == returned.data_fetched_at


def test_home_freeze_keeps_explicit_invalidation_when_latest_card_reports_it(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    first = _snapshot("2026-10-23", "09:15", ("600001",))
    write_home_dashboard_snapshot(path, first)
    write_home_dashboard_snapshot(path, replace(first, candidates=()))
    latest = replace(first.candidates[0], research_status="日内已失效", next_step="有源风险复核")
    write_home_dashboard_snapshot(path, replace(first, candidates=(latest,)))
    result = load_home_dashboard_snapshot(path)
    assert result.candidates[0].research_status == "日内已失效"
    assert [(change.symbol, change.status) for change in result.candidate_changes] == [("600001", "invalidated")]
    assert result.candidates[0].coverage_note == ""
    index_path = tmp_path / "index.json"
    write_home_snapshot_index(index_path, _index(first))
    write_home_snapshot_index(index_path, _index(result))
    assert load_home_snapshot_index(index_path).snapshot_for_date(first.selected_date) == result


def test_home_freeze_keeps_explicit_invalidation_when_candidate_is_later_absent(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    first = _snapshot("2026-10-23", "09:15", ("600001",))
    write_home_dashboard_snapshot(path, first)
    invalidated = replace(first.candidates[0], research_status="日内已失效", next_step="有源风险复核")
    write_home_dashboard_snapshot(path, replace(first, candidates=(invalidated,)))
    write_home_dashboard_snapshot(path, replace(first, candidates=()))
    result = load_home_dashboard_snapshot(path)
    assert result.candidates[0].research_status == "日内已失效"
    assert result.candidates[0].coverage_note == ""
    assert [(change.symbol, change.status) for change in result.candidate_changes] == [("600001", "invalidated")]
    index_path = tmp_path / "index.json"
    write_home_snapshot_index(index_path, _index(first))
    write_home_snapshot_index(index_path, _index(replace(first, candidates=(invalidated,))))
    write_home_snapshot_index(index_path, _index(replace(first, candidates=())))
    indexed = load_home_snapshot_index(index_path).snapshot_for_date(first.selected_date)
    assert indexed == result


def test_home_freeze_reports_parameter_change_when_candidate_is_not_in_new_batch(tmp_path: Path) -> None:
    from aqsp.portfolio.parameter_snapshot import ParameterSnapshot

    path = tmp_path / "home.json"
    first = replace(_snapshot("2026-10-23", "09:15", ("600001",)),
                    parameter_snapshot=ParameterSnapshot("v1", "2026-10-01", "a" * 64))
    write_home_dashboard_snapshot(path, first)
    changed = replace(first, candidates=(),
                      parameter_snapshot=ParameterSnapshot("v2", "2026-10-01", "b" * 64))
    write_home_dashboard_snapshot(path, changed)
    result = load_home_dashboard_snapshot(path)
    assert result.candidates[0].research_status == "参数身份变化 · 仅观察"
    assert result.candidates[0].coverage_note
    assert not result.candidate_changes


def test_home_freeze_does_not_lock_empty_startup_result(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    write_home_dashboard_snapshot(path, _snapshot("2026-10-23", "09:15", ()))
    first_valid = _snapshot("2026-10-23", "11:00", ("600001",))
    write_home_dashboard_snapshot(path, first_valid)
    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    assert loaded.candidates[0].symbol == "600001"
    assert loaded.frozen_at == first_valid.generated_at


def test_home_freeze_is_consumed_by_the_date_index_when_written_twice(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home-index.json"
    write_home_snapshot_index(
        path, _index(_snapshot("2026-10-23", "09:15", ("600001", "600002")))
    )
    write_home_snapshot_index(
        path, _index(_snapshot("2026-10-23", "14:00", ("600003", "600002")))
    )
    loaded = load_home_snapshot_index(path)
    assert loaded is not None
    current = loaded.snapshot_for_date("2026-10-23")
    assert current is not None
    assert [candidate.symbol for candidate in current.candidates] == [
        "600001",
        "600002",
    ]
    bridge = _parse_snapshot(json.loads(current.to_json()))
    assert bridge.candidate_changes[0].symbol == "600003"
    assert bridge.frozen_at == "2026-10-23T09:15:00+08:00"


@pytest.mark.parametrize("existing_index", [False, True])
def test_home_freeze_main_connects_identical_single_and_indexed_rosters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_index: bool
) -> None:
    single_path = tmp_path / "home.json"
    index_path = tmp_path / "home-index.json"
    first = _snapshot("2026-10-23", "09:15", ("600001", "600002"))
    write_home_dashboard_snapshot(single_path, first)
    if existing_index:
        write_home_snapshot_index(index_path, _index(first))
    changed = _snapshot("2026-10-23", "14:00", ("600003", "600002"))
    monkeypatch.setattr(write_home_snapshot, "DashboardDataProvider", lambda: object())
    monkeypatch.setattr(
        write_home_snapshot, "build_home_snapshot", lambda *args, **kwargs: changed
    )
    monkeypatch.setattr(
        write_home_snapshot,
        "build_home_snapshot_index",
        lambda *args, **kwargs: _index(changed),
    )
    assert (
        write_home_snapshot.main(
            ["--output", str(single_path), "--index-output", str(index_path)]
        )
        == 0
    )
    single = load_home_dashboard_snapshot(single_path)
    index = load_home_snapshot_index(index_path)
    assert single is not None and index is not None
    assert single == index.snapshot_for_date("2026-10-23")
    assert [candidate.symbol for candidate in single.candidates] == ["600001", "600002"]
    assert [(change.symbol, change.status) for change in single.candidate_changes] == [
        ("600003", "added"),
    ]


def test_pick_snapshot_freezes_first_nonempty_roster_and_resets_next_session(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "picks.jsonl")
    pick = SimpleNamespace(symbol="600001", name="样本", score=70)
    save_snapshot([pick], path, "2026-10-23")
    initial = Path(path).read_bytes()
    replacement = SimpleNamespace(symbol="600002", name="新样本", score=90)
    save_snapshot([replacement], path, "2026-10-24")
    assert Path(path).read_bytes() == initial
    save_snapshot([replacement], path, "2026-10-26")
    loaded = load_snapshot("2026-10-26", path)
    assert loaded is not None
    assert loaded[0].symbol == "600002"


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        ("2026-10-24", "2026-10-23"),
        ("2026-10-07", "2026-09-30"),
        ("2026-10-09", "2026-10-09"),
    ],
)
def test_snapshot_trading_date_observes_weekends_and_exchange_holidays(
    day: str, expected: str
) -> None:
    assert snapshot_trading_date(day) == expected


def test_snapshot_trading_date_rejects_invalid_date() -> None:
    with pytest.raises(ValueError):
        snapshot_trading_date("invalid")


@pytest.mark.parametrize("existing_index", [False, True])
def test_daily_pipeline_keeps_single_and_index_identical_when_roster_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_index: bool
) -> None:
    single_path = tmp_path / "home_dashboard_snapshot.json"
    index_path = tmp_path / "home_dashboard_snapshot_index.json"
    first = _snapshot("2026-10-23", "09:15", ("600001", "600002"))
    write_home_dashboard_snapshot(single_path, first)
    if existing_index:
        write_home_snapshot_index(index_path, _index(first))
    changed = _snapshot("2026-10-23", "14:00", ("600003", "600002"))
    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_PATH", str(single_path))
    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_INDEX_PATH", str(index_path))
    monkeypatch.syspath_prepend(str(tmp_path / "scripts"))
    monkeypatch.setitem(
        sys.modules,
        "write_home_snapshot",
        SimpleNamespace(
            build_home_snapshot=lambda *args, **kwargs: changed,
            build_home_snapshot_index=lambda *args, **kwargs: _index(changed),
        ),
    )
    monkeypatch.setattr(
        "aqsp.web.data_provider.DashboardDataProvider", lambda **kwargs: object()
    )
    config = SimpleNamespace(
        project_root=tmp_path, ledger_path="ledger.jsonl", paper_ledger="paper.jsonl"
    )
    result = daily_pipeline._refresh_home_snapshot(config, logging.getLogger(__name__))
    assert "error" not in result
    single = load_home_dashboard_snapshot(single_path)
    index = load_home_snapshot_index(index_path)
    assert single is not None and index is not None
    assert index.snapshot_for_date("2026-10-23") == single
    assert [candidate.symbol for candidate in single.candidates] == ["600001", "600002"]
    assert single.frozen_at == first.generated_at
    assert [(change.symbol, change.status) for change in single.candidate_changes] == [
        ("600003", "added"),
    ]
