from __future__ import annotations

from datetime import datetime

from aqsp.core.types import PickResult
from aqsp.market_context import (
    REALTIME_CROSS_MARKET_INSTRUMENTS,
    RealtimeCrossMarketPolicy,
    build_market_context_artifact,
    build_realtime_cross_market_context,
    build_pick_market_context,
    combine_cross_market_overview,
    cross_market_rule_runtime_lines,
    cross_market_rule_runtime_summary,
    format_pick_market_context_chain_summary,
    format_pick_market_context_summary,
    market_context_lines_for_pick,
    market_context_metrics_for_pick,
    relevant_cross_market_implications_for_pick,
)
from aqsp.news.catalysts import CatalystEvent, CatalystReport


def _realtime_cross_market_payload() -> dict[str, dict[str, object]]:
    return {
        instrument: {
            "value": float(index + 1),
            "change_pct": 0.25,
            "source": "fixture-feed",
            "source_url": "https://example.test/market",
            "observed_at": "2026-07-14T09:59:30+08:00",
            "fetched_at": "2026-07-14T09:59:40+08:00",
            "timestamp_source": "vendor",
        }
        for index, instrument in enumerate(REALTIME_CROSS_MARKET_INSTRUMENTS)
    }


_REALTIME_NOW = datetime.fromisoformat("2026-07-14T10:00:00+08:00")


def test_market_context_runtime_summary_exposes_core_cross_market_rules() -> None:
    summary = cross_market_rule_runtime_summary(
        enable_domestic_intelligence=True,
        enable_global_intelligence=True,
    )

    assert summary.global_enabled is True
    assert summary.rule_count >= 8
    assert "commercial_space" in summary.core_rule_ids
    assert "physical_ai" in summary.core_rule_ids
    assert "geopolitics" in summary.core_rule_ids
    assert "oil_price_shock" in summary.core_rule_ids
    assert "海外商业航天催化" in summary.rule_themes
    assert "国际油价冲击" in summary.rule_themes
    assert summary.advisory_boundary == "deterministic_context_priority_only"
    lines = cross_market_rule_runtime_lines()
    assert any(line.startswith("- cross_market_rule_count:") for line in lines)


def test_realtime_cross_market_context_marks_all_supported_instruments_fresh() -> None:
    context = build_realtime_cross_market_context(
        _realtime_cross_market_payload(),
        now=_REALTIME_NOW,
    )

    assert context.status == "fresh"
    assert context.available_instruments == REALTIME_CROSS_MARKET_INSTRUMENTS
    assert [item.instrument for item in context.observations] == list(
        REALTIME_CROSS_MARKET_INSTRUMENTS
    )
    assert all(item.value is not None for item in context.observations)
    assert all(item.status == "fresh" for item in context.observations)
    assert context.observations[0].provenance.source == "fixture-feed"
    assert context.observations[0].provenance.timestamp_source == "vendor"
    assert context.observations[0].provenance.fetched_at.endswith("+08:00")


def test_realtime_cross_market_context_keeps_missing_values_unavailable_not_zero() -> (
    None
):
    context = build_realtime_cross_market_context({}, now=_REALTIME_NOW)

    assert context.status == "unavailable"
    assert context.available_instruments == ()
    assert all(item.status == "unavailable" for item in context.observations)
    assert all(item.value is None for item in context.observations)
    assert all(item.change_pct is None for item in context.observations)
    assert all("未提供" in item.detail for item in context.observations)


def test_realtime_cross_market_context_marks_observations_stale_by_age() -> None:
    payload = _realtime_cross_market_payload()
    payload["SPX"]["observed_at"] = "2026-07-14T09:00:00+08:00"
    context = build_realtime_cross_market_context(
        payload,
        now=_REALTIME_NOW,
        policy=RealtimeCrossMarketPolicy(max_age_seconds=60),
    )

    spx = context.observations[0]
    assert context.status == "partial"
    assert spx.status == "stale"
    assert spx.value == 1.0
    assert spx.age_seconds == 60 * 60
    assert any("SPX: stale" in warning for warning in context.warnings)


def test_realtime_cross_market_context_normalizes_shanghai_gold_alias() -> None:
    payload = _realtime_cross_market_payload()
    gold_payload = payload.pop("GOLD")
    payload["上海金"] = gold_payload

    context = build_realtime_cross_market_context(payload, now=_REALTIME_NOW)

    gold = next(item for item in context.observations if item.instrument == "GOLD")
    assert gold.status == "fresh"
    assert gold.value == 7.0
    assert "GOLD" in context.available_instruments


def test_realtime_cross_market_context_surfaces_gold_timeout() -> None:
    payload = _realtime_cross_market_payload()
    payload["GOLD"] = {"status": "timeout", "value": 2_400.0}

    context = build_realtime_cross_market_context(payload, now=_REALTIME_NOW)

    gold = next(item for item in context.observations if item.instrument == "GOLD")
    assert gold.status == "timeout"
    assert gold.value is None
    assert any("GOLD: timeout" in warning for warning in context.warnings)


def test_realtime_cross_market_context_marks_timeout_without_numeric_fallback() -> None:
    payload = _realtime_cross_market_payload()
    payload["SPX"] = {
        "status": "timeout",
        "value": 123.0,
        "source": "fixture-feed",
        "fetched_at": "2026-07-14T10:00:00+08:00",
    }
    context = build_realtime_cross_market_context(payload, now=_REALTIME_NOW)

    spx = context.observations[0]
    assert context.status == "partial"
    assert spx.status == "timeout"
    assert spx.value is None
    assert spx.change_pct is None
    assert any("SPX: timeout" in warning for warning in context.warnings)


def test_realtime_cross_market_context_marks_elapsed_timeout_explicitly() -> None:
    payload = _realtime_cross_market_payload()
    payload["WTI"]["fetch_elapsed_seconds"] = 6.0
    context = build_realtime_cross_market_context(payload, now=_REALTIME_NOW)

    wti = next(item for item in context.observations if item.instrument == "WTI")
    assert wti.status == "timeout"
    assert wti.value is None
    assert "超过 5.000s" in wti.detail


def test_realtime_cross_market_context_requires_timezone_and_source_provenance() -> (
    None
):
    payload = _realtime_cross_market_payload()
    payload["DXY"].pop("source")
    payload["US10Y"]["observed_at"] = "2026-07-14T09:59:30"
    context = build_realtime_cross_market_context(payload, now=_REALTIME_NOW)

    dxy = next(item for item in context.observations if item.instrument == "DXY")
    us10y = next(item for item in context.observations if item.instrument == "US10Y")
    assert dxy.status == "unavailable"
    assert us10y.status == "unavailable"
    assert dxy.value is None
    assert us10y.value is None
    assert "来源或带时区时间戳缺失" in dxy.detail
    assert "来源或带时区时间戳缺失" in us10y.detail


def test_market_context_artifact_keeps_realtime_context_out_of_deterministic_score() -> (
    None
):
    artifact = build_market_context_artifact(
        catalyst_report=None,
        realtime_cross_market=_realtime_cross_market_payload(),
        realtime_now=_REALTIME_NOW,
    )
    pick = PickResult(
        symbol="600519",
        name="示例标的",
        date="2026-07-14",
        close=100.0,
        score=72.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        position="watch",
    )

    assert artifact.realtime_cross_market is not None
    assert artifact.realtime_cross_market.status == "fresh"
    assert market_context_metrics_for_pick(pick, artifact) == {}
    assert pick.score == 72.0


