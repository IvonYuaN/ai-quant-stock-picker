from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from aqsp.news.catalysts import CatalystEvent, CatalystReport, serialize_catalyst_report
from aqsp.web.home_snapshot import (
    load_home_dashboard_snapshot,
    load_home_snapshot_index,
)
from scripts import write_home_snapshot


def _candidate(symbol: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        display_name=f"{symbol} 示例",
        score=score,
        action_label="纸面复核",
        status_label="观察中",
        next_step=f"核对 {symbol} 量能",
        reasons=("MA20 斜率向上",),
        strategies=("ma_pullback",),
        news_catalyst_summary=f"{symbol} 消息催化",
        cross_market_summary="不应取用",
        adjusted_score=99.0,
        close=12.34,
        ret5_pct=4.5,
        ret20_pct=12.75,
        volume_ratio=1.6,
        rsi12=64.2,
        bias20_pct=2.1,
        stop_loss=11.1,
        take_profit=14.8,
        data_source="eastmoney",
        data_fetched_at="2026-07-10T14:59:00+08:00",
        data_timestamp_source="bar_time",
        freshness="fresh",
    )


def _write_walkforward_artifacts(
    tmp_path: Path,
    *,
    status: str = "completed",
    run_date: object = "2026-07-18",
    both_pass: object = True,
) -> None:
    status_path = tmp_path / "walkforward_production_status.json"
    gate_path = tmp_path / "walkforward_gate.json"
    status_path.write_text(
        json.dumps({"status": status}, ensure_ascii=False), encoding="utf-8"
    )
    gate_path.write_text(
        json.dumps({"run_date": run_date, "both_pass": both_pass}),
        encoding="utf-8",
    )


def test_walkforward_evidence_reads_completed_status_and_sidecar_in_shanghai(
    monkeypatch, tmp_path
) -> None:
    _write_walkforward_artifacts(tmp_path)
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS",
        str(tmp_path / "walkforward_production_status.json"),
    )
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_GATE_PATH", str(tmp_path / "walkforward_gate.json")
    )

    ok, updated_at = write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert ok is True
    assert updated_at is not None
    assert updated_at.tzinfo == ZoneInfo("Asia/Shanghai")
    assert updated_at.isoformat() == "2026-07-18T00:00:00+08:00"


@pytest.mark.parametrize("status", ["blocked_resources", "timeout", "failed"])
def test_walkforward_evidence_rejects_non_completed_production_status(
    monkeypatch, tmp_path, status: str
) -> None:
    _write_walkforward_artifacts(tmp_path, status=status)
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS",
        str(tmp_path / "walkforward_production_status.json"),
    )
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_GATE_PATH", str(tmp_path / "walkforward_gate.json")
    )

    assert write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    ) == (False, None)


def test_walkforward_evidence_rejects_old_or_invalid_sidecar(
    monkeypatch, tmp_path
) -> None:
    _write_walkforward_artifacts(tmp_path, run_date="2026-05-01")
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS",
        str(tmp_path / "walkforward_production_status.json"),
    )
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_GATE_PATH", str(tmp_path / "walkforward_gate.json")
    )

    ok, updated_at = write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert ok is False
    assert updated_at is not None
    assert updated_at.isoformat() == "2026-05-01T00:00:00+08:00"

    _write_walkforward_artifacts(tmp_path, both_pass="true")
    assert write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    ) == (False, None)


