import json

import pytest

from scripts.check_variant_results import validate_variant_results


def _evidence(symbol: str, end_date: str) -> dict[str, object]:
    return {
        "date": end_date,
        "signal_date": "2026-07-23",
        "execution_date": end_date,
        "symbol": symbol,
        "macd_hist": 0.12,
        "kdj_j": 55.0,
        "volume_ratio": 1.35,
        "atr_pct": 2.4,
    }


def _variant(index: int, end_date: str = "2026-07-24") -> dict[str, object]:
    previous = "2026-07-23"
    symbol = f"{index:06d}"
    evidence = _evidence(symbol, end_date)
    return {
        "variant_id": f"variant_{index}",
        "strategy_signature": f"mode_{index}|lb=20",
        "holdings_signature": f"{symbol}:100",
        "holdings_date": end_date,
        "previous_holdings_date": previous,
        "holdings": [
            {
                "symbol": symbol,
                "name": f"样本{index}",
                "quantity": 100,
                "entry_evidence": evidence,
            }
        ],
        "previous_holdings": [],
        "recent_actions": [
            {
                "date": end_date,
                "symbol": symbol,
                "side": "buy",
                "reason": "MACD确认",
                "evidence": evidence,
            }
        ],
        "adjustments": [f"买入 {symbol}：技术面触发。"],
        "technical_evidence": [evidence],
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


def test_check_variant_results_rejects_top_duplicate_holdings(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    for index in range(10):
        variants[index]["holdings_signature"] = "000001:100"
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top holding signatures too repetitive"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_repeated_holdings_outside_top_window(
    tmp_path,
) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    for index in range(20, 100):
        variants[index]["holdings_signature"] = "000001:100"
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique holding signatures below minimum"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_materially_repeated_holdings(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    for index in range(60, 100):
        variants[index]["holdings_signature"] = "000001:100"
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique holding signatures below minimum"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_missing_technical_evidence(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    variants[0]["technical_evidence"] = []
    variants[0]["recent_actions"] = []
    variants[0]["holdings"] = [{"symbol": "000000", "name": "样本", "quantity": 100}]
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="technical evidence missing"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_current_holding_without_same_day_evidence(
    tmp_path,
) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    variants[0]["technical_evidence"][0]["execution_date"] = "2026-07-23"
    variants[0]["technical_evidence"][0]["date"] = "2026-07-23"
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="current holding technical evidence missing"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_holding_without_name(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    variants[0]["holdings"][0].pop("name")
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="holding name missing"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_change_without_named_stock_explanation(
    tmp_path,
) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    variants[0]["adjustments"] = ["今日技术面触发，调整纸面仓位。"]
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="holding change explanation incomplete"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_empty_technical_metric_values(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    for key in ("macd_hist", "kdj_j", "volume_ratio", "atr_pct"):
        variants[0]["technical_evidence"][0][key] = None
        variants[0]["recent_actions"][0]["evidence"][key] = None
        variants[0]["holdings"][0]["entry_evidence"][key] = None
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="technical evidence missing"):
        validate_variant_results(path, expected_end="2026-07-24")


def test_check_variant_results_rejects_future_technical_evidence(tmp_path) -> None:
    path = tmp_path / "variant_results.json"
    variants = [_variant(index) for index in range(100)]
    evidence = variants[0]["technical_evidence"][0]
    evidence["date"] = "2026-07-25"
    evidence["execution_date"] = "2026-07-25"
    variants[0]["recent_actions"][0]["evidence"] = evidence
    variants[0]["holdings"][0]["entry_evidence"] = evidence
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v2",
                "end_date": "2026-07-24",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 600, "supported_symbols": 4920},
                "variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="technical evidence missing"):
        validate_variant_results(path, expected_end="2026-07-24")