def test_market_context_blocks_news_without_source_when_timestamp_is_fresh() -> None:
    report = CatalystReport(
        date="2026-07-13",
        generated_at="2026-07-13T10:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="SpaceX evaluates IPO window",
                source="",
                published_at="2026-07-13T09:45:00+08:00",
                impact="positive",
                category="资本运作",
                confidence=0.9,
                source_quality_label="高价值来源",
                source_quality_score=4,
                inference="商业航天预期升温。",
            ),
        ),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert artifact.cross_market_implications == ()
    assert any("无可追踪来源" in warning for warning in artifact.warnings)


def test_market_context_direct_news_fallback_exposes_full_chain_when_no_rule_matches() -> (
    None
):
    report = CatalystReport(
        date="2026-07-13",
        generated_at="2026-07-13T10:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="宁德时代中标储能大单",
                source="公司公告",
                published_at="2026-07-13T09:45:00+08:00",
                symbol="300750",
                name="宁德时代",
                impact="positive",
                category="订单/需求验证",
                confidence=0.9,
                source_quality_label="高价值来源",
                source_quality_score=4,
                inference="宁德时代订单催化明确，短线偏强。",
                url="https://example.com/catl-order",
            ),
        ),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-13",
        close=100.0,
        score=70.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        position="watch",
        metrics={"sector": "储能", "industry": "电池"},
    )

    metrics = market_context_metrics_for_pick(pick, artifact)

    assert metrics["cross_market_context_only"] is True
    assert metrics["cross_market_score_adjustment_allowed"] is False
    assert metrics["cross_market_first_order_targets"] == ("300750 宁德时代",)
    assert metrics["cross_market_second_order_targets"] == (
        "电池",
        "储能",
        "同主题竞品/上下游",
    )
    assert len(metrics["cross_market_transmission_path"]) == 3
    assert metrics["cross_market_validation_signals"]
    assert metrics["cross_market_invalidation_signals"]
    assert metrics["cross_market_source_published_at"] == ("2026-07-13T09:45:00+08:00")


def test_market_context_keeps_event_chain_and_resonance_confirmation_for_pick() -> None:
    report = CatalystReport(
        date="2026-07-13",
        generated_at="2026-07-13T10:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="某厂商发布新一代800G光模块产品",
                source="公司公告",
                published_at="2026-07-13T09:45:00+08:00",
                symbol="300001",
                name="样本光模块",
                impact="positive",
                category="新品/产品发布",
                confidence=0.9,
                source_quality_label="高价值来源",
                source_quality_score=4,
                affected_sectors=("光模块", "服务器", "AI算力"),
                transmission_path=(
                    "海外算力资本开支",
                    "光模块/服务器订单",
                    "上游芯片与散热交付",
                ),
                validation_signals=(
                    "公司订单、扩产或出货被公告确认",
                    "光模块与服务器成交扩散",
                ),
                invalidation_signals=(
                    "只有产品发布没有订单",
                    "订单兑现或板块成交明显转弱",
                ),
            ),
        ),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="300001",
        name="样本光模块",
        date="2026-07-13",
        close=100.0,
        score=70.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        position="watch",
        metrics={"sector": "光模块", "industry": "服务器"},
    )

    metrics = market_context_metrics_for_pick(pick, artifact)

    assert metrics["cross_market_context_only"] is True
    assert metrics["cross_market_score_adjustment_allowed"] is False
    assert metrics["cross_market_second_order_targets"] == (
        "光模块",
        "服务器",
        "AI算力",
        "同主题竞品/上下游",
    )
    assert metrics["cross_market_transmission_path"] == (
        "公司公告消息 -> 300001 样本光模块",
        "海外算力资本开支",
        "光模块/服务器订单",
        "上游芯片与散热交付",
        "价格与成交确认后再判断催化是否延续",
    )
    assert metrics["cross_market_validation_signals"] == (
        "公司订单、扩产或出货被公告确认",
        "光模块与服务器成交扩散",
    )


def test_market_context_artifact_summarizes_symbol_global_and_flow_signals() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="partial",
        events=(
            CatalystEvent(
                title="锂盐涨价预期升温，多家厂商报价上调",
                source="财联社",
                published_at="2026-06-30 10:02:00+08:00",
                symbol="002594",
                name="比亚迪",
                impact="positive",
                category="涨价/供需催化",
                source_quality_label="主流媒体",
                source_quality_score=2,
                inference="上游涨价预期升温，整车链需区分成本与弹性。",
            ),
            CatalystEvent(
                title="海外风险资产走弱，美元指数反弹",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="negative",
                category="宏观风险",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="海外风险偏好回落，需警惕高 beta 承压。",
            ),
        ),
        warnings=("全市场快讯: 已过滤 2 条过期消息",),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        northbound_flow_5d_z=1.34,
        margin_balance_change_5d=0.041,
    )

    assert artifact.source_status == "partial"
    assert artifact.news_status == "high_impact"
    assert artifact.warnings == ("全市场快讯: 已过滤 2 条过期消息",)
    assert artifact.summary_lines[0].startswith("个股催化: 002594 比亚迪 偏多")
    assert artifact.summary_lines[1].startswith("全局雷达: 全市场 偏空")
    assert any("来源质量:" in line for line in artifact.summary_lines)
    assert any("海外风险: 偏空" in line for line in artifact.summary_lines)
    assert "消息状态: 部分可用" in artifact.summary_lines
    assert any("北向资金: 偏强" in line for line in artifact.summary_lines)
    assert any("综合风向: 分化" in line for line in artifact.summary_lines)


def test_market_context_accepts_mixed_timezone_event_times() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="NVIDIA",
                published_at="2026-06-30T09:40:00",
                impact="positive",
                category="科技催化",
                source_quality_label="高价值来源",
                source_quality_score=4,
                inference="机器人链预期升温。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert any("情报时效:" in line for line in artifact.summary_lines)


def test_market_context_turns_symbol_news_into_candidate_judgement() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:10:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="宁德时代中标储能大单",
                source="公告",
                published_at="2026-07-07T08:40:00+08:00",
                symbol="300750",
                name="宁德时代",
                impact="positive",
                category="订单/需求验证",
                confidence=0.78,
                source_quality_label="高价值来源",
                source_quality_score=4,
                inference="宁德时代 交易催化明确，短线偏强。",
                url="https://example.com/catl",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-07",
        close=180.0,
        score=70.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=180.0,
        stop_loss=170.0,
        take_profit=198.0,
        position="watch",
        metrics={"industry": "电池"},
    )

    metrics = market_context_metrics_for_pick(pick, artifact)

    assert metrics["news_catalyst_judgement"] == "supports"
    assert metrics["news_catalyst_support_count"] == 1
    assert metrics["cross_market_primary_theme"] == "消息面直接催化"
    assert metrics["cross_market_action"] == "优先复核"
    assert metrics["cross_market_priority_score"] == 3
    assert metrics["cross_market_score_adjustment_allowed"] is False
    assert metrics["cross_market_context_only"] is True
    assert any(
        line.startswith("消息支持: 300750 宁德时代")
        for line in market_context_lines_for_pick(pick, artifact)
    )


