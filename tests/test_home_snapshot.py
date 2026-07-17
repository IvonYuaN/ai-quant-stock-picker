from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.news.catalysts import CatalystReport, serialize_catalyst_report
from scripts import write_home_snapshot
from aqsp.web import home_snapshot
from aqsp.web.home_snapshot import (
    HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
    HOME_SNAPSHOT_SCHEMA_VERSION,
    MAX_HOME_SNAPSHOT_BYTES,
    MAX_HOME_SNAPSHOT_INDEX_DAYS,
    HomeDashboardSnapshot,
    HomeSnapshotDay,
    HomeSnapshotCandidate,
    HomeSnapshotColdstart,
    HomeSnapshotCrossMarket,
    HomeSnapshotDebate,
    HomeSnapshotIndex,
    HomeSnapshotMarketContext,
    HomeSnapshotMessage,
    HomeSnapshotSource,
    HOME_SNAPSHOT_CLOSE_TTL,
    HOME_SNAPSHOT_INTRADAY_TTL,
    load_home_dashboard_snapshot,
    load_home_snapshot_for_date,
    load_home_snapshot_index,
    stale_after_for,
    stale_after_for_task,
    write_home_dashboard_snapshot,
    write_home_snapshot_index,
)


def _snapshot(
    *,
    dates: tuple[str, ...] = ("2026-07-11",),
    selected_date: str = "2026-07-11",
    candidates: tuple[HomeSnapshotCandidate, ...] = (),
    debate: HomeSnapshotDebate | None = None,
    debates: tuple[HomeSnapshotDebate, ...] = (),
    summaries: tuple[str, ...] = (),
    stale_after: str = "",
    messages: tuple[HomeSnapshotMessage, ...] = (),
) -> HomeDashboardSnapshot:
    return HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        selected_date=selected_date,
        available_dates=dates,
        candidates=candidates,
        debate=debate,
        debates=debates,
        summaries=summaries,
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-11",
            lag_days=0,
            status="fresh",
        ),
        coldstart=HomeSnapshotColdstart(status="ready", detail="样本已就绪"),
        stale_after=stale_after,
        messages=messages,
    )


def _candidate(symbol: str) -> HomeSnapshotCandidate:
    return HomeSnapshotCandidate(
        symbol=symbol,
        display_name=f"{symbol} 示例",
        score=72.5,
        research_status="纸面复核",
        next_step="确认量能",
        context="海外产业映射",
        deterministic_reasons=("MA20 斜率向上",),
        strategies=("ma_pullback",),
        evidence_status="有独立规则证据",
    )


def test_home_snapshot_round_trips_bounded_home_payload(tmp_path) -> None:
    debate = HomeSnapshotDebate(
        symbol="603019",
        display_name="中科曙光",
        conclusion="维持纸面复核",
        primary_risk_gate="量能承接",
        next_trigger="放量确认",
        active_roles=("cross_market", "risk"),
    )
    source = tmp_path / "home.json"
    snapshot = _snapshot(
        dates=("2026-07-11", "2026-07-10"),
        candidates=(_candidate("603019"), _candidate("600879")),
        debate=debate,
        summaries=("实时源正常", "委员会仅作证据复核"),
    )

    write_home_dashboard_snapshot(source, snapshot)

    assert load_home_dashboard_snapshot(source) == snapshot
    assert json.loads(source.read_text(encoding="utf-8"))["schema_version"] == "v1"


def test_home_snapshot_round_trips_candidate_provenance(tmp_path) -> None:
    candidate = HomeSnapshotCandidate(
        symbol="603019",
        display_name="中科曙光",
        score=72.5,
        research_status="纸面复核",
        next_step="确认量能",
        context="",
        deterministic_reasons=("量价确认",),
        data_source="tencent",
        data_fetched_at="2026-07-11T10:05:00+08:00",
        data_timestamp_source="bar_time",
        freshness="fresh",
    )
    path = tmp_path / "home.json"

    write_home_dashboard_snapshot(path, _snapshot(candidates=(candidate,)))

    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    assert loaded.candidates[0] == candidate


