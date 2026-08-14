from scripts.refresh_variant_results_from_market_db import (
    MarketSymbol,
    attach_discussion_links,
    evolution_profiles,
    prioritize_focus_symbols,
)
from scripts.run_variant_suite import VariantProfile, assign_variant_lifecycle


def _profile() -> VariantProfile:
    return VariantProfile(
        variant_id="trend_base",
        label="趋势基线",
        lookback=20,
        entry_return_pct=1.5,
        max_bias_pct=10.0,
        mode="trend",
        max_positions=3,
        position_weight=1 / 3,
        hypothesis="趋势延续",
    )


def test_variant_lifecycle_eliminates_negative_result_after_minimum_signal_days() -> None:
    results = [
        {
            "variant_id": "loser",
            "rank": 5,
            "return_pct": -3.0,
            "independent_signal_days": 35,
            "strategy": {
                "entry_return_pct": 1.0,
                "max_bias_pct": 10.0,
                "max_positions": 4,
            },
        }
    ]

    assign_variant_lifecycle(results)

    assert results[0]["lifecycle_status"] == "淘汰"
    assert results[0]["next_generation"]["max_positions"] == 3


def test_variant_evolution_replaces_eliminated_parent_in_next_generation() -> None:
    previous = {
        "variants": [
            {
                "variant_id": "trend_base",
                "label": "趋势基线",
                "generation": 1,
                "lifecycle_status": "淘汰",
                "strategy": {
                    "id": "trend_base",
                    "lookback_days": 20,
                    "entry_return_pct": 1.5,
                    "max_bias_pct": 10.0,
                    "mode": "trend",
                    "max_positions": 3,
                    "hypothesis": "趋势延续",
                },
                "next_generation": {
                    "generation": 2,
                    "entry_return_pct": 2.0,
                    "max_bias_pct": 9.0,
                    "max_positions": 2,
                },
            }
        ]
    }

    evolved = evolution_profiles((_profile(),), previous)

    assert evolved[0].variant_id == "trend_base__g2"
    assert evolved[0].parent_variant_id == "trend_base"
    assert evolved[0].max_bias_pct == 9.0


def test_variant_batch_forces_discussed_candidates_into_bounded_pool() -> None:
    eligible = tuple(
        MarketSymbol(f"{index:06d}.SZ", f"{index:06d}", str(index), "深市主板")
        for index in range(5)
    )
    batch = eligible[:3]

    selected = prioritize_focus_symbols(
        batch,
        eligible,
        ({"symbol": "000004", "strategies": ["ma_pullback"]},),
    )

    assert [item.symbol for item in selected] == ["000004", "000000", "000001"]


def test_variant_discussion_links_only_matching_strategy_modes() -> None:
    payload: dict[str, object] = {
        "variants": [
            {"strategy": {"mode": "pullback"}},
            {"strategy": {"mode": "volume_breakout"}},
        ]
    }
    cohort = (
        {
            "symbol": "000001",
            "display_name": "000001 平安银行",
            "strategies": ["ma_pullback"],
            "risk_gate": "跌破防守位失效",
        },
    )

    attach_discussion_links(payload, cohort)

    variants = payload["variants"]
    assert isinstance(variants, list)
    assert variants[0]["discussion_links"][0]["symbol"] == "000001"
    assert variants[1]["discussion_links"] == []