def test_market_context_marks_negative_news_as_opposing_candidate() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:10:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="宁德时代被监管问询",
                source="交易所",
                published_at="2026-07-07T08:40:00+08:00",
                symbol="300750",
                name="宁德时代",
                impact="negative",
                category="监管/合规风险",
                confidence=0.82,
                source_quality_label="高价值来源",
                source_quality_score=4,
                inference="宁德时代 风险抬升，短线回避监管/合规风险方向。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-07",
        close=180.0,
        score=70.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=180.0,
        stop_loss=170.0,
        take_profit=198.0,
        position="watch",
    )

    metrics = market_context_metrics_for_pick(pick, artifact)

    assert metrics["news_catalyst_judgement"] == "opposes"
    assert metrics["news_catalyst_oppose_count"] == 1
    assert metrics["cross_market_action"] == "风险复核"
    assert metrics["cross_market_score_adjustment_allowed"] is False
    assert metrics["cross_market_context_only"] is True


def test_market_context_artifact_surfaces_cache_fallback_warning() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="partial",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能和机器人链预期升温。",
            ),
        ),
        warnings=("消息缓存回退: 使用 5 分钟前摘要",),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        northbound_flow_5d_z=0.0,
        margin_balance_change_5d=0.0,
    )

    assert "消息状态: 部分可用" in artifact.summary_lines
    assert "消息缓存回退: 使用 5 分钟前摘要" in artifact.summary_lines


def test_market_context_artifact_summarizes_timeout_warning_when_cache_fallback_missing() -> (
    None
):
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="partial",
        events=(),
        warnings=("全市场快讯: 部分消息源超时或连接中断，已降级使用其它来源",),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        northbound_flow_5d_z=0.0,
        margin_balance_change_5d=0.0,
    )

    assert "消息补位: 部分来源超时，已按可用摘要继续。" in artifact.summary_lines


def test_market_context_artifact_marks_unloaded_news_instead_of_claiming_no_signal() -> (
    None
):
    artifact = build_market_context_artifact(
        catalyst_report=None,
        northbound_flow_5d_z=0.2,
        margin_balance_change_5d=0.01,
    )

    assert artifact.source_status == "not_loaded"
    assert artifact.summary_lines == (
        "消息状态: 未加载，不能据此判断暂无消息；维持价格与成交主导。",
    )
    assert artifact.warnings == ("消息源未加载：不得将空结果视为无消息。",)


def test_market_context_artifact_marks_mixed_external_signals_as_divergent() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="海外芯片指数反弹",
                source="财联社",
                published_at="2026-06-30 10:02:00+08:00",
                impact="positive",
                category="风险偏好",
                inference="成长风格风险偏好回暖。",
            ),
            CatalystEvent(
                title="美元继续走强",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="negative",
                category="宏观风险",
                inference="美元偏强压制风险资产估值。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        northbound_flow_5d_z=-1.2,
        margin_balance_change_5d=0.0,
    )

    assert any("海外风险: 分化" in line for line in artifact.summary_lines)
    assert any("综合风向: 分化" in line for line in artifact.summary_lines)


def test_market_context_implication_evidence_includes_source_quality() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="科技催化",
                confidence=0.82,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="具身智能和机器人链预期升温。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    implication_line = next(
        line for line in artifact.summary_lines if line.startswith("传导推演[")
    )
    assert "多源/权威媒体" in implication_line


def test_market_context_artifact_respects_global_intelligence_switch(
    monkeypatch, tmp_path
) -> None:
    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  domestic_market_intelligence:
    enabled: true
    purpose: domestic on
  global_market_intelligence:
    enabled: false
    purpose: global off
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="比亚迪获上游报价催化",
                source="财联社",
                published_at="2026-06-30 10:02:00+08:00",
                symbol="002594",
                name="比亚迪",
                impact="positive",
                category="涨价/供需催化",
                inference="上游涨价预期升温。",
            ),
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能和机器人链预期升温。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        northbound_flow_5d_z=1.34,
        margin_balance_change_5d=0.041,
    )

    assert any(
        line.startswith("个股催化: 002594 比亚迪") for line in artifact.summary_lines
    )
    assert any("北向资金: 偏强" in line for line in artifact.summary_lines)
    assert not any(line.startswith("全局雷达:") for line in artifact.summary_lines)
    assert not any(line.startswith("海外风险:") for line in artifact.summary_lines)
    assert artifact.cross_market_implications == ()
    assert artifact.cross_market_overview == ""


def test_market_context_artifact_respects_domestic_intelligence_switch(
    monkeypatch, tmp_path
) -> None:
    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  domestic_market_intelligence:
    enabled: false
    purpose: domestic off
  global_market_intelligence:
    enabled: true
    purpose: global on
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="比亚迪获上游报价催化",
                source="财联社",
                published_at="2026-06-30 10:02:00+08:00",
                symbol="002594",
                name="比亚迪",
                impact="positive",
                category="涨价/供需催化",
                inference="上游涨价预期升温。",
            ),
            CatalystEvent(
                title="纳斯达克大涨带动科技股反弹",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="风险偏好",
                inference="海外风险资产反弹，A股成长风格关注度提升。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        northbound_flow_5d_z=1.34,
        margin_balance_change_5d=0.041,
    )

    assert not any(line.startswith("个股催化:") for line in artifact.summary_lines)
    assert not any(line.startswith("北向资金:") for line in artifact.summary_lines)
    assert not any(line.startswith("融资情绪:") for line in artifact.summary_lines)
    assert any(
        line.startswith("全局雷达: 全市场 偏多") for line in artifact.summary_lines
    )
    assert any(line.startswith("海外风险: 偏多") for line in artifact.summary_lines)
    assert len(artifact.cross_market_implications) == 1
    assert (
        artifact.cross_market_overview
        == "外盘风险偏好修复，重点看 A股成长、高弹性、AI链"
    )


def test_market_context_artifact_adds_cross_market_implication_lines() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="SpaceX 拟推进上市并加快低轨卫星部署",
                source="财联社",
                published_at="2026-06-30 10:02:00+08:00",
                impact="positive",
                category="资本运作",
                inference="海外商业航天关注度抬升。",
            ),
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能和机器人链预期升温。",
            ),
            CatalystEvent(
                title="中东冲突升级推升避险需求",
                source="央视",
                published_at="2026-06-30 08:20:00+08:00",
                impact="negative",
                category="宏观风险",
                inference="地缘风险抬升，黄金和军工关注度提升。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        northbound_flow_5d_z=0.0,
        margin_balance_change_5d=0.0,
    )

    assert any(
        "传导推演[中]: 海外商业航天催化 -> A股商业航天、卫星互联网、军工电子；动作 重点跟踪"
        in line
        for line in artifact.summary_lines
    )
    assert any(
        "传导推演[中]: 海外物理AI叙事升温 -> A股机器人、AI算力、传感器、丝杠、减速器；动作 重点跟踪"
        in line
        for line in artifact.summary_lines
    )
    assert any(
        "传导推演[中]: 地缘冲突升温 -> A股黄金、军工、能源链；动作 重点跟踪" in line
        for line in artifact.summary_lines
    )
    assert any("观察窗 2-5日" in line for line in artifact.summary_lines)
    assert len(artifact.cross_market_implications) == 3
    assert artifact.cross_market_implications[0].action == "重点跟踪"
    assert (
        artifact.cross_market_overview
        == "海外物理AI叙事升温，重点看 A股机器人、AI算力、传感器"
    )


