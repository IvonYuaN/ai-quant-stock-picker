from __future__ import annotations

from datetime import date

from aqsp.walkforward_gate import (
    MIN_PRODUCTION_GATE_SYMBOLS,
    build_walkforward_gate_payload,
    validate_walkforward_gate_payload,
    validate_walkforward_market_coverage,
)


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload = build_walkforward_gate_payload(
        dsr=1.9,
        pbo=0.24,
        run_date="2026-06-10",
        start="2023-01-01",
        end="2024-12-31",
        n_periods=12,
    )
    payload.update(overrides)
    return payload


def test_walkforward_gate_validates_pass_when_payload_is_strict_and_clean() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(),
        today=date(2026, 6, 14),
        heldout_cutoff=date(2024, 12, 31),
    )

    assert result.ok is True
    assert result.blockers == ()


def test_walkforward_gate_rejects_string_booleans_and_string_metrics() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(
            deflated_sharpe="1.9",
            pbo="0.24",
            pbo_valid="true",
            dsr_pass="true",
            pbo_pass="true",
            both_pass="true",
        ),
        today=date(2026, 6, 14),
    )

    assert result.ok is False
    assert "deflated_sharpe missing/invalid" in result.blockers
    assert "pbo missing/invalid" in result.blockers
    assert "both_pass flag missing/invalid/false" in result.blockers


def test_walkforward_gate_rejects_bool_nan_and_non_int_periods() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(deflated_sharpe=True, pbo=float("nan"), n_periods=True),
        today=date(2026, 6, 14),
    )

    assert result.ok is False
    assert "deflated_sharpe missing/invalid" in result.blockers
    assert "pbo missing/invalid" in result.blockers
    assert "n_periods missing/invalid" in result.blockers


def test_walkforward_gate_recomputes_metric_thresholds_instead_of_trusting_flags() -> (
    None
):
    result = validate_walkforward_gate_payload(
        _valid_payload(deflated_sharpe=0.8, pbo=0.0),
        today=date(2026, 6, 14),
    )

    assert result.ok is False
    assert "DSR=0.8000 <= 1.0" in result.blockers
    assert "PBO=0.00% outside (0%, 50%)" in result.blockers


def test_walkforward_gate_rejects_stale_and_heldout_payloads() -> None:
    result = validate_walkforward_gate_payload(
        _valid_payload(run_date="2026-01-01", data_end="2026-04-30"),
        today=date(2026, 6, 14),
        heldout_cutoff=date(2024, 12, 31),
    )

    assert result.ok is False
    assert "gate stale: 164 days > 35" in result.blockers
    assert "data_end=2026-04-30 > heldout_cutoff=2024-12-31" in result.blockers


def test_walkforward_market_coverage_passes_full_market_gate() -> None:
    result = validate_walkforward_market_coverage(
        _valid_payload(effective_symbols=MIN_PRODUCTION_GATE_SYMBOLS)
    )

    assert result.ok is True
    assert result.effective_symbols == MIN_PRODUCTION_GATE_SYMBOLS
    assert result.blockers == ()


def test_walkforward_market_coverage_rejects_missing_and_bool_counts() -> None:
    missing = validate_walkforward_market_coverage(_valid_payload())
    boolean = validate_walkforward_market_coverage(
        _valid_payload(effective_symbols=True)
    )

    assert missing.ok is False
    assert missing.effective_symbols is None
    assert "effective_symbols missing/invalid" in missing.blockers
    assert boolean.ok is False
    assert boolean.effective_symbols is None
    assert "effective_symbols missing/invalid" in boolean.blockers


def test_walkforward_market_coverage_rejects_smoke_sample_counts() -> None:
    result = validate_walkforward_market_coverage(_valid_payload(effective_symbols=300))

    assert result.ok is False
    assert result.effective_symbols == 300
    assert f"effective_symbols=300 < {MIN_PRODUCTION_GATE_SYMBOLS}" in result.blockers
