from __future__ import annotations

import pytest
import pandas as pd

import signal_definitions
import tenant


@pytest.fixture
def isolated_signal_store(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_definitions.pf, "CACHE_DIR", str(tmp_path))
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _save(*, signal_id: str = "close_above_previous", expected_revision: int | None = None):
    return signal_definitions.save_signal(
        signal_id=signal_id,
        name="收盘高于昨收",
        kind="entry",
        enabled=True,
        conditions=[
            {
                "left": "close",
                "op": ">",
                "right": "field:prev_close",
                "left_days": 0,
                "right_days": 0,
            }
        ],
        expected_revision=expected_revision,
    )


def test_signal_definitions_store_create_update_and_delete_with_revision(isolated_signal_store):
    created = _save()
    assert created["revision"] == 1
    assert signal_definitions.list_signals() == [created]

    updated = signal_definitions.save_signal(
        signal_id=created["id"],
        name="收盘低于昨收",
        kind="exit",
        enabled=False,
        conditions=[{"left": "close", "op": "<", "right": "field:prev_close"}],
        expected_revision=1,
    )
    assert updated["revision"] == 2
    assert updated["enabled"] is False
    with pytest.raises(signal_definitions.SignalDefinitionConflict):
        _save(expected_revision=1)

    assert signal_definitions.delete_signal(created["id"], expected_revision=2)
    assert signal_definitions.list_signals() == []


def test_signal_definitions_are_tenant_scoped(isolated_signal_store):
    created = _save()
    token = tenant.current_tenant.set("another-user")
    try:
        assert signal_definitions.list_signals() == []
        with pytest.raises(signal_definitions.SignalDefinitionConflict):
            signal_definitions.save_signal(
                signal_id=created["id"],
                name="异租户不可覆盖",
                kind="entry",
                enabled=True,
                conditions=[{"left": "close", "op": ">", "right": 1}],
                expected_revision=1,
            )
    finally:
        tenant.current_tenant.reset(token)


@pytest.mark.parametrize(
    ("field", "operator", "right", "left_days"),
    [
        ("future_return", ">", 0, 0),
        ("close", "contains", "x", 0),
        ("close", ">", "field:future_return", 0),
        ("close", ">", 0, 61),
        ("raw_close", ">", "field:close", 1),
    ],
)
def test_signal_definitions_reject_unsupported_conditions_when_saved(
    isolated_signal_store, field, operator, right, left_days
):
    with pytest.raises(ValueError):
        signal_definitions.save_signal(
            signal_id="invalid_signal",
            name="无效定义",
            kind="both",
            enabled=True,
            conditions=[
                {"left": field, "op": operator, "right": right, "left_days": left_days}
            ],
            expected_revision=None,
        )


def test_signal_definitions_options_disclose_consumer_gap(isolated_signal_store):
    result = signal_definitions.options()
    assert result["fields"] == ["amount", "close", "high", "low", "open", "prev_close", "raw_close", "volume"]
    assert result["max_conditions"] == 8
    assert result["consumer_status"] == "monitor_snapshot_and_historical_scan"


def test_signal_definitions_evaluate_current_raw_conditions_and_fail_closed_on_missing_values(
    isolated_signal_store,
):
    signal = {
        "conditions": [
            {"left": "close", "op": ">", "right": "field:open"},
            {"left": "volume", "op": "!=", "right": 0},
        ]
    }
    frame = pd.DataFrame(
        {
            "date": ["2026-10-28", "2026-10-29", "2026-10-30"],
            "open": [10.0, 11.0, 10.0],
            "close": [11.0, 10.0, 12.0],
            "volume": [100.0, None, 100.0],
        }
    )

    result = signal_definitions.evaluate_signal_series(signal, frame)

    assert result.tolist() == [True, False, True]
    assert "adj_factor" not in frame


def test_signal_definitions_align_prior_price_operands_to_trigger_date_pit_basis(
    isolated_signal_store,
):
    signal = {
        "conditions": [
            {"left": "close", "op": ">", "right": "field:close", "right_days": 1}
        ]
    }
    frame = pd.DataFrame(
        {
            "date": ["2026-10-29", "2026-10-30"],
            "close": [100.0, 50.0],
            "adj_factor": [1.0, 0.5],
        }
    )

    result = signal_definitions.evaluate_signal_series(signal, frame)

    assert result.tolist() == [False, False]
    assert signal_definitions.needs_pit_factors(signal)


def test_signal_definitions_require_pit_basis_for_previous_close_reference(
    isolated_signal_store,
):
    signal = {"conditions": [{"left": "close", "op": ">", "right": "field:prev_close"}]}
    frame = pd.DataFrame(
        {
            "date": ["2026-10-29", "2026-10-30"],
            "close": [100.0, 50.0],
            "adj_factor": [1.0, 0.5],
        }
    )

    result = signal_definitions.evaluate_signal_series(signal, frame)

    assert result.tolist() == [False, False]
    assert signal_definitions.needs_pit_factors(signal)