def test_market_context_aggregates_multi_event_theme_stack_and_conflict() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T11:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 10:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能和机器人链预期升温。",
            ),
            CatalystEvent(
                title="海外人形机器人融资继续升温",
                source="财联社",
                published_at="2026-06-30 10:20:00+08:00",
                impact="positive",
                category="资本运作",
                inference="机器人映射链再次获得资金关注。",
            ),
            CatalystEvent(
                title="海外机器人订单兑现节奏低于预期",
                source="路透",
                published_at="2026-06-30 09:50:00+08:00",
                impact="negative",
                category="订单扰动",
                inference="主题仍在，但兑现节奏存在分歧。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert len(artifact.cross_market_implications) == 1
    implication = artifact.cross_market_implications[0]
    assert implication.rule_id == "physical_ai"
    assert implication.support_event_count == 2
    assert implication.conflict_event_count == 1
    assert implication.evidence_stack_summary == "同向 2 条｜反向 1 条"
    assert implication.action == "优先复核"
    assert "同向 2 条｜反向 1 条" in implication.summary_line

    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-06-30",
        close=220.0,
        score=70.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=220.0,
        stop_loss=210.0,
        take_profit=240.0,
        position="watch",
        metrics={"sector": "机器人", "industry": "机器人"},
    )
    context = build_pick_market_context(pick, artifact)
    assert context.support_event_count == 2
    assert context.conflict_event_count == 1
    assert context.evidence_stack_summary == "同向 2 条｜反向 1 条"

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_support_event_count"] == 2
    assert metrics["cross_market_conflict_event_count"] == 1
    assert metrics["cross_market_evidence_stack_summary"] == "同向 2 条｜反向 1 条"
    assert metrics["cross_market_source_quality_label"] == "多源/权威媒体"
    assert metrics["cross_market_source_quality_score"] == 3
    assert metrics["cross_market_first_order_targets"] == (
        "机器人整机",
        "AI算力/边缘计算",
        "丝杠/减速器",
        "传感器",
    )
    formatted_pick = PickResult(
        symbol=pick.symbol,
        name=pick.name,
        date=pick.date,
        close=pick.close,
        score=pick.score,
        rating=pick.rating,
        entry_type=pick.entry_type,
        ideal_buy=pick.ideal_buy,
        stop_loss=pick.stop_loss,
        take_profit=pick.take_profit,
        position=pick.position,
        metrics=metrics,
    )
    assert "同向 2 条｜反向 1 条" in format_pick_market_context_chain_summary(
        formatted_pick
    )
    pick_lines = market_context_lines_for_pick(pick, artifact)
    assert "证据堆栈: 同向 2 条｜反向 1 条" in pick_lines


def test_market_context_maps_physical_ai_to_ai_compute_pick_when_relevant() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T11:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 10:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能、机器人和边缘算力链预期升温。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="688256",
        name="寒武纪",
        date="2026-06-30",
        close=260.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=260.0,
        stop_loss=245.0,
        take_profit=285.0,
        position="watch",
        metrics={"sector": "算力", "industry": "AI芯片"},
    )

    assert artifact.cross_market_implications[0].rule_id == "physical_ai"
    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "海外物理AI叙事升温"
    assert metrics["cross_market_first_order_targets"] == (
        "机器人整机",
        "AI算力/边缘计算",
        "丝杠/减速器",
        "传感器",
    )
    assert metrics["cross_market_second_order_targets"] == (
        "工控",
        "机器视觉",
        "伺服",
        "算力芯片",
    )
    assert (
        "AI算力或边缘计算分支同步放量扩散" in metrics["cross_market_validation_signals"]
    )


def test_market_context_does_not_trigger_physical_ai_on_generic_nvidia_news() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T11:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达财报超预期，数据中心收入增长",
                source="新华社",
                published_at="2026-06-30 10:40:00+08:00",
                impact="positive",
                category="科技公司",
                inference="海外芯片公司业绩增长，主要映射数据中心需求。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert not any(
        item.rule_id == "physical_ai" for item in artifact.cross_market_implications
    )


def test_market_context_filters_cross_market_implications_by_pick_relevance() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="SpaceX 拟推进上市并加快低轨卫星部署",
                source="财联社",
                published_at="2026-06-30 10:02:00+08:00",
                impact="positive",
                category="资本运作",
                inference="海外商业航天关注度抬升。",
            ),
            CatalystEvent(
                title="英伟达发布 Physical AI 平台",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="科技催化",
                inference="具身智能和机器人链预期升温。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    space_pick = PickResult(
        symbol="688297",
        name="中无人机",
        date="2026-06-30",
        close=45.0,
        score=71.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=45.0,
        stop_loss=42.0,
        take_profit=50.0,
        position="watch",
        metrics={"sector": "军工", "industry": "商业航天"},
    )
    bank_pick = PickResult(
        symbol="600036",
        name="招商银行",
        date="2026-06-30",
        close=45.0,
        score=71.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=45.0,
        stop_loss=42.0,
        take_profit=50.0,
        position="watch",
        metrics={"sector": "银行", "industry": "股份制银行"},
    )

    relevant = relevant_cross_market_implications_for_pick(
        space_pick,
        artifact.cross_market_implications,
    )
    assert len(relevant) == 1
    assert relevant[0].rule_id == "commercial_space"
    pick_context = build_pick_market_context(space_pick, artifact)
    assert pick_context.primary_theme == "海外商业航天催化"
    assert pick_context.linkage_basis == "题材映射"
    assert pick_context.primary_action == "重点跟踪"
    assert pick_context.lead_window == "隔夜-2日"
    assert pick_context.priority_score == 2
    metrics = market_context_metrics_for_pick(space_pick, artifact)
    assert metrics["cross_market_primary_theme"] == "海外商业航天催化"
    assert metrics["cross_market_linkage_basis"] == "题材映射"
    assert metrics["cross_market_lead_window"] == "隔夜-2日"
    assert metrics["cross_market_first_order_targets"] == (
        "商业航天龙头",
        "卫星互联网/低轨组网",
        "火箭发射配套",
    )
    assert metrics["cross_market_second_order_targets"] == (
        "军工电子",
        "通信设备",
        "高端制造",
    )
    assert (
        metrics["cross_market_validation_signals"][0]
        == "商业航天龙头高开后仍有放量换手承接"
    )
    assert (
        metrics["cross_market_invalidation_signals"][0]
        == "只有 SpaceX 新闻刺激但A股商业航天家数不扩散"
    )
    formatted_pick = PickResult(
        symbol=space_pick.symbol,
        name=space_pick.name,
        date=space_pick.date,
        close=space_pick.close,
        score=space_pick.score,
        rating=space_pick.rating,
        entry_type=space_pick.entry_type,
        ideal_buy=space_pick.ideal_buy,
        stop_loss=space_pick.stop_loss,
        take_profit=space_pick.take_profit,
        position=space_pick.position,
        metrics=metrics,
    )
    assert (
        format_pick_market_context_summary(formatted_pick)
        == "重点跟踪｜海外商业航天催化｜观察窗 2-5日"
    )
    assert (
        format_pick_market_context_chain_summary(formatted_pick)
        == "题材映射｜领先窗 隔夜-2日｜先看 商业航天龙头｜锚点 商业航天龙头竞价强度与换手承接｜确认 商业航天龙头高开后仍有放量换手承接｜失效 只有 SpaceX 新闻刺激但A股商业航天家数不扩散"
    )

    space_lines = market_context_lines_for_pick(space_pick, artifact)
    assert any("海外商业航天催化" in line for line in space_lines)
    assert any(
        line.startswith("传导链: 题材映射｜领先窗 隔夜-2日") for line in space_lines
    )
    assert any(
        line.startswith("先看链条: 商业航天龙头、卫星互联网/低轨组网、火箭发射配套")
        for line in space_lines
    )
    assert any(
        line.startswith("扩散链条: 军工电子、通信设备、高端制造")
        for line in space_lines
    )
    assert any(
        line.startswith("盘中锚点: 商业航天龙头竞价强度与换手承接")
        for line in space_lines
    )
    assert any(
        line.startswith("确认信号: 商业航天龙头高开后仍有放量换手承接")
        for line in space_lines
    )
    assert any(
        line.startswith("失效条件: 只有 SpaceX 新闻刺激但A股商业航天家数不扩散")
        for line in space_lines
    )
    assert not any("海外物理AI叙事升温" in line for line in space_lines)

    bank_lines = market_context_lines_for_pick(bank_pick, artifact)
    assert not any(line.startswith("传导推演[") for line in bank_lines)
    assert (
        combine_cross_market_overview(
            "海外商业航天催化，重点看 688297 中无人机",
            artifact,
        )
        == "海外商业航天催化，重点看 688297 中无人机；方向 商业航天、卫星互联网、军工电子"
    )


