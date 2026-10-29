"""Model-only deduplication must preserve current and historical source facts."""
from __future__ import annotations

from copy import deepcopy

import pytest

from model_evidence_projection import model_evidence_projection


def source_evidence() -> tuple[dict, dict]:
    stock = {"status": "rising", "source": "synthetic-stock", "as_of": "2026-10-29T10:00:00+08:00",
             "change_pct": 1.2, "price_path": {"status": "available", "windows": [{"window": 5, "volume_ratio": 1.1}]}}
    market = {"status": "unknown", "source": "", "as_of": None, "issues": ["width unavailable"]}
    sector = {"status": "falling", "source": "synthetic-sector", "as_of": "2026-10-29T09:59:00+08:00"}
    price = {"price": 11.9, "label": "本交易日已见低点", "source": "synthetic-stock",
             "as_of": "2026-10-29T10:00:00+08:00"}
    evidence = {"holding": {"shares": 1000, "cost": 12.0}, "stock": stock, "market": market,
                "sector": sector, "risk_trigger": {"levels": ["L1"]}, "price_0": deepcopy(price),
                "route_constraints": {"routes": [{"choice": "t_watch", "status": "insufficient"}],
                                      "evidence": {"stock": deepcopy(stock), "market": deepcopy(market),
                                                   "sector": deepcopy(sector)}}}
    return evidence, {"price_0": price}


def test_model_evidence_projection_preserves_all_facts_when_duplicate_sources_are_referenced() -> None:
    evidence, prices = source_evidence()
    before, prices_before = deepcopy(evidence), deepcopy(prices)
    projected = model_evidence_projection(evidence, prices)
    assert projected["route_constraints"]["evidence"] == {
        key: {"evidence_ref": key} for key in ("market", "sector", "stock")}
    assert projected["price_0"] == {"price_ref": "price_0"}
    # Explicit pointers can recover the exact original packet with its types.
    resolved = deepcopy(projected)
    for key, pointer in resolved["route_constraints"]["evidence"].items():
        resolved["route_constraints"]["evidence"][key] = deepcopy(projected[pointer["evidence_ref"]])
    resolved["price_0"] = deepcopy(prices[projected["price_0"]["price_ref"]])
    assert resolved == before
    assert evidence == before and prices == prices_before
    assert projected["holding"] == before["holding"]
    assert projected["risk_trigger"] == before["risk_trigger"]
    assert projected["route_constraints"]["routes"] == before["route_constraints"]["routes"]


@pytest.mark.parametrize("mutation", [
    {"source": "different-source"}, {"as_of": "2026-10-28T10:00:00+08:00"},
    {"price_path": {"status": "available", "windows": [{"window": 5, "volume_ratio": 1.2}]}},
    {"additional_fact": "not repeated"},
])
def test_model_evidence_projection_retains_nested_evidence_when_any_fact_differs(mutation: dict) -> None:
    evidence, prices = source_evidence()
    evidence["route_constraints"]["evidence"]["stock"].update(mutation)
    before = deepcopy(evidence)
    projected = model_evidence_projection(evidence, prices)
    assert projected["route_constraints"]["evidence"]["stock"] == before["route_constraints"]["evidence"]["stock"]
    assert evidence == before


@pytest.mark.parametrize("left,right", [(True, 1), (1, 1.0), ([1], (1,)), ({"a": True}, {"a": 1})])
def test_model_evidence_projection_retains_original_type_when_python_values_compare_equal(left, right) -> None:
    evidence, prices = source_evidence()
    evidence["stock"]["typed"] = left
    evidence["route_constraints"]["evidence"]["stock"]["typed"] = right
    evidence["price_0"]["typed"] = left
    prices["price_0"]["typed"] = right
    projected = model_evidence_projection(evidence, prices)
    assert "evidence_ref" not in projected["route_constraints"]["evidence"]["stock"]
    assert "price_ref" not in projected["price_0"]
    assert type(projected["stock"]["typed"]) is type(left)


@pytest.mark.parametrize("route", [None, [], "invalid", {}, {"evidence": None}, {"evidence": []}, {"evidence": {}}])
def test_model_evidence_projection_keeps_missing_or_malformed_routes_when_no_facts_can_be_referenced(route) -> None:
    evidence = {"route_constraints": route, "market": {}, "price_0": {}}
    assert model_evidence_projection(evidence, {"price_0": {}}) == evidence
    assert model_evidence_projection({}, {}) == {}


def test_model_evidence_projection_keeps_unknown_and_unmatched_fields_when_only_current_namespaces_are_known() -> None:
    evidence, prices = source_evidence()
    evidence["future_namespace"] = {"source": "future", "value": 1}
    evidence["route_constraints"]["evidence"]["future_namespace"] = deepcopy(evidence["future_namespace"])
    evidence["route_constraints"]["evidence"]["missing_top_level"] = {"value": 2}
    evidence["other_price"] = deepcopy(prices["price_0"])
    evidence["price_1"] = deepcopy(prices["price_0"])
    evidence["price_2"] = {**prices["price_0"], "source": "different"}
    prices["other_price"] = deepcopy(prices["price_0"])
    prices["price_2"] = deepcopy(prices["price_0"])
    projected = model_evidence_projection(evidence, prices)
    assert projected["route_constraints"]["evidence"]["future_namespace"] == evidence["future_namespace"]
    assert projected["route_constraints"]["evidence"]["missing_top_level"] == {"value": 2}
    for key in ("other_price", "price_1", "price_2"):
        assert projected[key] == evidence[key]


def test_model_evidence_projection_retains_history_when_adopted_facts_equal_current_sources() -> None:
    evidence, prices = source_evidence()
    historical = {"evidence_snapshot": deepcopy(evidence), "price_refs": deepcopy(prices)}
    evidence["adopted_plan"] = historical
    evidence["route_constraints"]["history"] = deepcopy(historical)
    projected = model_evidence_projection(evidence, prices)
    assert projected["adopted_plan"] == historical
    assert projected["route_constraints"]["history"] == historical
    assert "evidence_ref" not in projected["adopted_plan"]["evidence_snapshot"]["route_constraints"]["evidence"]["stock"]


def test_model_evidence_projection_does_not_share_mutable_data_when_consumer_modifies_packet() -> None:
    evidence, prices = source_evidence()
    evidence_before, prices_before = deepcopy(evidence), deepcopy(prices)
    projected = model_evidence_projection(evidence, prices)
    projected["stock"]["price_path"]["windows"][0]["volume_ratio"] = 999
    projected["holding"]["shares"] = 1
    projected["route_constraints"]["routes"][0]["status"] = "changed"
    assert evidence == evidence_before and prices == prices_before
