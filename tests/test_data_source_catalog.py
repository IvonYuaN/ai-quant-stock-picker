from __future__ import annotations

import pytest

from aqsp.data.source_catalog import load_data_source_catalog


def test_load_data_source_catalog_reads_runtime_and_research_sources() -> None:
    catalog = load_data_source_catalog()
    by_id = catalog.by_id()

    assert catalog.version
    assert "eastmoney" in catalog.fallback_order["daily_close"]
    assert by_id["akshare"].runtime_ready is True
    assert by_id["a_stock_data_endpoint_reference"].runtime_ready is False
    assert by_id["a_stock_data_endpoint_reference"].adoption_gate
    assert len(catalog.runtime_sources()) >= 5
    assert len(catalog.research_candidates()) >= 3
    assert catalog.fallback_sources("daily_close")[0].id == "tdx_vipdoc"


def test_load_data_source_catalog_rejects_candidate_without_gate(tmp_path) -> None:
    path = tmp_path / "data_sources.yaml"
    path.write_text(
        """
version: test
default_policy: local_first
sources:
  - id: cninfo
    name: Cninfo
    category: announcement
    markets: [a_share]
    access: http_api
    cost: free
    runtime_ready: false
    research_status: research_candidate
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing adoption_gate"):
        load_data_source_catalog(path)


def test_load_data_source_catalog_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "data_sources.yaml"
    path.write_text(
        """
version: test
default_policy: local_first
sources:
  - id: eastmoney
    name: Eastmoney
    category: market_data
    markets: [a_share]
    access: http_api
    cost: free
    runtime_ready: true
    research_status: runtime
  - id: eastmoney
    name: Eastmoney Copy
    category: market_data
    markets: [a_share]
    access: http_api
    cost: free
    runtime_ready: true
    research_status: runtime
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate data source catalog id"):
        load_data_source_catalog(path)


def test_load_data_source_catalog_rejects_unknown_fallback_source(tmp_path) -> None:
    path = tmp_path / "data_sources.yaml"
    path.write_text(
        """
version: test
default_policy: local_first
fallback_order:
  online_first: [missing_source]
sources:
  - id: eastmoney
    name: Eastmoney
    category: market_data
    markets: [a_share]
    access: http_api
    cost: free
    runtime_ready: true
    research_status: runtime
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown fallback source"):
        load_data_source_catalog(path)


def test_load_data_source_catalog_rejects_non_runtime_fallback_source(tmp_path) -> None:
    path = tmp_path / "data_sources.yaml"
    path.write_text(
        """
version: test
default_policy: local_first
fallback_order:
  online_first: [research_only]
sources:
  - id: research_only
    name: Research Only
    category: market_data
    markets: [a_share]
    access: http_api
    cost: free
    runtime_ready: false
    research_status: candidate
    adoption_gate: [fixture]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not runtime_ready"):
        load_data_source_catalog(path)


def test_load_data_source_catalog_rejects_string_runtime_flag(tmp_path) -> None:
    path = tmp_path / "data_sources.yaml"
    path.write_text(
        """
version: test
default_policy: local_first
sources:
  - id: eastmoney
    name: Eastmoney
    category: market_data
    markets: [a_share]
    access: http_api
    cost: free
    runtime_ready: "true"
    research_status: runtime
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be a boolean"):
        load_data_source_catalog(path)


def test_load_data_source_catalog_rejects_malformed_fallback_order(tmp_path) -> None:
    path = tmp_path / "data_sources.yaml"
    path.write_text(
        """
version: test
default_policy: local_first
fallback_order: [eastmoney]
sources:
  - id: eastmoney
    name: Eastmoney
    category: market_data
    markets: [a_share]
    access: http_api
    cost: free
    runtime_ready: true
    research_status: runtime
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fallback_order must be a mapping"):
        load_data_source_catalog(path)