def test_market_context_maps_spacex_ipo_to_commercial_space_priority_review() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="马斯克称 SpaceX 正评估 IPO 上市窗口",
                source="新华社",
                published_at="2026-07-07T09:05:00+08:00",
                impact="positive",
                category="资本运作",
                confidence=0.86,
                source_count=2,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="SpaceX 估值和上市预期升温，海外商业航天风险偏好提升。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="688297",
        name="中无人机",
        date="2026-07-07",
        close=45.0,
        score=71.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=45.0,
        stop_loss=42.0,
        take_profit=50.0,
        position="watch",
        metrics={"sector": "商业航天", "industry": "卫星互联网"},
    )

    assert artifact.cross_market_implications[0].rule_id == "commercial_space"
    assert artifact.cross_market_implications[0].strength == "强"
    assert artifact.cross_market_implications[0].action == "优先复核"
    assert (
        artifact.cross_market_overview
        == "海外商业航天催化，优先看 A股商业航天、卫星互联网、军工电子"
    )

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "海外商业航天催化"
    assert metrics["cross_market_action"] == "优先复核"
    assert metrics["cross_market_priority_score"] == 3
    assert metrics["cross_market_score_adjustment_allowed"] is True
    assert metrics["cross_market_priority_boost"] is True
    assert metrics["cross_market_context_only"] is False
    assert metrics["cross_market_first_order_targets"] == (
        "商业航天龙头",
        "卫星互联网/低轨组网",
        "火箭发射配套",
    )
    assert metrics["cross_market_validation_signals"][0] == (
        "商业航天龙头高开后仍有放量换手承接"
    )
    assert metrics["cross_market_invalidation_signals"][0] == (
        "只有 SpaceX 新闻刺激但A股商业航天家数不扩散"
    )


def test_market_context_uses_symbol_profile_when_realtime_source_has_no_sector() -> (
    None
):
    artifact = build_market_context_artifact(
        catalyst_report=CatalystReport(
            date="2026-07-10",
            generated_at="2026-07-10T10:00:00+08:00",
            source_status="ok",
            events=(
                CatalystEvent(
                    title="SpaceX 拟推进上市并加快低轨卫星部署",
                    source="NASA",
                    published_at="2026-07-10T09:30:00+08:00",
                    impact="positive",
                    category="资本运作",
                    inference="海外商业航天关注度抬升。",
                ),
            ),
            warnings=(),
        )
    )
    pick = PickResult(
        symbol="600879",
        name="航天电子",
        date="2026-07-10",
        close=10.0,
        score=70.0,
        rating="watch",
        entry_type="volume_breakout",
        ideal_buy=10.0,
        stop_loss=9.0,
        take_profit=12.0,
        position="watch",
    )

    metrics = market_context_metrics_for_pick(pick, artifact)

    assert metrics["cross_market_primary_theme"] == "海外商业航天催化"
    assert metrics["cross_market_rule_ids"] == ("commercial_space",)


def test_market_context_does_not_trigger_commercial_space_on_office_space() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="Office space 需求回暖，海外写字楼租赁改善",
                source="新华社",
                published_at="2026-07-07T09:05:00+08:00",
                impact="positive",
                category="地产",
                confidence=0.86,
                source_count=2,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="办公楼空置率下降，主要影响海外地产租赁。",
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert artifact.cross_market_implications == ()


def test_market_context_blocks_stale_cross_market_news_from_actionable_context() -> (
    None
):
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="马斯克称 SpaceX 正评估 IPO 上市窗口",
                source="新华社",
                published_at="2026-07-05T09:05:00+08:00",
                impact="positive",
                category="资本运作",
                confidence=0.86,
                source_count=2,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="SpaceX 估值和上市预期升温，海外商业航天风险偏好提升。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="688297",
        name="中无人机",
        date="2026-07-07",
        close=45.0,
        score=71.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=45.0,
        stop_loss=42.0,
        take_profit=50.0,
        position="watch",
        metrics={"sector": "商业航天", "industry": "卫星互联网"},
    )

    assert artifact.cross_market_implications == ()
    assert artifact.catalyst_events == ()
    assert any(
        "情报门禁: 已排除 1 条超出短线窗口的旧消息" in line
        for line in artifact.summary_lines
    )
    assert market_context_metrics_for_pick(pick, artifact) == {}


def test_market_context_blocks_single_low_quality_source_from_actionable_context() -> (
    None
):
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="网传宁德时代获得储能订单",
                source="自媒体",
                published_at="2026-07-07T09:20:00+08:00",
                symbol="300750",
                name="宁德时代",
                impact="positive",
                category="传闻",
                confidence=0.6,
                source_count=1,
                source_quality_label="普通来源",
                source_quality_score=1,
                inference="网传订单消息，来源质量不足。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-07",
        close=180.0,
        score=70.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=180.0,
        stop_loss=170.0,
        take_profit=198.0,
        position="watch",
        metrics={"industry": "电池"},
    )

    assert artifact.catalyst_events == ()
    assert any(
        "情报门禁: 已排除 1 条普通单源消息" in line for line in artifact.summary_lines
    )
    assert market_context_metrics_for_pick(pick, artifact) == {}


def test_market_context_maps_domestic_policy_stimulus_to_short_term_targets() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="国常会部署新一轮设备更新和消费品以旧换新政策",
                source="新华社",
                published_at="2026-07-07T09:08:00+08:00",
                impact="positive",
                category="稳增长政策",
                confidence=0.84,
                source_count=2,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="设备更新、低空经济、汽车家电和工程机械短线政策预期升温。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="000425",
        name="徐工机械",
        date="2026-07-07",
        close=8.0,
        score=68.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=8.0,
        stop_loss=7.5,
        take_profit=8.8,
        position="watch",
        metrics={"sector": "设备更新", "industry": "工程机械"},
    )

    assert artifact.cross_market_implications[0].rule_id == "domestic_policy_stimulus"
    assert artifact.cross_market_implications[0].strength == "强"
    assert (
        artifact.cross_market_overview
        == "国内政策催化，优先看 A股设备更新、低空经济、汽车家电"
    )

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "国内政策催化"
    assert metrics["cross_market_linkage_basis"] == "政策预期差映射"
    assert metrics["cross_market_first_order_targets"] == (
        "设备更新",
        "低空经济",
        "汽车家电以旧换新",
    )
    assert metrics["cross_market_second_order_targets"] == (
        "工业母机/机器人",
        "工程机械",
        "基建链",
        "充电桩",
    )
    assert metrics["cross_market_pressure_targets"] == ("纯防御高股息",)
    assert metrics["cross_market_validation_signals"][0] == (
        "政策受益龙头竞价强且开盘后仍有换手承接"
    )
    assert metrics["cross_market_invalidation_signals"][0] == (
        "只有口号没有细则或资金安排"
    )