def test_home_snapshot_legacy_candidate_provenance_stays_empty(tmp_path) -> None:
    path = tmp_path / "legacy.json"
    payload = _snapshot(candidates=(_candidate("603019"),)).to_dict()
    payload["candidates"][0].pop("data_source", None)
    payload["candidates"][0].pop("data_fetched_at", None)
    payload["candidates"][0].pop("data_timestamp_source", None)
    payload["candidates"][0].pop("freshness", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    legacy_candidate = loaded.candidates[0]
    assert legacy_candidate.data_source == ""
    assert legacy_candidate.data_fetched_at == ""
    assert legacy_candidate.data_timestamp_source == ""
    assert legacy_candidate.freshness == ""


def test_home_snapshot_writes_service_group_readable_mode(tmp_path) -> None:
    source = tmp_path / "home.json"

    write_home_dashboard_snapshot(source, _snapshot())

    assert source.stat().st_mode & 0o777 == 0o640


def test_home_snapshot_prefers_structured_news_artifact(monkeypatch, tmp_path) -> None:
    path = tmp_path / "news.json"
    report = CatalystReport(
        date=today_shanghai().isoformat(),
        generated_at=now_shanghai().isoformat(timespec="seconds"),
        events=(),
        source_status="partial",
        warnings=("国际源超时，已降级",),
        event_status="no_valid_news",
    )
    path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(path))

    status, messages, parsed = write_home_snapshot._parse_news_report_payload(
        report.date
    )

    assert status == "无可用消息"
    assert messages == ()
    assert parsed == report
    assert parsed.warnings == ("国际源超时，已降级",)


def test_home_snapshot_round_trips_messages_and_debate_process(tmp_path) -> None:
    snapshot = _snapshot(
        candidates=(_candidate("600001"), _candidate("600879")),
        debate=HomeSnapshotDebate(
            symbol="600001",
            display_name="示例",
            conclusion="维持观察",
            primary_risk_gate="量能",
            next_trigger="放量",
            active_roles=("技术多头", "风险控制"),
            round_count=3,
            bull_count=1,
            bear_count=1,
            neutral_count=7,
            process_summary="3轮；看多 1 / 看空 1 / 中性 7",
        ),
    )
    snapshot = replace(
        snapshot,
        message_status="部分可用",
        messages=(
            HomeSnapshotMessage(
                title="海外主线",
                summary="等待 A 股板块确认",
                impact="短线观察",
                category="跨市",
                source="RSS",
                published_at="2026-07-11T09:00:00+08:00",
                event_type="海外公司事件",
                affected_sectors=("商业航天",),
                affected_symbols=("600879",),
                transmission_hypothesis="海外事件 -> A股映射",
                supporting_evidence=("RSS: 海外主线",),
                source_url="https://example.test/news",
            ),
        ),
    )
    path = tmp_path / "home.json"

    write_home_dashboard_snapshot(path, snapshot)

    loaded = load_home_dashboard_snapshot(path)
    assert loaded == snapshot
    assert loaded is not None
    assert loaded.message_status == "部分可用"
    assert loaded.messages[0].source == "RSS"
    assert loaded.messages[0].event_type == "海外公司事件"
    assert loaded.messages[0].affected_sectors == ("商业航天",)
    assert loaded.messages[0].supporting_evidence == ("RSS: 海外主线",)
    assert loaded.debate is not None
    assert loaded.debate.round_count == 3


def test_home_snapshot_write_normalizes_legacy_timestamp_offsets(tmp_path) -> None:
    snapshot = replace(
        _snapshot(stale_after="2026-07-12T01:30:00+00:00"),
        generated_at="2026-07-11T01:30:00Z",
        messages=(
            HomeSnapshotMessage(
                title="消息",
                summary="仅作辅助参考",
                impact="中性",
                category="消息",
                source="RSS",
                published_at="2026-07-11T01:00:00",
            ),
        ),
        market_context=HomeSnapshotMarketContext(
            status="可用",
            overview="仅作辅助参考",
            summary_lines=("消息状态: 可用",),
            cross_market=(
                HomeSnapshotCrossMarket(
                    rule_id="commercial_space",
                    theme="海外商业航天催化",
                    strength="中",
                    action="观察为主",
                    source_title="海外消息",
                    source_region="international",
                    source_published_at="2026-07-11T01:00:00Z",
                    affected_sectors=("商业航天",),
                    transmission_path=("海外消息 -> A股主题",),
                    validation_signals=("价格与成交确认",),
                    invalidation_signals=("板块不扩散",),
                    summary="仅作辅助参考",
                ),
            ),
        ),
    )
    path = tmp_path / "home.json"

    write_home_dashboard_snapshot(path, snapshot)

    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    assert loaded.generated_at == "2026-07-11T09:30:00+08:00"
    assert loaded.stale_after == "2026-07-12T09:30:00+08:00"
    assert loaded.messages[0].published_at == "2026-07-11T01:00:00+08:00"
    assert (
        loaded.market_context is not None
        and loaded.market_context.cross_market[0].source_published_at
        == "2026-07-11T09:00:00+08:00"
    )


