#!/usr/bin/env python3
"""Smoke-check market intelligence scoring boundaries after runtime deploy."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.core.types import PickResult
from aqsp.market_context import (
    build_market_context_artifact,
    cross_market_rule_runtime_summary,
    market_context_metrics_for_pick,
)
from aqsp.news.catalysts import CatalystEvent, CatalystReport
from aqsp.portfolio.manager import apply_portfolio_manager
from aqsp.data.news_source import rss_news_runtime_summary


def _pick(
    symbol: str,
    score: float,
    *,
    metrics: dict[str, object] | None = None,
) -> PickResult:
    return PickResult(
        symbol=symbol,
        name=symbol,
        date="2026-07-08",
        close=10.0,
        score=score,
        rating="buy_candidate",
        entry_type="close",
        ideal_buy=10.0,
        stop_loss=9.5,
        take_profit=11.0,
        position="10%-30%",
        metrics=metrics or {},
    )


def _direct_news_metrics() -> dict[str, object]:
    report = CatalystReport(
        date="2026-07-08",
        generated_at="2026-07-08T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="宁德时代中标储能大单",
                source="公告",
                published_at="2026-07-08T09:20:00+08:00",
                symbol="300750",
                name="宁德时代",
                impact="positive",
                category="订单/需求验证",
                confidence=0.82,
                source_quality_label="高价值来源",
                source_quality_score=4,
                inference="个股消息催化明确，但只进入上下文复核。",
            ),
        ),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    return market_context_metrics_for_pick(
        _pick("300750", 70.0, metrics={"industry": "电池"}),
        artifact,
    )


def _structured_cross_market_metrics() -> dict[str, object]:
    report = CatalystReport(
        date="2026-07-08",
        generated_at="2026-07-08T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="英伟达发布 Physical AI 平台，机器人链关注升温",
                source="新华社",
                published_at="2026-07-08T09:15:00+08:00",
                impact="positive",
                category="科技催化",
                confidence=0.86,
                source_count=2,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="海外物理AI叙事升温，机器人和AI算力链映射增强。",
            ),
        ),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    return market_context_metrics_for_pick(
        _pick("688981", 66.0, metrics={"industry": "机器人 AI算力"}),
        artifact,
    )


def _geopolitics_metrics() -> dict[str, object]:
    report = CatalystReport(
        date="2026-07-08",
        generated_at="2026-07-08T09:40:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="中东冲突升级，避险需求推升黄金军工能源链",
                source="新华社",
                published_at="2026-07-08T09:10:00+08:00",
                impact="negative",
                category="宏观风险",
                confidence=0.84,
                source_quality_label="多源/权威媒体",
                source_quality_score=3,
                inference="地缘风险升温，黄金、军工和能源链进入短线复核。",
            ),
        ),
    )
    artifact = build_market_context_artifact(catalyst_report=report)
    return market_context_metrics_for_pick(
        _pick("600489", 64.0, metrics={"sector": "黄金", "industry": "贵金属"}),
        artifact,
    )


def run_smoke_checks() -> dict[str, Any]:
    direct_metrics = _direct_news_metrics()
    direct_bundle = apply_portfolio_manager(
        [_pick("300750", 70.0, metrics=direct_metrics)]
    )
    structured_metrics = _structured_cross_market_metrics()
    structured_bundle = apply_portfolio_manager(
        [_pick("688981", 66.0, metrics=structured_metrics)]
    )
    geopolitics_metrics = _geopolitics_metrics()
    runtime_summary = cross_market_rule_runtime_summary()
    rss_summary = rss_news_runtime_summary()

    direct_decision = direct_bundle.decisions[0]
    structured_decision = structured_bundle.decisions[0]
    checks = {
        "direct_news_context_only": (
            direct_metrics.get("cross_market_score_adjustment_allowed") is False
            and direct_decision.score_delta == 0.0
            and direct_bundle.picks[0].score == 70.0
        ),
        "structured_cross_market_context_only": (
            structured_metrics.get("cross_market_score_adjustment_allowed") is True
            and bool(structured_metrics.get("cross_market_rule_ids"))
            and structured_decision.score_delta == 0.0
            and structured_decision.priority_delta == 0.0
            and structured_bundle.picks[0].score == 66.0
            and structured_bundle.picks[0].metrics.get("context_priority_score") == 66.0
        ),
        "geopolitics_maps_to_gold_defense_energy": (
            geopolitics_metrics.get("cross_market_primary_theme") == "地缘冲突升温"
            and geopolitics_metrics.get("cross_market_rule_ids") == ("geopolitics",)
            and "黄金"
            in geopolitics_metrics.get("cross_market_first_order_targets", ())
            and "军工"
            in geopolitics_metrics.get("cross_market_first_order_targets", ())
            and "油气"
            in geopolitics_metrics.get("cross_market_first_order_targets", ())
        ),
        "runtime_rules_visible": (
            runtime_summary.global_enabled
            and "commercial_space" in runtime_summary.core_rule_ids
            and "physical_ai" in runtime_summary.core_rule_ids
            and "geopolitics" in runtime_summary.core_rule_ids
        ),
        "rss_core_triggers_covered": rss_summary.all_core_triggers_covered,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "direct_news": {
            "theme": direct_metrics.get("cross_market_primary_theme", ""),
            "score_adjustment_allowed": direct_metrics.get(
                "cross_market_score_adjustment_allowed"
            ),
            "score_delta": direct_decision.score_delta,
            "final_score": direct_bundle.picks[0].score,
            "action": direct_decision.action,
        },
        "structured_cross_market": {
            "theme": structured_metrics.get("cross_market_primary_theme", ""),
            "rule_ids": structured_metrics.get("cross_market_rule_ids", ()),
            "score_adjustment_allowed": structured_metrics.get(
                "cross_market_score_adjustment_allowed"
            ),
            "score_delta": structured_decision.score_delta,
            "priority_delta": structured_decision.priority_delta,
            "final_score": structured_bundle.picks[0].score,
            "context_priority_score": structured_bundle.picks[0].metrics.get(
                "context_priority_score"
            ),
            "action": structured_decision.action,
        },
        "geopolitics": {
            "theme": geopolitics_metrics.get("cross_market_primary_theme", ""),
            "rule_ids": geopolitics_metrics.get("cross_market_rule_ids", ()),
            "first_order_targets": geopolitics_metrics.get(
                "cross_market_first_order_targets", ()
            ),
            "validation": geopolitics_metrics.get(
                "cross_market_validation_signals", ()
            ),
            "invalidation": geopolitics_metrics.get(
                "cross_market_invalidation_signals", ()
            ),
        },
        "runtime_rules": {
            "rule_count": runtime_summary.rule_count,
            "core_rule_ids": runtime_summary.core_rule_ids,
            "boundary": runtime_summary.advisory_boundary,
        },
        "rss_sources": {
            "enabled": rss_summary.enabled,
            "feed_count": rss_summary.feed_count,
            "covered_triggers": rss_summary.covered_triggers,
            "missing_triggers": rss_summary.missing_triggers,
            "keyword_gated_feeds": rss_summary.keyword_gated_feeds,
        },
    }


def main() -> int:
    result = run_smoke_checks()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
