from __future__ import annotations

from fastapi.testclient import TestClient

import app as app_module
import stock_research


def test_stock_research_api_analyze_save_and_list(monkeypatch):
    report = {
        "report_id": "stock-research:test-report",
        "status": "complete",
        "generated_at": "2026-10-31T11:00:00+08:00",
        "saved_at": None,
        "symbol": "600001",
        "name": "测试公司",
        "model": "test-model",
        "summary": "有证据的摘要",
        "summary_evidence_ids": ["technical.levels.rolling_range"],
        "dimensions": {
            key: {"status": "unavailable", "findings": [], "missing": ["来源缺失"]}
            for key in ("technical", "fundamental", "financial", "news")
        },
        "evidence": {},
        "missing_evidence": [],
        "personal_context": {
            "personal_intent": "核对行业", "time_horizon": "财报前",
            "personal_hypothesis": "收入改善", "counterargument": "现金流偏弱",
        },
        "saved": False,
        "revision": 1,
        "notice": "仅供人工复核。",
    }

    async def analyze(code, personal_context, config=None, server_model=None):
        assert code == "600001"
        assert personal_context["time_horizon"] == "财报前"
        assert config is None
        assert server_model is None
        return report

    def save(report_id):
        assert report_id == report["report_id"]
        return {**report, "saved": True, "revision": 2, "saved_at": "2026-10-31T11:01:00+08:00"}

    def saved_report(report_id):
        assert report_id == report["report_id"]
        return {**report, "saved": True, "revision": 2, "saved_at": "2026-10-31T11:01:00+08:00"}

    monkeypatch.setattr(stock_research, "run_stock_research", analyze)
    monkeypatch.setattr(stock_research, "save_report", save)
    monkeypatch.setattr(stock_research, "get_saved_report", saved_report)
    monkeypatch.setattr(stock_research, "list_saved_reports", lambda code=None: [{**report, "saved": True}] if code == "600001" else [])
    client = TestClient(app_module.app)

    response = client.post("/api/stock-research/600001/analyze", json=report["personal_context"])
    assert response.status_code == 200
    assert response.json()["data"]["saved"] is False

    saved = client.post(f"/api/stock-research/reports/{report['report_id']}/save", json={})
    assert saved.status_code == 200
    assert saved.json()["data"]["saved"] is True

    reopened = client.get(f"/api/stock-research/reports/{report['report_id']}")
    assert reopened.status_code == 200
    assert reopened.json()["data"]["report_id"] == report["report_id"]

    listed = client.get("/api/stock-research/reports?code=600001")
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["report_id"] == report["report_id"]