def test_home_snapshot_round_trips_multiple_debate_summaries(tmp_path) -> None:
    debates = tuple(
        HomeSnapshotDebate(
            symbol=symbol,
            display_name=f"{symbol} 示例",
            conclusion="保留纸面复核",
            primary_risk_gate="承接强度",
            next_trigger="放量确认",
            active_roles=("技术多头", "风控"),
            round_count=3,
            process_summary="3轮；看多 1 / 看空 1 / 中性 7",
        )
        for symbol in ("600001", "600002", "600003")
    )
    path = tmp_path / "home.json"
    candidates = tuple(_candidate(symbol) for symbol in ("600001", "600002", "600003"))

    write_home_dashboard_snapshot(
        path, _snapshot(candidates=candidates, debates=debates)
    )

    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    assert tuple(item.symbol for item in loaded.debates) == (
        "600001",
        "600002",
        "600003",
    )
    assert loaded.debate is loaded.debates[0]


def test_home_snapshot_rejects_duplicate_debate_symbols() -> None:
    debate = HomeSnapshotDebate(
        symbol="600001",
        display_name="示例",
        conclusion="观察",
        primary_risk_gate="量能",
        next_trigger="放量",
        active_roles=("风控",),
    )

    with pytest.raises(ValueError, match="duplicate symbols"):
        _snapshot(
            candidates=(_candidate("600001"),),
            debates=(debate, debate),
        )


def test_home_snapshot_rejects_debate_symbol_outside_candidates() -> None:
    debate = HomeSnapshotDebate(
        symbol="600002",
        display_name="示例",
        conclusion="观察",
        primary_risk_gate="量能",
        next_trigger="放量",
        active_roles=("风控",),
    )

    with pytest.raises(ValueError, match="debates symbols.*candidates"):
        _snapshot(candidates=(_candidate("600001"),), debates=(debate,))


def test_home_snapshot_allows_message_symbol_outside_candidates() -> None:
    message = HomeSnapshotMessage(
        title="消息",
        summary="仅作辅助参考",
        impact="中性",
        category="消息",
        source="RSS",
        published_at="2026-07-11T09:00:00+08:00",
        affected_symbols=("600002",),
    )

    snapshot = _snapshot(candidates=(_candidate("600001"),), messages=(message,))

    assert snapshot.messages == (message,)


def test_home_snapshot_allows_market_message_without_affected_symbols() -> None:
    message = HomeSnapshotMessage(
        title="市场消息",
        summary="仅作市场级参考",
        impact="中性",
        category="市场",
        source="RSS",
        published_at="2026-07-11T09:00:00+08:00",
    )

    snapshot = _snapshot(messages=(message,))

    assert snapshot.messages == (message,)


def test_home_snapshot_reads_legacy_single_debate_payload(tmp_path) -> None:
    path = tmp_path / "legacy-home.json"
    debate = HomeSnapshotDebate(
        symbol="600001",
        display_name="示例",
        conclusion="观察",
        primary_risk_gate="量能",
        next_trigger="放量",
        active_roles=("风控",),
    )
    payload = _snapshot(candidates=(_candidate("600001"),), debate=debate).to_dict()
    payload.pop("debates")
    payload["debate"] = debate.__dict__
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = load_home_dashboard_snapshot(path)
    assert loaded is not None
    assert loaded.debates == (debate,)