def test_market_context_honors_explicit_news_region_switches() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="国内政策支持设备更新",
                source="新华社",
                source_region="domestic",
                published_at="2026-07-07T09:20:00+08:00",
                impact="positive",
                category="稳增长政策",
                confidence=0.84,
                source_count=2,
                source_quality_score=3,
                inference="设备更新预期升温。",
            ),
            CatalystEvent(
                title="Nasdaq rallies as tech stocks lead",
                source="MarketWatch",
                source_region="international",
                published_at="2026-07-07T09:25:00+08:00",
                impact="positive",
                category="风险偏好",
                confidence=0.84,
                source_count=2,
                source_quality_score=3,
                inference="海外风险偏好改善。",
            ),
        ),
    )

    artifact = build_market_context_artifact(
        catalyst_report=report,
        enable_domestic_intelligence=False,
        enable_global_intelligence=True,
    )

    assert [event.source_region for event in artifact.catalyst_events] == [
        "international"
    ]
    assert "国内政策催化" not in artifact.cross_market_overview


def test_market_context_maps_low_altitude_policy_to_policy_not_physical_ai() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="发改委支持低空经济试点，工信部推进机器人设备更新",
                source="发改委",
                published_at="2026-07-07T09:12:00+08:00",
                impact="positive",
                category="产业政策",
                confidence=0.78,
                inference="低空经济、自动化和机器人产业链获得政策催化。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="002085",
        name="万丰奥威",
        date="2026-07-07",
        close=16.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=16.0,
        stop_loss=15.0,
        take_profit=17.8,
        position="watch",
        metrics={"sector": "低空经济", "industry": "飞行汽车"},
    )

    assert artifact.cross_market_implications[0].rule_id == "domestic_policy_stimulus"
    assert artifact.cross_market_implications[0].rule_id != "physical_ai"

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "国内政策催化"
    assert "低空经济" in metrics["cross_market_first_order_targets"]


def test_market_context_does_not_misfire_domestic_policy_on_generic_policy_noise() -> (
    None
):
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="市场人士称政策面仍需关注",
                source="财联社",
                published_at="2026-07-07T09:12:00+08:00",
                impact="positive",
                category="市场观点",
                confidence=0.7,
                inference="短线情绪仍待进一步观察。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)

    assert artifact.cross_market_implications == ()
    assert not any(line.startswith("传导推演[") for line in artifact.summary_lines)


def test_market_context_adds_pressure_targets_for_geopolitics_and_risk_on() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="中东冲突升级推升避险需求",
                source="央视",
                published_at="2026-06-30 08:20:00+08:00",
                impact="negative",
                category="宏观风险",
                inference="地缘风险抬升，黄金和军工关注度提升。",
            ),
            CatalystEvent(
                title="纳斯达克大涨带动科技股反弹",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="风险偏好",
                inference="海外风险资产反弹，A股成长风格关注度提升。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)

    gold_pick = PickResult(
        symbol="600489",
        name="中金黄金",
        date="2026-06-30",
        close=18.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=18.0,
        stop_loss=17.0,
        take_profit=20.0,
        position="watch",
        metrics={"sector": "黄金", "industry": "贵金属"},
    )
    ai_pick = PickResult(
        symbol="688256",
        name="寒武纪",
        date="2026-06-30",
        close=260.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=260.0,
        stop_loss=245.0,
        take_profit=285.0,
        position="watch",
        metrics={"sector": "芯片", "industry": "人工智能"},
    )

    gold_metrics = market_context_metrics_for_pick(gold_pick, artifact)
    assert gold_metrics["cross_market_pressure_targets"] == (
        "高beta成长",
        "风险偏好题材",
    )

    ai_metrics = market_context_metrics_for_pick(ai_pick, artifact)
    assert ai_metrics["cross_market_pressure_targets"] == ("高股息防御",)


def test_market_context_maps_us_risk_on_to_growth_ai_pick_when_relevant() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="美股大涨，纳斯达克科技股反弹",
                source="新华社",
                published_at="2026-06-30 09:40:00+08:00",
                impact="positive",
                category="风险偏好",
                inference="海外风险资产反弹，A股成长风格关注度提升。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="688256",
        name="寒武纪",
        date="2026-06-30",
        close=260.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=260.0,
        stop_loss=245.0,
        take_profit=285.0,
        position="watch",
        metrics={"sector": "算力", "industry": "AI芯片"},
    )

    assert artifact.cross_market_implications[0].rule_id == "us_risk_on"
    assert (
        artifact.cross_market_overview
        == "外盘风险偏好修复，重点看 A股成长、高弹性、AI链"
    )

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "外盘风险偏好修复"
    assert metrics["cross_market_linkage_basis"] == "风险偏好映射"
    assert metrics["cross_market_first_order_targets"] == (
        "AI链高弹性",
        "算力/芯片",
        "机器人成长",
    )
    assert metrics["cross_market_pressure_targets"] == ("高股息防御",)
    assert metrics["cross_market_validation_signals"][0] == (
        "次日竞价高弹性方向明显强于防御方向"
    )
    assert metrics["cross_market_invalidation_signals"][0] == (
        "美股强但A股竞价无明显风险偏好跟随"
    )

    formatted_pick = PickResult(
        symbol=pick.symbol,
        name=pick.name,
        date=pick.date,
        close=pick.close,
        score=pick.score,
        rating=pick.rating,
        entry_type=pick.entry_type,
        ideal_buy=pick.ideal_buy,
        stop_loss=pick.stop_loss,
        take_profit=pick.take_profit,
        position=pick.position,
        metrics=metrics,
    )
    assert (
        format_pick_market_context_summary(formatted_pick)
        == "重点跟踪｜外盘风险偏好修复｜观察窗 次日-3日"
    )
    assert "先看 AI链高弹性" in format_pick_market_context_chain_summary(formatted_pick)

    pick_lines = market_context_lines_for_pick(pick, artifact)
    assert any(
        line.startswith("先看链条: AI链高弹性、算力/芯片") for line in pick_lines
    )
    assert any(line.startswith("承压方向: 高股息防御") for line in pick_lines)


def test_market_context_does_not_trigger_us_risk_on_on_us_market_name_only() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T11:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="纳斯达克交易所公布新上市规则",
                source="新华社",
                published_at="2026-06-30 10:40:00+08:00",
                impact="positive",
                category="交易所",
                inference="规则更新不代表海外风险偏好变化。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert not any(
        item.rule_id == "us_risk_on" for item in artifact.cross_market_implications
    )


