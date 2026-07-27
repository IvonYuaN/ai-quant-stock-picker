import json

import pytest

from scripts.check_variant_results import validate_variant_results


def _variant(index: int, end_date: str = "2026-07-24") -> dict[str, object]:
    previous = "2026-07-23"
    symbol = f"{index:06d}"
    return {
        "variant_id": f"variant_{index}",
        "strategy_signature": f"mode_{index}|lb=20",
        "holdings_signature": f"{symbol}:100",
        "holdings_date": end_date,
        "previous_holdings_date": previous,
        "holdings": [{"symbol": symbol, "quantity": 100}],
        "previous_holdings": [],
        "recent_actions": [
            {"date": end_date, "symbol": symbol, "side": "buy", "reason": "MACD确认"}
        ],
        "adjustments": [f"买入 {symbol}：技术面触发。"],
    }


def test_check_variant_results_accepts_v2_diversified_payload(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": [_variant(index) for index in range(100)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = validate_variant_results(path, expected_end="2026-07-24")

    assert result.schema_version == "variant-suite-v2"
    assert result.variants == 100
    assert result.selected_symbols == 600


def test_check_variant_results_rejects_old_or_tiny_payload(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v1",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 120, "supported_symbols": 4920},
                "variants": [_variant(1)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema_version"):
        validate_variant_results(path)
