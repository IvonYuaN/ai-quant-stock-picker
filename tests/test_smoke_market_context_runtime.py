from __future__ import annotations

from scripts.smoke_market_context_runtime import run_smoke_checks


def test_smoke_market_context_runtime_guards_score_adjustment_boundary() -> None:
    result = run_smoke_checks()

    assert result["ok"] is True
    assert result["checks"] == {
        "direct_news_context_only": True,
        "structured_cross_market_context_only": True,
        "geopolitics_maps_to_gold_defense_energy": True,
        "runtime_rules_visible": True,
        "rss_core_triggers_covered": True,
    }
    assert result["direct_news"]["score_delta"] == 0.0
    assert result["direct_news"]["final_score"] == 70.0
    assert result["direct_news"]["score_adjustment_allowed"] is False
    assert result["structured_cross_market"]["score_delta"] == 0.0
    assert result["structured_cross_market"]["priority_delta"] == 0.0
    assert result["structured_cross_market"]["final_score"] == 66.0
    assert result["structured_cross_market"]["context_priority_score"] == 66.0
    assert result["structured_cross_market"]["score_adjustment_allowed"] is True
    assert result["geopolitics"]["rule_ids"] == ("geopolitics",)
    assert result["geopolitics"]["first_order_targets"] == ("黄金", "军工", "油气")
    assert "commercial_space" in result["runtime_rules"]["core_rule_ids"]
    assert "physical_ai" in result["runtime_rules"]["core_rule_ids"]
    assert "geopolitics" in result["runtime_rules"]["core_rule_ids"]
    assert result["rss_sources"]["missing_triggers"] == ()
    assert (
        result["rss_sources"]["keyword_gated_feeds"]
        == result["rss_sources"]["feed_count"]
    )