def test_home_snapshot_freshness_is_timezone_aware_and_legacy_is_stale() -> None:
    snapshot = _snapshot(stale_after="2026-07-11T10:30:00+08:00")
    as_of = datetime(2026, 7, 11, 10, 29, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert snapshot.is_stale(as_of) is False
    assert snapshot.is_stale(as_of.replace(minute=30)) is True
    assert _snapshot().is_stale(as_of) is True
    assert stale_after_for("2026-07-11T09:30:00+08:00") == ("2026-07-12T09:30:00+08:00")


def test_home_snapshot_task_freshness_distinguishes_intraday_and_close() -> None:
    generated_at = "2026-07-11T15:00:00+08:00"

    assert HOME_SNAPSHOT_INTRADAY_TTL.total_seconds() == 30 * 60
    assert HOME_SNAPSHOT_CLOSE_TTL.total_seconds() == 18 * 60 * 60
    assert stale_after_for_task(generated_at, "intraday") == (
        "2026-07-11T15:30:00+08:00"
    )
    assert stale_after_for_task(generated_at, "midday") == ("2026-07-11T15:30:00+08:00")
    assert stale_after_for_task(generated_at, "daily") == ("2026-07-12T09:00:00+08:00")


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "generated_at": "2026-07-11T09:30:00",
            "stale_after": "2026-07-12T09:30:00+08:00",
        },
        {
            "generated_at": "2026-07-11T09:30:00+08:00",
            "stale_after": "2026-07-11T09:00:00+08:00",
        },
    ],
)
def test_home_snapshot_rejects_unverifiable_freshness(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="generated_at|stale_after"):
        HomeDashboardSnapshot(**{**_snapshot().to_dict(), **kwargs})


def test_home_snapshot_rejects_selected_date_outside_available_dates() -> None:
    with pytest.raises(ValueError, match="selected_date must exist in available_dates"):
        _snapshot(dates=("2026-07-10",), selected_date="2026-07-11")


def test_home_snapshot_index_round_trips_and_never_substitutes_date(tmp_path) -> None:
    first = _snapshot(dates=("2026-07-11",), stale_after="2026-07-12T09:30:00+08:00")
    second = _snapshot(
        selected_date="2026-07-10",
        dates=("2026-07-10",),
        stale_after="2026-07-12T09:30:00+08:00",
    )
    index = HomeSnapshotIndex(
        schema_version=HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        stale_after="2026-07-12T09:30:00+08:00",
        selected_date="2026-07-11",
        days=(
            HomeSnapshotDay(date="2026-07-11", snapshot=first),
            HomeSnapshotDay(date="2026-07-10", snapshot=second),
        ),
    )
    source = tmp_path / "home-index.json"

    write_home_snapshot_index(source, index)

    assert load_home_snapshot_index(source) == index
    assert load_home_snapshot_for_date(source, "2026-07-10") == second
    assert load_home_snapshot_for_date(source, "2026-07-09") is None
    assert load_home_dashboard_snapshot(source) is None


def test_home_snapshot_index_rejects_more_than_four_days() -> None:
    snapshots = tuple(
        HomeSnapshotDay(
            date=f"2026-07-{11 - index:02d}",
            snapshot=_snapshot(
                selected_date=f"2026-07-{11 - index:02d}",
                dates=(f"2026-07-{11 - index:02d}",),
                stale_after="2026-07-12T09:30:00+08:00",
            ),
        )
        for index in range(MAX_HOME_SNAPSHOT_INDEX_DAYS + 1)
    )

    with pytest.raises(ValueError, match="days"):
        HomeSnapshotIndex(
            schema_version=HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
            generated_at="2026-07-11T09:30:00+08:00",
            stale_after="2026-07-12T09:30:00+08:00",
            days=snapshots,
        )