class _Provider:
    def __init__(self, debate_symbol: str = "600003") -> None:
        self.digest_calls: list[tuple[str, str]] = []
        self.runtime_dates: list[str] = []
        self.debate_symbol = debate_symbol

    def default_task_id(self) -> str:
        return "main_chain"

    def home_digest_payload(
        self,
        task_id: str,
        signal_date: str = "",
    ) -> SimpleNamespace:
        self.digest_calls.append((task_id, signal_date))
        return SimpleNamespace(
            task_view=SimpleNamespace(
                selected_date="2026-07-10",
                latest_date="2026-07-10",
                available_dates=(
                    "2026-07-10",
                    "2026-07-09",
                    "2026-07-08",
                    "2026-07-07",
                    "2026-07-04",
                ),
                detail_cards=(
                    _candidate("600001", 88.0),
                    _candidate("600002", 80.0),
                    _candidate("600003", 72.0),
                    _candidate("600004", 66.0),
                ),
                source_status={"actual_source": "sina", "lag_days": "0"},
                headline="主链已落盘",
            ),
            spotlights=(
                _candidate("600002", 5.0),
                _candidate("600005", 99.0),
            ),
            debates=(
                SimpleNamespace(
                    symbol=self.debate_symbol,
                    display_name=f"{self.debate_symbol} 示例",
                    research_verdict="委员会建议复核",
                    consensus="不应取用",
                    primary_risk_gate="量能未确认",
                    next_trigger="放量站稳",
                    adjusted_score=999.0,
                    recommended_adjustment="raise",
                    agent_views=(
                        SimpleNamespace(role_id="bull"),
                        SimpleNamespace(role_id="risk_control"),
                    ),
                ),
            ),
            overview=SimpleNamespace(
                focus_headline="重点看首个确定性候选",
                blocker_headline="量能阻塞待解除",
                top_headline="主链候选已生成",
            ),
        )

    def runtime_overview(self, signal_date: str = "") -> SimpleNamespace:
        self.runtime_dates.append(signal_date)
        return SimpleNamespace(
            conclusion="当前运行已落盘",
            effective_source="sina",
            requested_source="akshare",
            data_latest_trade_date="2026-07-10",
            lag_days="0",
            run_status="fresh",
            source_reason="实时源正常",
            coldstart_progress="样本累积中",
            coldstart_handoff_line="等待最小样本量",
            gate_blocker_line="",
        )


