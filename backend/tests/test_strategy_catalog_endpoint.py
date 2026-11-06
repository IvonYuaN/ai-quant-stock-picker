from __future__ import annotations

import app as app_module
from fastapi.testclient import TestClient


def test_strategy_catalog_endpoint_returns_hypotheses_and_validation_gates():
    response = TestClient(app_module.app).get("/api/aqsp/strategy-catalog")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["version"]
    family = next(item for item in payload["families"] if item["id"] == "ma_pullback")
    assert family["hypothesis"]
    assert family["signals"]
    assert family["validation_required"]
    assert isinstance(family["runtime_ready"], bool)


def test_strategy_catalog_endpoint_reports_missing_catalog(monkeypatch, tmp_path):
    import aqsp.strategies.catalog as strategy_catalog

    monkeypatch.setattr(strategy_catalog, "_DEFAULT_CATALOG_PATH", tmp_path / "missing.yaml")
    response = TestClient(app_module.app).get("/api/aqsp/strategy-catalog")

    assert response.status_code == 503
    assert "策略目录暂不可用" in response.json()["detail"]


def test_strategy_catalog_endpoint_fails_closed_on_unknown_status(monkeypatch, tmp_path):
    import aqsp.strategies.catalog as strategy_catalog

    path = tmp_path / "strategy_sources.yaml"
    path.write_text(
        """
version: test
families:
  - id: multi_factor
    name: 多因子研究
    hypothesis: 先验证样本外结果。
    current_status: reseach_absorbed
    absorbed_from: [repo/a]
    validation_required: [walk-forward]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(strategy_catalog, "_DEFAULT_CATALOG_PATH", path)

    response = TestClient(app_module.app).get("/api/aqsp/strategy-catalog")

    assert response.status_code == 503
    assert "multi_factor" in response.json()["detail"]
    assert "invalid current_status" in response.json()["detail"]