def test_market_context_maps_global_liquidity_easing_to_growth_and_gold() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="美联储鸽派表态，美债收益率下行、美元走弱",
                source="新华社",
                published_at="2026-07-07T09:12:00+08:00",
                impact="positive",
                category="宏观流动性",
                confidence=0.84,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="降息交易升温，成长、AI链和黄金有色获得流动性映射。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    ai_pick = PickResult(
        symbol="688256",
        name="寒武纪",
        date="2026-07-07",
        close=260.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=260.0,
        stop_loss=245.0,
        take_profit=285.0,
        position="watch",
        metrics={"sector": "算力", "industry": "AI芯片"},
    )
    gold_pick = PickResult(
        symbol="600489",
        name="中金黄金",
        date="2026-07-07",
        close=18.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=18.0,
        stop_loss=17.0,
        take_profit=20.0,
        position="watch",
        metrics={"sector": "黄金", "industry": "贵金属"},
    )

    assert artifact.cross_market_implications[0].rule_id == "global_liquidity_easing"
    assert artifact.cross_market_implications[0].strength == "强"
    assert (
        artifact.cross_market_overview
        == "全球流动性宽松交易，优先看 A股成长、AI链、黄金"
    )

    ai_metrics = market_context_metrics_for_pick(ai_pick, artifact)
    assert ai_metrics["cross_market_primary_theme"] == "全球流动性宽松交易"
    assert ai_metrics["cross_market_action"] == "优先复核"
    assert ai_metrics["cross_market_first_order_targets"] == (
        "高弹性成长",
        "AI链/算力",
        "黄金/有色",
    )
    assert ai_metrics["cross_market_pressure_targets"] == ("银行息差", "高股息防御")
    assert ai_metrics["cross_market_validation_signals"][0] == (
        "成长和AI链竞价强于高股息防御"
    )
    assert ai_metrics["cross_market_invalidation_signals"][0] == (
        "降息交易未传导到A股，成长方向竞价弱于防御"
    )

    gold_metrics = market_context_metrics_for_pick(gold_pick, artifact)
    assert gold_metrics["cross_market_primary_theme"] == "全球流动性宽松交易"
    assert "黄金/有色" in gold_metrics["cross_market_first_order_targets"]


def test_market_context_maps_geopolitics_to_gold_defense_energy_pick() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="战争升级推升避险需求",
                source="新华社",
                published_at="2026-06-30 08:20:00+08:00",
                impact="negative",
                category="宏观风险",
                inference="地缘风险抬升，黄金军工能源关注度提升。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="600489",
        name="中金黄金",
        date="2026-06-30",
        close=18.0,
        score=66.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=18.0,
        stop_loss=17.0,
        take_profit=20.0,
        position="watch",
        metrics={"sector": "黄金", "industry": "贵金属"},
    )

    assert artifact.cross_market_implications[0].rule_id == "geopolitics"
    assert (
        artifact.cross_market_overview == "地缘冲突升温，重点看 A股黄金、军工、能源链"
    )

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "地缘冲突升温"
    assert metrics["cross_market_linkage_basis"] == "避险定价映射"
    assert metrics["cross_market_first_order_targets"] == ("黄金", "军工", "油气")
    assert metrics["cross_market_second_order_targets"] == ("航运", "资源品")
    assert metrics["cross_market_pressure_targets"] == (
        "高beta成长",
        "风险偏好题材",
    )
    assert metrics["cross_market_validation_signals"][0] == (
        "黄金军工油气三个方向至少两个同步走强"
    )
    assert metrics["cross_market_invalidation_signals"][0] == (
        "消息很快降温或停火预期回升"
    )

    formatted_pick = PickResult(
        symbol=pick.symbol,
        name=pick.name,
        date=pick.date,
        close=pick.close,
        score=pick.score,
        rating=pick.rating,
        entry_type=pick.entry_type,
        ideal_buy=pick.ideal_buy,
        stop_loss=pick.stop_loss,
        take_profit=pick.take_profit,
        position=pick.position,
        metrics=metrics,
    )
    assert (
        format_pick_market_context_summary(formatted_pick)
        == "重点跟踪｜地缘冲突升温｜观察窗 1-3日"
    )
    assert "承压 高beta成长" in format_pick_market_context_chain_summary(formatted_pick)

    pick_lines = market_context_lines_for_pick(pick, artifact)
    assert any(line.startswith("先看链条: 黄金、军工、油气") for line in pick_lines)
    assert any(line.startswith("扩散链条: 航运、资源品") for line in pick_lines)
    assert any(
        line.startswith("承压方向: 高beta成长、风险偏好题材") for line in pick_lines
    )


def test_market_context_does_not_trigger_geopolitics_without_safe_haven_asset_path() -> (
    None
):
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="战争题材电影上映引发讨论",
                source="新华社",
                published_at="2026-06-30 08:20:00+08:00",
                impact="negative",
                category="娱乐",
                inference="影视内容热度上升，不涉及资产定价。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert artifact.cross_market_implications == ()


def test_market_context_maps_oil_price_shock_to_energy_and_pressure_targets() -> None:
    report = CatalystReport(
        date="2026-07-07",
        generated_at="2026-07-07T09:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="OPEC 减产预期升温，布伦特原油价格大涨",
                source="财联社",
                published_at="2026-07-07T09:10:00+08:00",
                impact="positive",
                category="商品价格",
                confidence=0.82,
                source_quality_label="主流媒体",
                source_quality_score=2,
                inference="原油供应收紧推升能源价格，油气油服和煤化工受益。",
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    oil_pick = PickResult(
        symbol="600938",
        name="中国海油",
        date="2026-07-07",
        close=28.0,
        score=68.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=28.0,
        stop_loss=26.0,
        take_profit=31.0,
        position="watch",
        metrics={"sector": "油气", "industry": "石油开采"},
    )
    airline_pick = PickResult(
        symbol="600029",
        name="南方航空",
        date="2026-07-07",
        close=6.0,
        score=62.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=6.0,
        stop_loss=5.7,
        take_profit=6.6,
        position="watch",
        metrics={"sector": "航空", "industry": "航空运输"},
    )

    assert artifact.cross_market_implications[0].rule_id == "oil_price_shock"
    assert (
        artifact.cross_market_overview == "国际油价冲击，优先看 A股油气、煤化工、航运"
    )

    oil_metrics = market_context_metrics_for_pick(oil_pick, artifact)
    assert oil_metrics["cross_market_primary_theme"] == "国际油价冲击"
    assert oil_metrics["cross_market_first_order_targets"] == (
        "油气开采",
        "油服",
        "煤炭/煤化工",
    )
    assert oil_metrics["cross_market_pressure_targets"] == (
        "航空",
        "下游化工",
        "消费运输",
    )
    assert oil_metrics["cross_market_validation_signals"][0] == (
        "油气和油服同步放量而非单一龙头脉冲"
    )
    assert oil_metrics["cross_market_invalidation_signals"][0] == (
        "油价冲高回落或减产预期被证伪"
    )

    airline_metrics = market_context_metrics_for_pick(airline_pick, artifact)
    assert airline_metrics["cross_market_primary_theme"] == "国际油价冲击"
    assert airline_metrics["cross_market_pressure_targets"][0] == "航空"


def test_market_context_matches_chip_export_controls_for_autonomy_chain() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="美国扩大对华 AI 芯片出口管制范围",
                source="新华社",
                published_at="2026-06-30 09:10:00+08:00",
                impact="negative",
                category="外部冲击",
                inference="半导体设备材料与国产算力自主可控预期升温。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="688012",
        name="中微公司",
        date="2026-06-30",
        close=165.0,
        score=70.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=165.0,
        stop_loss=155.0,
        take_profit=182.0,
        position="watch",
        metrics={"sector": "半导体设备", "industry": "半导体"},
    )

    assert artifact.cross_market_implications[0].rule_id == "chip_export_controls"
    assert (
        artifact.cross_market_overview
        == "海外芯片限制升级，重点看 A股半导体设备、半导体材料、国产算力"
    )

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "海外芯片限制升级"
    assert metrics["cross_market_linkage_basis"] == "供应链重定价"
    assert metrics["cross_market_first_order_targets"] == (
        "半导体设备",
        "半导体材料",
        "国产算力",
    )
    assert metrics["cross_market_pressure_targets"] == ("苹果链", "出口代工")
    assert metrics["cross_market_validation_signals"][0] == (
        "半导体设备材料与国产算力同步放量而非单点脉冲"
    )

    formatted_pick = PickResult(
        symbol=pick.symbol,
        name=pick.name,
        date=pick.date,
        close=pick.close,
        score=pick.score,
        rating=pick.rating,
        entry_type=pick.entry_type,
        ideal_buy=pick.ideal_buy,
        stop_loss=pick.stop_loss,
        take_profit=pick.take_profit,
        position=pick.position,
        metrics=metrics,
    )
    assert (
        format_pick_market_context_summary(formatted_pick)
        == "重点跟踪｜海外芯片限制升级｜观察窗 2-5日"
    )
    assert "承压 苹果链" in format_pick_market_context_chain_summary(formatted_pick)

    pick_lines = market_context_lines_for_pick(pick, artifact)
    assert any(
        line.startswith("传导链: 供应链重定价｜领先窗 隔夜-3日") for line in pick_lines
    )
    assert any(line.startswith("承压方向: 苹果链、出口代工") for line in pick_lines)