def test_write_home_snapshot_builds_bounded_advisory_only_payload(monkeypatch) -> None:
    provider = _Provider()
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(
            2026,
            7,
            10,
            15,
            1,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert provider.digest_calls == [("intraday", "2026-07-10")]
    assert provider.runtime_dates == ["2026-07-10"]
    assert snapshot.available_dates == (
        "2026-07-10",
        "2026-07-09",
        "2026-07-08",
        "2026-07-07",
    )
    assert [item.symbol for item in snapshot.candidates] == [
        "600001",
        "600002",
        "600003",
        "600004",
        "600005",
    ]

    assert [item.score for item in snapshot.candidates] == [
        88.0,
        80.0,
        72.0,
        66.0,
        99.0,
    ]
    assert snapshot.candidates[0].deterministic_reasons == ("MA20 斜率向上",)
    assert snapshot.candidates[0].strategies == ("ma_pullback",)
    assert snapshot.candidates[0].evidence_status == "有独立规则证据"
    assert snapshot.candidates[0].context.endswith("数据源: eastmoney")
    assert snapshot.candidates[0].data_source == "eastmoney"
    assert snapshot.candidates[0].data_fetched_at == "2026-07-10T14:59:00+08:00"
    assert snapshot.candidates[0].data_timestamp_source == "bar_time"
    assert snapshot.candidates[0].freshness == "fresh"
    assert [
        (item.label, item.value) for item in snapshot.candidates[0].technical_metrics
    ] == [
        ("现价", "12.34"),
        ("5日动能", "+4.50%"),
        ("20日动能", "+12.75%"),
        ("量比", "1.60x"),
        ("RSI12", "64.2"),
        ("MA20偏离", "+2.10%"),
        ("纸面止损", "11.10"),
        ("纸面止盈", "14.80"),
    ]
    assert snapshot.debate is not None
    assert snapshot.debate.symbol == "600003"
    assert snapshot.debate.conclusion == "委员会建议复核"
    assert "999" not in snapshot.to_json()
    assert "raise" not in snapshot.to_json()
    assert snapshot.summaries == (
        "讨论复核 1/5 只；4 只未通过质量门，已隐藏",
        "当前运行已落盘",
        "重点看首个确定性候选",
    )
    assert snapshot.stale_after == "2026-07-10T15:31:00+08:00"


def test_snapshot_candidate_maps_freshness_label_when_status_is_missing() -> None:
    candidate = _candidate("600006", 70.0)
    candidate.freshness = ""
    candidate.freshness_label = "新鲜"

    snapshot_candidate = write_home_snapshot._snapshot_candidate(candidate)

    assert snapshot_candidate is not None
    assert snapshot_candidate.freshness == "fresh"


def test_write_home_snapshot_hides_quality_failed_debate() -> None:
    provider = _Provider()
    original = provider.home_digest_payload

    def payload_with_failed_debate(
        task_id: str, signal_date: str = ""
    ) -> SimpleNamespace:
        payload = original(task_id, signal_date)
        payload.debates = (
            SimpleNamespace(
                **{
                    **vars(payload.debates[0]),
                    "debate_quality_issues": ("missing_support_viewpoint",),
                }
            ),
        )
        return payload

    provider.home_digest_payload = payload_with_failed_debate
    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.debates == ()


def test_write_home_snapshot_makes_hidden_candidate_count_explicit(monkeypatch) -> None:
    provider = _Provider()
    original_payload = provider.home_digest_payload
    original_runtime = provider.runtime_overview

    def payload_with_six_candidates(task_id: str, signal_date: str = ""):
        payload = original_payload(task_id, signal_date)
        payload.task_view.detail_cards = (
            *payload.task_view.detail_cards,
            _candidate("600006", 60.0),
            _candidate("600007", 59.0),
        )
        payload.spotlights = ()
        return payload

    def runtime_with_count(signal_date: str = ""):
        runtime = original_runtime(signal_date)
        runtime.conclusion = "待复核 6 只，先看 600001、600002、600003"
        return runtime

    monkeypatch.setattr(provider, "home_digest_payload", payload_with_six_candidates)
    monkeypatch.setattr(provider, "runtime_overview", runtime_with_count)
    monkeypatch.setattr(
        write_home_snapshot,
        "_recommendation_gate",
        lambda *args, **kwargs: write_home_snapshot.HomeSnapshotRecommendationGate(
            recommendation_allowed=True,
            status="open",
            reasons=(),
        ),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert len(snapshot.candidates) == 5
    assert "待复核 6 只，首页展示 5 只" in snapshot.summaries[0]


def test_write_home_snapshot_downgrades_recommendations_when_gate_is_blocked(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        write_home_snapshot,
        "_recommendation_gate",
        lambda *args, **kwargs: write_home_snapshot.HomeSnapshotRecommendationGate(
            recommendation_allowed=False,
            status="blocked",
            reasons=("walkforward_failed",),
        ),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.recommendation_gate.recommendation_allowed is False
    assert snapshot.candidates
    assert all(
        not write_home_snapshot.is_home_recommendation(candidate)
        for candidate in snapshot.candidates
    )
    assert all(
        "仅观察（推荐 gate 阻塞）" in candidate.research_status
        for candidate in snapshot.candidates
    )


def test_snapshot_realtime_cross_market_reads_sidecar_without_network(
    monkeypatch, tmp_path: Path
) -> None:
    sidecar = tmp_path / "realtime_cross_market_context.json"
    payload = {
        "SPX": {
            "value": 5500.0,
            "change_pct": 0.8,
            "observed_at": "2026-07-10T14:59:00+08:00",
            "source": "test",
        }
    }
    sidecar.write_text(
        json.dumps({"schema_version": "v1", "status": "fresh", "payload": payload}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_REALTIME_CROSS_MARKET_PATH", str(sidecar))

    assert write_home_snapshot._snapshot_realtime_cross_market("intraday") == payload
    assert write_home_snapshot._snapshot_realtime_cross_market("daily") is None

    sidecar.write_text("not-json", encoding="utf-8")
    assert write_home_snapshot._snapshot_realtime_cross_market("intraday") is None


def test_write_home_snapshot_keeps_realtime_context_when_news_report_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_MARKET_CONTEXT_LIVE_SOURCE", "true")
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(
            2026,
            7,
            10,
            15,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
    )
    monkeypatch.setattr(
        write_home_snapshot,
        "_snapshot_realtime_cross_market",
        lambda _task_id: {
            "SPX": {
                "value": 5500.0,
                "change_pct": 0.8,
                "observed_at": "2026-07-10T14:59:00+08:00",
                "fetched_at": "2026-07-10T15:00:00+08:00",
                "source": "test-feed",
                "source_url": "https://example.test/spx",
                "timestamp_source": "vendor",
            }
        },
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.market_context is not None
    assert snapshot.market_context.cross_market == ()
    assert any(
        line.startswith("实时跨市:") for line in snapshot.market_context.summary_lines
    )


def test_write_home_snapshot_normalizes_legacy_news_and_cross_market_timestamps(
    monkeypatch, tmp_path
) -> None:
    report_path = tmp_path / "news.json"
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report = CatalystReport(
        date="2026-07-10",
        generated_at=current_time.replace(tzinfo=None).isoformat(timespec="seconds"),
        source_status="ok",
        events=(
            CatalystEvent(
                title="SpaceX 评估 IPO 上市窗口",
                    source="Reuters",
                published_at="2026-07-10T01:00:00Z",
                impact="positive",
                category="资本运作",
                inference="海外商业航天风险偏好升温",
                source_region="international",
            ),
            CatalystEvent(
                title="历史事件",
                source="旧缓存",
                published_at="2026-07-09T09:00:00+08:00",
                impact="positive",
            ),
            CatalystEvent(
                title="无时间事件",
                source="未知",
                published_at="",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.messages[0].published_at == "2026-07-10T09:00:00+08:00"
    assert snapshot.market_context is not None
    assert (
        snapshot.market_context.cross_market[0].source_published_at
        == "2026-07-10T09:00:00+08:00"
    )
    assert all("2026-07-09" not in item.title for item in snapshot.messages)


def test_messages_prioritize_distinct_topics_before_repeating_one_topic() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=tuple(
            CatalystEvent(
                title=title,
                source="feed",
                published_at=f"2026-07-10T09:{index:02d}:00+08:00",
                impact="positive",
                category=category,
                inference=title,
                source_region=region,
            )
            for index, (title, category, region) in enumerate(
                (
                    ("英伟达新品 1", "海外公司事件", "international"),
                    ("英伟达新品 2", "海外公司事件", "international"),
                    ("PCB 涨价", "供应链/价格变化", "domestic"),
                    ("商业航天 IPO", "海外公司事件", "international"),
                    ("军工订单", "地缘事件", "mixed"),
                    ("政策支持", "产业政策", "domestic"),
                )
            )
        ),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert [message.title for message in messages] == [
        "英伟达新品 1",
        "PCB 涨价",
        "商业航天 IPO",
        "军工订单",
        "政策支持",
    ]


def test_messages_bound_one_source_when_multiple_sources_are_available() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=tuple(
            CatalystEvent(
                title=f"主源事件 {index}",
                source="主源",
                published_at=f"2026-07-10T09:{index:02d}:00+08:00",
                impact="positive",
                category=f"主源类别 {index}",
                inference=f"主源摘要 {index}",
            )
            for index in range(5)
        )
        + tuple(
            CatalystEvent(
                title=f"备用源事件 {index}",
                source="备用源",
                published_at=f"2026-07-10T10:{index:02d}:00+08:00",
                impact="neutral",
                category=f"备用类别 {index}",
                inference=f"备用源摘要 {index}",
            )
            for index in range(2)
        ),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert len(messages) == 4
    assert {message.source for message in messages} == {"主源", "备用源"}
    assert sum(message.source == "主源" for message in messages) == 2


def test_messages_bound_sources_even_when_digest_has_fewer_than_five_items() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=tuple(
            CatalystEvent(
                title=f"主源事件 {index}",
                source="主源",
                published_at=f"2026-07-10T09:{index:02d}:00+08:00",
                category=f"主源类别 {index}",
            )
            for index in range(3)
        )
        + (
            CatalystEvent(
                title="备用源事件",
                source="备用源",
                published_at="2026-07-10T10:00:00+08:00",
                category="备用类别",
            ),
        ),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert [message.source for message in messages] == ["主源", "主源", "备用源"]


def test_messages_exclude_events_without_traceable_source() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="无来源事件",
                source="",
                published_at="2026-07-10T09:00:00+08:00",
                impact="positive",
                inference="不应进入首页摘要",
            ),
        ),
    )

    assert write_home_snapshot._messages_from_catalyst_report(report) == ()


def test_write_home_snapshot_excludes_future_dated_news(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "news.json"
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report = CatalystReport(
        date="2026-07-10",
        generated_at=current_time.isoformat(),
        source_status="ok",
        events=(
            CatalystEvent(
                title="未来事件",
                source="feed",
                published_at="2026-07-10T15:02:00+08:00",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.messages == ()


def test_write_home_snapshot_rejects_stale_current_news_without_markdown_fallback(
    monkeypatch, tmp_path
) -> None:
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report_path = tmp_path / "news.json"
    markdown_path = tmp_path / "news.md"
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T08:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="旧消息",
                source="旧缓存",
                published_at="2026-07-10T08:00:00+08:00",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        "# 消息面雷达-2026-07-10|可用\n\n"
        "## 事件\n\n"
        "- 1. 利好 | 全市场 | 消息\n"
        "- 结果: 不应回退的旧 Markdown\n"
        "- 时间: 2026-07-10T08:00:00+08:00\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(markdown_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "超时"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert any(
        "超过 6 小时有效窗口" in item for item in snapshot.market_context.warnings
    )
    assert "不应回退的旧 Markdown" not in snapshot.to_json()


def test_write_home_snapshot_preserves_catalyst_chain_evidence(
    monkeypatch, tmp_path
) -> None:
    current_time = datetime(2026, 7, 10, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report_path = tmp_path / "news.json"
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T10:00:00+08:00",
        source_status="ok",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="NVIDIA 发布 Physical AI 新平台",
                source="NVIDIA",
                published_at="2026-07-10T09:30:00+08:00",
                impact="positive",
                category="科技催化",
                inference="映射机器人和边缘算力链",
                url="https://nvidia.example/news",
                affected_sectors=("机器人", "AI算力"),
                affected_symbols=("000977",),
                transmission_hypothesis="海外大厂发布 -> A股机器人映射",
                supporting_evidence=("NVIDIA: Physical AI 新平台",),
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "可用"
    message = snapshot.messages[0]
    assert message.event_type == "新品发布"
    assert message.affected_sectors == ("机器人", "AI算力")
    assert message.affected_symbols == ("000977",)
    assert message.transmission_hypothesis == "海外大厂发布 -> A股机器人映射"
    assert message.supporting_evidence == ("NVIDIA: Physical AI 新平台",)
    assert message.source_url == "https://nvidia.example/news"


def test_write_home_snapshot_clears_messages_when_current_source_failed(
    monkeypatch, tmp_path
) -> None:
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report_path = tmp_path / "news.json"
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="failed",
        warnings=("国际源超时",),
        event_status="source_failed",
        events=(
            CatalystEvent(
                title="失败源残留消息",
                source="旧缓存",
                published_at="2026-07-10T15:00:00+08:00",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "来源失败"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert "国际源超时" in snapshot.market_context.warnings
    assert snapshot.market_context.cross_market == ()


def test_write_home_snapshot_rejects_provider_historical_date_fallback() -> None:
    with pytest.raises(ValueError, match="historical date"):
        write_home_snapshot.build_home_snapshot(
            _Provider(), signal_date="2026-07-11", task_id="intraday"
        )


def test_write_home_snapshot_keeps_observation_and_blocked_cards_after_recommendations() -> (
    None
):
    provider = _Provider()
    original = provider.home_digest_payload

    def _mixed_payload(task_id: str, signal_date: str = "") -> SimpleNamespace:
        payload = original(task_id, signal_date)
        payload.task_view.detail_cards = (
            SimpleNamespace(
                symbol="699999",
                display_name="699999 阻塞项",
                score=100.0,
                action_label="阻塞观察",
                status_label="阻塞观察",
                rank_label="阻塞观察",
                blocker="流动性不足",
            ),
            *payload.task_view.detail_cards,
        )
        payload.spotlights = (
            SimpleNamespace(
                symbol="688888",
                display_name="688888 观察项",
                score=101.0,
                action_label="继续观察",
                status_label="观察",
                rank_label="观察",
                blocker="",
            ),
            _candidate("600005", 99.0),
        )
        return payload

    provider.home_digest_payload = _mixed_payload

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert [item.symbol for item in snapshot.candidates] == [
        "600001",
        "600002",
        "600003",
        "600004",
        "600005",
    ]


def test_write_home_snapshot_keeps_only_observation_cards_when_no_recommendation_exists() -> (
    None
):
    provider = _Provider()
    original = provider.home_digest_payload

    def _observation_payload(task_id: str, signal_date: str = "") -> SimpleNamespace:
        payload = original(task_id, signal_date)
        payload.task_view.detail_cards = (
            SimpleNamespace(
                symbol="699999",
                display_name="699999 阻塞项",
                score=100.0,
                action_label="阻塞观察",
                status_label="阻塞观察",
                rank_label="阻塞观察",
                blocker="流动性不足",
            ),
            SimpleNamespace(
                symbol="688888",
                display_name="688888 观察项",
                score=90.0,
                action_label="继续观察",
                status_label="观察",
                rank_label="观察",
                blocker="",
            ),
        )
        payload.spotlights = ()
        payload.debates = ()
        return payload

    provider.home_digest_payload = _observation_payload
    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert [item.symbol for item in snapshot.candidates] == ["699999", "688888"]
    assert all(
        not write_home_snapshot.is_home_recommendation(item)
        for item in snapshot.candidates
    )


def test_write_home_snapshot_maps_midday_to_latest_intraday_artifact() -> None:
    provider = _DateAwareProvider()

    write_home_snapshot.build_home_snapshot(provider, task_id="midday")

    assert provider.digest_calls == [
        ("intraday", write_home_snapshot.today_shanghai().isoformat())
    ]


def test_write_home_snapshot_hides_debate_for_non_current_candidate(
    monkeypatch,
) -> None:
    provider = _Provider(debate_symbol="600999")
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.debate is None
    assert snapshot.summaries[0] == "委员会结论缺少当前候选映射，已隐藏"
    assert "600999" not in snapshot.to_json()


def test_write_home_snapshot_reads_only_current_day_news_report(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "\n".join(
            (
                "# 消息面雷达-2026-07-10|部分可用",
                "",
                "## 事件",
                "",
                "- 1. 利好 | 市场/行业 | 跨市",
                "- 结果: 海外主线",
                "- 结论: 等待 A 股板块确认",
                "- 影响: 短线观察",
                "- 来源: RSS",
                "- 时间: 2026-07-10T09:00:00+08:00",
                "",
                "## 状态",
                "",
                "- 状态: partial",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "部分可用"
    assert len(snapshot.messages) == 1
    assert snapshot.messages[0].title == "海外主线"
    assert snapshot.messages[0].source == "RSS"

    assert (
        write_home_snapshot.build_home_snapshot(
            _DateAwareProvider(), signal_date="2026-07-09", task_id="intraday"
        ).messages
        == ()
    )


def test_write_home_snapshot_reads_dated_news_archive_when_latest_is_missing(
    monkeypatch, tmp_path
) -> None:
    archive_dir = tmp_path / "news_archive"
    archive_dir.mkdir()
    report = CatalystReport(
        date="2026-07-09",
        generated_at="2026-07-09T10:00:00+08:00",
        events=(
            CatalystEvent(
                title="PCB 供需变化",
                source="eastmoney_domestic",
                published_at="2026-07-09T09:30:00+08:00",
                impact="positive",
                category="涨价/供需催化",
                confidence=0.9,
                inference="短线关注产业链确认",
                source_region="domestic",
            ),
        ),
        source_status="partial",
        event_status="high_impact",
        raw_news_count=1,
    )
    (archive_dir / "news-2026-07-09.json").write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(tmp_path / "missing-latest.json"))
    monkeypatch.setenv("AQSP_NEWS_ARCHIVE_DIR", str(archive_dir))

    snapshot = write_home_snapshot.build_home_snapshot(
        _DateAwareProvider(), signal_date="2026-07-09", task_id="intraday"
    )

    assert snapshot.message_status == "部分可用"
    assert [item.title for item in snapshot.messages] == ["PCB 供需变化"]


def test_write_home_snapshot_structures_current_cross_market_context(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "\n".join(
            (
                "# 消息面雷达-2026-07-10|可用",
                "",
                "## 结论",
                "",
                "- 海外商业航天催化",
                "- 数据状态: 可用",
                "- 事件状态: 已筛出高影响事件",
                "",
                "## 事件",
                "",
                "- 1. 利好 | 全市场 | 资本运作",
                "- 结果: SpaceX 评估 IPO 上市窗口",
                "- 结论: 海外商业航天风险偏好升温",
                "- 影响: 利好",
                "- 来源: 新华社 | 质量 多源/权威媒体（3/4） | 区域 international",
                "- 时间: 2026-07-10T09:00:00+08:00",
                "",
                "## 状态",
                "",
                "- 状态: ok",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.market_context is not None
    assert snapshot.market_context.cross_market[0].rule_id == "commercial_space"
    assert snapshot.market_context.cross_market[0].source_region == "international"
    assert any(message.category == "跨市场传导" for message in snapshot.messages)


def test_write_home_snapshot_treats_explicit_empty_event_report_as_no_high_impact() -> (
    None
):
    from scripts import write_home_snapshot

    status = write_home_snapshot._report_event_status(
        "## 事件\n\n- 未筛出高影响消息\n## 状态\n", "partial"
    )

    assert status == "no_high_impact"


def test_write_home_snapshot_marks_historical_news_as_excluded(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "# 消息面雷达-2026-07-09|可用\n\n## 事件\n\n- 1. 利好 | 全市场 | 跨市\n"
        "- 结果: 历史消息\n- 结论: 不应进入当天快照\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "历史消息已排除"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert snapshot.market_context.status == "历史消息已排除"


def test_normalize_catalyst_report_downgrades_historical_high_impact_status() -> None:
    report = CatalystReport(
        date="2026-07-19",
        generated_at="2026-07-19T15:36:11+08:00",
        source_status="partial",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="旧事件",
                source="fixture",
                published_at="2026-07-17T09:00:00+08:00",
                impact="positive",
            ),
        ),
    )

    normalized, historical_count, invalid_count = (
        write_home_snapshot._normalize_catalyst_report_for_snapshot(
            report, "2026-07-19"
        )
    )

    assert historical_count == 1
    assert invalid_count == 0
    assert normalized.events == ()
    assert normalized.news_status == "stale_only"


def test_write_home_snapshot_explains_empty_current_news(monkeypatch, tmp_path) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "# 消息面雷达-2026-07-10|可用\n\n## 结论\n\n"
        "- 无强事件\n- 数据状态: 可用\n- 事件状态: 抓取成功但未筛出高影响事件\n"
        "\n## 事件\n\n- 未筛出高影响消息\n\n## 状态\n\n- 状态: ok\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "无高影响消息"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert snapshot.market_context.status == "无高影响消息"
    assert any(
        line == "消息结果: 抓取成功但未筛出高影响事件"
        for line in snapshot.market_context.summary_lines
    )


def test_write_home_snapshot_does_not_label_domestic_news_as_overseas_risk() -> None:
    artifact = SimpleNamespace(
        summary_lines=("海外风险: 偏多（正面 1 / 负面 0）", "消息状态: 部分可用"),
        catalyst_events=(SimpleNamespace(source_region="domestic"),),
        cross_market_implications=(),
        cross_market_overview="",
        source_status="partial",
        warnings=(),
    )

    context = write_home_snapshot._snapshot_market_context(artifact)

    assert context.summary_lines == ("消息状态: 部分可用",)


class _DateAwareProvider(_Provider):
    def home_digest_payload(
        self, task_id: str, signal_date: str = ""
    ) -> SimpleNamespace:
        payload = super().home_digest_payload(task_id, signal_date)
        selected_date = signal_date or payload.task_view.selected_date
        payload.task_view.selected_date = selected_date
        payload.task_view.latest_date = selected_date
        return payload


def test_write_home_snapshot_builds_optional_four_day_index(monkeypatch) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    index = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert index.available_dates == (
        "2026-07-10",
        "2026-07-09",
        "2026-07-08",
        "2026-07-07",
    )
    assert index.snapshot_for_date("2026-07-09") is not None
    assert index.snapshot_for_date("2026-07-04") is None


def test_merge_home_snapshot_index_preserves_unrequested_history() -> None:
    provider = _DateAwareProvider()
    existing = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-10", task_id="intraday"
    )
    refreshed = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-09", task_id="intraday"
    )

    merged = write_home_snapshot.merge_home_snapshot_index(existing, refreshed)

    assert merged.selected_date == "2026-07-09"
    assert merged.snapshot_for_date("2026-07-09") == refreshed.snapshot_for_date(
        "2026-07-09"
    )
    historical = merged.snapshot_for_date("2026-07-10")
    original = existing.snapshot_for_date("2026-07-10")
    assert historical.candidates == original.candidates
    assert historical.available_dates == merged.available_dates


def test_write_home_snapshot_cli_honors_output_date_and_task_id(
    monkeypatch, tmp_path, capsys
) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(write_home_snapshot, "DashboardDataProvider", lambda: provider)
    output = tmp_path / "snapshot.json"
    index_output = tmp_path / "snapshot-index.json"

    result = write_home_snapshot.main(
        [
            "--output",
            str(output),
            "--date",
            "2026-07-10",
            "--task-id",
            "intraday",
            "--index-output",
            str(index_output),
        ]
    )

    snapshot = load_home_dashboard_snapshot(output)
    assert result == 0
    assert snapshot is not None
    assert snapshot.selected_date == "2026-07-10"
    assert provider.digest_calls == [
        ("intraday", date)
        for date in ("2026-07-10", "2026-07-09", "2026-07-08", "2026-07-07")
    ]
    assert "task=intraday" in capsys.readouterr().out
    assert load_home_snapshot_index(index_output) is not None


def test_write_home_snapshot_cli_writes_default_index_and_env_overrides_path(
    monkeypatch, tmp_path
) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(write_home_snapshot, "DashboardDataProvider", lambda: provider)
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    output = tmp_path / "snapshot.json"
    index_output = tmp_path / "snapshot-index.json"
    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_INDEX_PATH", str(index_output))

    result = write_home_snapshot.main(
        [
            "--output",
            str(output),
            "--date",
            "2026-07-10",
            "--task-id",
            "intraday",
        ]
    )

    index = load_home_snapshot_index(index_output)
    assert result == 0
    assert index is not None
    assert len(index.days) == 4


def test_write_home_snapshot_cli_rejects_shared_snapshot_and_index_path(
    monkeypatch, tmp_path
) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(write_home_snapshot, "DashboardDataProvider", lambda: provider)
    output = tmp_path / "same.json"

    with pytest.raises(ValueError, match="different paths"):
        write_home_snapshot.main(
            [
                "--output",
                str(output),
                "--index-output",
                str(output),
                "--date",
                "2026-07-10",
            ]
        )