def test_home_snapshot_write_uses_shared_atomic_writer(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def _atomic_write(path: str, payload: str) -> None:
        captured["path"] = path
        captured["payload"] = payload

    monkeypatch.setattr(home_snapshot, "atomic_write_text", _atomic_write)
    target = tmp_path / "home.json"
    target.touch()

    write_home_dashboard_snapshot(target, _snapshot())

    assert captured["path"] == target
    assert str(captured["payload"]).endswith("\n")


def test_home_snapshot_write_does_not_replace_newer_date_with_history(tmp_path) -> None:
    current = _snapshot(selected_date="2026-07-11")
    historical = _snapshot(
        dates=("2026-07-10",),
        selected_date="2026-07-10",
    )
    source = tmp_path / "home.json"

    write_home_dashboard_snapshot(source, current)

    with pytest.raises(ValueError, match="newer home snapshot"):
        write_home_dashboard_snapshot(source, historical)

    assert load_home_dashboard_snapshot(source) == current


def test_home_snapshot_index_write_does_not_replace_newer_date_with_history(
    tmp_path,
) -> None:
    current = _snapshot(selected_date="2026-07-11")
    historical = _snapshot(
        dates=("2026-07-10",),
        selected_date="2026-07-10",
    )
    current_index = HomeSnapshotIndex(
        schema_version=HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        stale_after="2026-07-12T09:30:00+08:00",
        selected_date="2026-07-11",
        days=(HomeSnapshotDay(date="2026-07-11", snapshot=current),),
    )
    historical_index = HomeSnapshotIndex(
        schema_version=HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
        generated_at="2026-07-10T09:30:00+08:00",
        stale_after="2026-07-11T09:30:00+08:00",
        selected_date="2026-07-10",
        days=(HomeSnapshotDay(date="2026-07-10", snapshot=historical),),
    )
    source = tmp_path / "home-index.json"

    write_home_snapshot_index(source, current_index)

    with pytest.raises(ValueError, match="newer home snapshot index"):
        write_home_snapshot_index(source, historical_index)

    assert load_home_snapshot_index(source) == current_index


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "dates",
            tuple(f"2026-07-{day:02d}" for day in range(1, 6)),
            "available_dates",
        ),
        (
            "candidates",
            tuple(_candidate(str(index)) for index in range(6)),
            "candidates",
        ),
        ("summaries", ("a", "b", "c", "d"), "summaries"),
    ],
)
def test_home_snapshot_rejects_values_beyond_home_page_limits(
    field: str,
    value: tuple[object, ...],
    message: str,
) -> None:
    kwargs: dict[str, object] = {field: value}

    with pytest.raises(ValueError, match=message):
        _snapshot(**kwargs)


def test_home_snapshot_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValueError, match="schema"):
        HomeDashboardSnapshot(
            **{
                **_snapshot().to_dict(),
                "schema_version": "v2",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b'{"schema_version":"v0"}',
        b'{"schema_version":"v1","extra":true}',
    ],
)
def test_home_snapshot_load_fails_safely_for_invalid_schema(
    tmp_path, payload: bytes
) -> None:
    source = tmp_path / "home.json"
    source.write_bytes(payload)

    assert load_home_dashboard_snapshot(source) is None


def test_home_snapshot_load_fails_safely_when_file_exceeds_budget(tmp_path) -> None:
    source = tmp_path / "home.json"
    source.write_bytes(b"x" * (MAX_HOME_SNAPSHOT_BYTES + 1))

    assert load_home_dashboard_snapshot(source) is None


def test_home_snapshot_load_fails_safely_for_unsupported_version(tmp_path) -> None:
    source = tmp_path / "home.json"
    payload = _snapshot().to_dict()
    payload["schema_version"] = "v2"
    source.write_text(json.dumps(payload), encoding="utf-8")

    assert load_home_dashboard_snapshot(source) is None


def test_home_snapshot_write_rejects_payload_that_exceeds_byte_budget(tmp_path) -> None:
    source = tmp_path / "home.json"
    snapshot = _snapshot(summaries=("x" * MAX_HOME_SNAPSHOT_BYTES,))

    with pytest.raises(ValueError, match="64 KiB"):
        write_home_dashboard_snapshot(source, snapshot)

    assert not source.exists()


def test_home_snapshot_module_has_no_ledger_or_network_dependencies() -> None:
    source = __import__("aqsp.web.home_snapshot", fromlist=["__file__"]).__file__
    assert source is not None
    module = ast.parse(Path(source).read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(module)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
    )

    assert not any("ledger" in name for name in imports)
    assert not any(name.startswith(("requests", "urllib")) for name in imports)