def test_market_context_does_not_misfire_chip_export_controls_on_generic_sanctions() -> (
    None
):
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="美国财政部宣布制裁多家航运实体",
                source="新华社",
                published_at="2026-06-30 09:10:00+08:00",
                impact="negative",
                category="外部冲击",
                inference="国际贸易摩擦扰动加大。",
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)

    assert artifact.cross_market_implications == ()
    assert not any(line.startswith("传导推演[") for line in artifact.summary_lines)


def test_market_context_matches_global_supply_tightening_for_upstream_chain() -> None:
    report = CatalystReport(
        date="2026-06-30",
        generated_at="2026-06-30T14:35:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="海外 HBM 报价上调，存储库存继续低位",
                source="财联社",
                published_at="2026-06-30 09:35:00+08:00",
                impact="positive",
                category="涨价/供需催化",
                inference="上游存储与先进封装映射预期升温。",
                source_quality_label="主流媒体",
                source_quality_score=2,
            ),
        ),
        warnings=(),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="002815",
        name="崇达技术",
        date="2026-06-30",
        close=12.4,
        score=68.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=12.4,
        stop_loss=11.7,
        take_profit=13.8,
        position="watch",
        metrics={"sector": "PCB", "industry": "先进封装"},
    )

    assert artifact.cross_market_implications[0].rule_id == "global_supply_tightening"
    assert (
        artifact.cross_market_overview
        == "海外供给收缩映射，重点看 A股存储、半导体材料、先进封装"
    )

    metrics = market_context_metrics_for_pick(pick, artifact)
    assert metrics["cross_market_primary_theme"] == "海外供给收缩映射"
    assert metrics["cross_market_linkage_basis"] == "供需缺口映射"
    assert metrics["cross_market_first_order_targets"] == (
        "存储",
        "半导体材料",
        "先进封装",
    )
    assert metrics["cross_market_second_order_targets"] == ("PCB", "覆铜板", "面板")
    assert metrics["cross_market_pressure_targets"] == ("消费电子代工", "下游整机")

    formatted_pick = PickResult(
        symbol=pick.symbol,
        name=pick.name,
        date=pick.date,
        close=pick.close,
        score=pick.score,
        rating=pick.rating,
        entry_type=pick.entry_type,
        ideal_buy=pick.ideal_buy,
        stop_loss=pick.stop_loss,
        take_profit=pick.take_profit,
        position=pick.position,
        metrics=metrics,
    )
    assert (
        format_pick_market_context_summary(formatted_pick)
        == "重点跟踪｜海外供给收缩映射｜观察窗 2-5日"
    )
    assert "承压 消费电子代工" in format_pick_market_context_chain_summary(
        formatted_pick
    )


def test_market_context_keeps_domestic_sector_news_without_stock_code() -> None:
    report = CatalystReport(
        date="2026-07-14",
        generated_at="2026-07-14T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="国常会部署设备更新，推动工业母机和机器人",
                source="新华社",
                source_region="domestic",
                published_at="2026-07-14T09:20:00+08:00",
                impact="positive",
                category="政策催化",
                confidence=0.8,
                source_quality_score=3,
                source_quality_label="多源/权威媒体",
                inference="设备更新和机器人政策预期升温。",
            ),
        ),
    )

    artifact = build_market_context_artifact(catalyst_report=report)

    assert artifact.cross_market_implications[0].rule_id == "domestic_policy_stimulus"
    assert any(line.startswith("国内雷达:") for line in artifact.summary_lines)
    assert not any(line.startswith("海外风险:") for line in artifact.summary_lines)


def test_market_context_exposes_direction_and_source_fetch_provenance() -> None:
    report = CatalystReport(
        date="2026-07-14",
        generated_at="2026-07-14T10:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="OPEC 减产预期升温，布伦特原油价格大涨",
                source="Reuters",
                source_region="international",
                published_at="2026-07-14T09:30:00+08:00",
                source_fetched_at="2026-07-14T09:35:00+08:00",
                url="https://example.com/oil",
                impact="positive",
                category="商品价格",
                confidence=0.8,
                source_quality_score=2,
                source_quality_label="主流媒体",
                inference="能源价格预期抬升。",
            ),
        ),
    )

    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="600938",
        name="中国海油",
        date="2026-07-14",
        close=28.0,
        score=68.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=28.0,
        stop_loss=26.0,
        take_profit=31.0,
        position="watch",
        metrics={"sector": "油气", "industry": "石油开采"},
    )
    metrics = market_context_metrics_for_pick(pick, artifact)

    implication = artifact.cross_market_implications[0]
    assert implication.impact_direction == "positive"
    assert implication.source_url == "https://example.com/oil"
    assert implication.source_fetched_at == "2026-07-14T09:35:00+08:00"
    assert metrics["cross_market_impact_direction"] == "positive"
    assert metrics["cross_market_source_url"] == "https://example.com/oil"
    assert metrics["cross_market_source_fetched_at"] == ("2026-07-14T09:35:00+08:00")


def test_market_context_matches_uppercase_pick_sector_case_insensitively() -> None:
    report = CatalystReport(
        date="2026-07-14",
        generated_at="2026-07-14T10:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="海外 HBM 报价上调，存储库存继续低位",
                source="Reuters",
                source_region="international",
                published_at="2026-07-14T09:30:00+08:00",
                impact="positive",
                category="涨价/供需催化",
                confidence=0.8,
                source_quality_score=2,
                source_quality_label="主流媒体",
                inference="上游存储与先进封装映射预期升温。",
            ),
        ),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    pick = PickResult(
        symbol="002815",
        name="样本PCB",
        date="2026-07-14",
        close=12.4,
        score=68.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=12.4,
        stop_loss=11.7,
        take_profit=13.8,
        position="watch",
        metrics={"sector": "PCB"},
    )

    assert (
        market_context_metrics_for_pick(pick, artifact)["cross_market_primary_theme"]
        == "海外供给收缩映射"
    )
