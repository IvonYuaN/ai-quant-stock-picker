from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import decision_history
import tenant


@dataclass(frozen=True)
class ReviewStub:
    id: str
    signal_id: str
    symbol: str
    rating: int
    notes: str
    created_at: str
    updated_at: str


def _candidate_rows() -> tuple[list[dict], list[dict]]:
    return ([{
        "id": "obs-1", "symbol": "600519", "name": "贵州茅台", "state": "stopped",
        "updated_at": "2026-10-31T10:00:00+08:00", "created_at": "2026-10-30T10:00:00+08:00",
        "source_snapshot_id": "snap-1", "condition": "站上观察位", "invalidation": "跌回区间",
        "revision": 2,
    }], [{
        "id": "decision-1", "symbol": "600519", "name": "贵州茅台", "choice": "defer",
        "updated_at": "2026-10-30T09:00:00+08:00", "created_at": "2026-10-30T09:00:00+08:00",
        "source_snapshot_id": "snap-1", "note": "等量能", "revision": 1,
        "personal_context": {"personal_intent": "验证承接"},
    }])


@pytest.fixture
def projection(monkeypatch, tmp_path: Path):
    observations, decisions = _candidate_rows()
    monkeypatch.setattr("candidate_observations.list_observations", lambda: observations)
    monkeypatch.setattr("candidate_observations.list_decisions", lambda: decisions)
    monkeypatch.setattr(decision_history.pf, "CACHE_DIR", str(tmp_path))
    portfolio_file = tmp_path / "portfolio.json"
    portfolio_file.write_text(json.dumps({
        "holdings": [{"position_id": "pos-1", "code": "000001", "name": "平安银行"}],
        "trade_receipts": [{
            "receipt_id": "fill-1", "tenant": "local", "position_id": "pos-1",
            "code": "000001", "direction": "buy", "date": "2026-10-29",
            "price": 11.25, "shares": 200, "actual_fees": None,
            "recorded_at": "2026-10-29T10:30:00+08:00",
        }, {
            "receipt_id": "foreign-fill", "tenant": "u_else", "position_id": "pos-foreign",
            "code": "000001", "direction": "sell", "date": "2026-10-29",
            "price": 12.0, "shares": 10, "actual_fees": 0,
            "recorded_at": "2026-10-29T11:30:00+08:00",
        }],
        "position_reviews": {
            "pos-1": {"version": 3, "updated_at": "2026-10-29T10:00:00+08:00", "symbol": "000001",
                       "review_plan": {"choice": "hold", "updated_at": "2026-10-29T09:00:00+08:00"}},
            "pos-old": {"version": 4, "updated_at": "2026-10-28T10:00:00+08:00", "symbol": "300750",
                         "review_plan": {"choice": "reduce"}},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(decision_history.pf, "_load", lambda: (_ for _ in ()).throw(AssertionError("must stay read-only")))
    monkeypatch.setattr("aqsp.review.get_reviews", lambda: [ReviewStub(
        id="review-1", signal_id="sig-1", symbol="600519", rating=4, notes="证据完整",
        created_at="2026-10-27T10:00:00+08:00", updated_at="2026-10-27T10:00:00+08:00",
    )])
    monkeypatch.setattr("performance_bridge.signals_payload", lambda limit=1000: {"signals": [{
        "id": "sig-1", "symbol": "600519", "name": "贵州茅台", "status": "validated",
    }]})
    token = tenant.current_tenant.set("local")
    yield
    tenant.current_tenant.reset(token)


def test_decision_history_keeps_domain_ids_and_archived_positions(projection):
    result = decision_history.build_history()
    assert result["total"] == 6
    assert {row["kind"] for row in result["items"]} == {"candidate", "position", "signal", "trade"}
    position = next(row for row in result["items"] if row["id"] == "pos-old")
    assert position["status"] == "archived"
    assert position["href"] == "/my/holdings/pos-old/plan"
    signal = next(row for row in result["items"] if row["kind"] == "signal")
    assert signal["source_snapshot_id"] == "sig-1"
    trade = next(row for row in result["items"] if row["kind"] == "trade")
    assert trade["id"] == trade["source_snapshot_id"] == "fill-1"
    assert trade["summary"] == "买入 200 股 @ 11.25 元"
    assert "费用待核对" in trade["detail"]
    assert trade["href"] == "/my/holdings/pos-1/plan"


def test_decision_history_trade_filter_reads_only_current_tenant_receipts(projection):
    result = decision_history.build_history(kind="trade", symbol="000001", status="buy")
    assert result["total"] == 1
    assert result["sources"]["trade_receipts"] == {
        "available": True, "error": "", "count": 1
    }

    token = tenant.current_tenant.set("u_other")
    try:
        other = decision_history.build_history(kind="trade")
    finally:
        tenant.current_tenant.reset(token)
    assert other["tenant_scope"] == "u_other"
    assert other["items"] == []
    assert other["sources"]["trade_receipts"]["count"] == 0


def test_decision_history_trade_source_failure_keeps_other_sources(projection, tmp_path: Path):
    portfolio_file = tmp_path / "portfolio.json"
    portfolio_file.write_text("{", encoding="utf-8")

    result = decision_history.build_history()

    assert result["sources"]["trade_receipts"]["available"] is False
    assert result["sources"]["candidate"]["available"] is True
    assert result["sources"]["formal_signal_reviews"]["available"] is True


def test_decision_history_endpoint_exposes_trade_receipt_projection(projection):
    from fastapi.testclient import TestClient
    import app as app_module

    response = TestClient(app_module.app).get("/api/aqsp/decision-history?kind=trade")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["sources"]["trade_receipts"] == {
        "available": True, "error": "", "count": 1
    }
    assert payload["items"][0]["id"] == "fill-1"
    assert payload["items"][0]["kind"] == "trade"


def test_decision_history_filters_without_changing_source_records(projection):
    result = decision_history.build_history(kind="candidate", symbol="600519", status="defer", limit=1)
    assert result["total"] == 1
    assert result["items"][0]["subkind"] == "decision"
    assert decision_history.build_history(since="2026-10-31")["total"] == 1


def test_decision_history_includes_tenant_variant_decisions(projection, monkeypatch):
    monkeypatch.setattr("research_variant_decisions.list_events", lambda: [{
        "id": "variant-review-1",
        "artifact_id": "a" * 64,
        "batch_id": "batch-1",
        "variant_id": "low_vol_v2",
        "choice": "continue_validation",
        "note": "先补独立样本外验证",
        "revision": 1,
        "created_at": "2026-11-01T10:00:00+08:00",
        "variant": {"label": "低波动第二代", "strategy": "low_vol_trend", "lifecycle_status": "样本积累"},
    }])

    result = decision_history.build_history(kind="research", status="continue_validation")

    assert result["total"] == 1
    assert result["sources"]["variant_decisions"] == {
        "available": True, "error": "", "count": 1,
    }
    item = result["items"][0]
    assert item["subkind"] == "variant_decision"
    assert item["source_snapshot_id"] == "a" * 64
    assert item["href"] == "/backtest"
    assert "仅记录个人研究决定" in item["detail"]


def test_decision_history_hides_global_formal_reviews_for_non_local_tenant(projection):
    token = tenant.current_tenant.set("u_other")
    try:
        result = decision_history.build_history()
    finally:
        tenant.current_tenant.reset(token)
    assert result["sources"]["formal_signal_reviews"]["available"] is False
    assert "未证明" in result["sources"]["formal_signal_reviews"]["error"]
    assert not any(row["kind"] == "signal" for row in result["items"])


def test_decision_history_reads_positions_without_triggering_tenant_migration(projection):
    result = decision_history.build_history(kind="position")
    assert result["total"] == 2
    assert {row["id"] for row in result["items"]} == {"pos-1", "pos-old"}


def test_decision_history_treats_missing_portfolio_as_empty(projection, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(decision_history.pf, "_pf_file", lambda: str(tmp_path / "missing.json"))
    result = decision_history.build_history(kind="position")
    assert result["sources"]["position"] == {"available": True, "error": "", "count": 0}
    assert result["items"] == []


@pytest.mark.parametrize("failure", ["invalid_json", "permission"])
def test_decision_history_position_source_failure_keeps_other_sources(projection, monkeypatch, tmp_path: Path, failure: str):
    portfolio_file = tmp_path / "portfolio.json"
    if failure == "invalid_json":
        portfolio_file.write_text("{", encoding="utf-8")
        monkeypatch.setattr(decision_history.pf, "_pf_file", lambda: str(portfolio_file))
    else:
        real_open = builtins.open

        def fail_portfolio(path, *args, **kwargs):
            if str(path) == str(portfolio_file):
                raise PermissionError("portfolio unavailable")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(decision_history.pf, "_pf_file", lambda: str(portfolio_file))
        monkeypatch.setattr(builtins, "open", fail_portfolio)

    result = decision_history.build_history()
    assert result["sources"]["position"]["available"] is False
    assert result["sources"]["candidate"]["available"] is True
    assert any(row["kind"] == "candidate" for row in result["items"])
    assert any(row["kind"] == "signal" for row in result["items"])


def test_decision_history_rejects_invalid_filters(projection):
    with pytest.raises(ValueError, match="kind"):
        decision_history.build_history(kind="notes")
    with pytest.raises(ValueError, match="since"):
        decision_history.build_history(since="2026-2-1")


@pytest.mark.parametrize("error", [ValueError("坏记录"), PermissionError("候选存储不可读")])
def test_decision_history_endpoint_returns_partial_source_states(projection, monkeypatch, error):
    from fastapi.testclient import TestClient
    import app as app_module

    monkeypatch.setattr("candidate_observations.list_observations", lambda: (_ for _ in ()).throw(error))
    response = TestClient(app_module.app).get("/api/aqsp/decision-history?kind=position")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["sources"]["candidate"]["available"] is False
    assert payload["sources"]["position"]["available"] is True
    assert payload["items"]
    assert all(row["kind"] == "position" for row in payload["items"])
