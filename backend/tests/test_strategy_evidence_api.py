from __future__ import annotations

from datetime import date
import json

import app as app_module
from fastapi.testclient import TestClient

from aqsp.research.strategy_evidence import (
    build_strategy_oos_evidence,
    save_strategy_oos_evidence,
)
from aqsp.walkforward_gate import build_walkforward_gate_payload


def _evidence():
    assumptions = {
        "uses_raw_prices": True,
        "uses_point_in_time_data": True,
        "train_test_separated": True,
        "has_purge_window": True,
        "includes_transaction_costs": True,
        "includes_slippage": True,
        "excludes_not_executable": True,
        "cost_model": "test fee and slippage",
        "price_mode": "raw",
    }
    gate = build_walkforward_gate_payload(
        dsr=1.7,
        pbo=0.2,
        run_date="2026-06-10",
        start="2023-01-01",
        end="2026-06-09",
        n_periods=12,
        n_variants=8,
        thresholds_version="1.1.21",
        metadata={"backtest_assumptions": assumptions},
    )
    return build_strategy_oos_evidence(
        strategy_id="composite",
        strategy_version="1.1.21",
        parameters={"weight": 0.3},
        requested_data_source="sqlite_db",
        actual_data_source="sqlite_db",
        price_mode="raw",
        data_start="2023-01-01",
        data_end="2026-06-09",
        effective_symbols=4200,
        metrics={"sharpe_ratio": 1.1},
        oos_windows=[{"period": "2025-01-01/2025-06-30", "total_return": 0.04}],
        assumptions=assumptions,
        gate_payload=gate,
        today=date(2026, 6, 14),
    )


def test_strategy_evidence_api_lists_verified_artifacts_without_promotion(monkeypatch, tmp_path):
    monkeypatch.setattr("aqsp.core.runtime.runtime_data_root", lambda: tmp_path)
    save_strategy_oos_evidence(_evidence(), root=tmp_path)

    response = TestClient(app_module.app).get(
        "/api/aqsp/strategy-evidence?strategy_id=composite"
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["count"] == 1
    assert payload["promotion_enabled"] is False
    assert payload["items"][0]["strategy_id"] == "composite"
    assert payload["items"][0]["human_review_required"] is True


def test_strategy_evidence_api_rejects_tampered_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr("aqsp.core.runtime.runtime_data_root", lambda: tmp_path)
    path = save_strategy_oos_evidence(_evidence(), root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["thresholds_version"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    response = TestClient(app_module.app).get("/api/aqsp/strategy-evidence")

    assert response.status_code == 503
    assert "已停止读取" in response.json()["detail"]
