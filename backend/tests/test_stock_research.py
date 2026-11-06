from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import decision_history
import portfolio as pf
import stock_research
import tenant


PERSONAL_CONTEXT = {
    "personal_intent": "核对行业景气是否支撑当前观察",
    "time_horizon": "下一个财报披露前",
    "personal_hypothesis": "收入增速可能改善",
    "counterargument": "现金流可能没有跟上",
}


def _evidence() -> dict:
    return {
        "technical.levels.rolling_range": {
            "domain": "technical", "title": "区间", "value": {"high": 12.0},
            "source": "fixture", "as_of": "2026-10-30", "price_basis": "pit_as_of",
        },
        "fundamental.profile": {
            "domain": "fundamental", "title": "公司资料", "value": {"行业": "测试行业"},
            "source": "fixture", "as_of": "2026-10-31T10:00:00+08:00",
        },
        "financial.latest": {
            "domain": "financial", "title": "最新报告期", "value": {"period": "2026-06-30", "roe": 10},
            "source": "fixture", "as_of": "2026-06-30",
        },
        "news.item.1": {
            "domain": "news", "title": "新闻", "value": {"title": "公司公告"},
            "source": "fixture", "as_of": "2026-10-31T09:00:00+08:00",
        },
    }


def _model_result(evidence: dict, *, invalid_domain: str | None = None) -> str:
    dimensions = {}
    for domain in ("technical", "fundamental", "financial", "news"):
        evidence_id = next((key for key, row in evidence.items() if row["domain"] == domain), None)
        if evidence_id:
            dimensions[domain] = {
                "status": "available",
                "findings": [{"text": f"基于 {domain} 证据", "evidence_ids": [evidence_id]}],
                "missing": [],
            }
        else:
            dimensions[domain] = {"status": "unavailable", "findings": [], "missing": ["来源缺失"]}
    if invalid_domain:
        dimensions[invalid_domain]["findings"][0]["evidence_ids"] = ["not-an-allowed-id"]
    return json.dumps({
        "summary": "四维摘要。",
        "summary_evidence_ids": [next(iter(evidence))],
        **dimensions,
        "missing_evidence": [],
    }, ensure_ascii=False)


@pytest.fixture
def report_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    token = tenant.current_tenant.set("local")
    yield tmp_path
    tenant.current_tenant.reset(token)


def _patch_model(monkeypatch: pytest.MonkeyPatch, *, invalid_domain: str | None = None) -> None:
    evidence = _evidence()
    endpoint = SimpleNamespace(url="https://model.invalid", key="secret", model="test-model", anthropic=False)
    limits = SimpleNamespace(synthesis_timeout=1.0)
    monkeypatch.setattr(
        stock_research.aqsp_bridge,
        "candidate_payload",
        lambda *_args: (_ for _ in ()).throw(stock_research.aqsp_bridge.AQSPCandidateNotFound("not current candidate")),
    )
    async def fake_collect(code):
        assert code == "600001"
        return "示例公司", evidence, {
            "technical": [], "fundamental": [], "financial": [], "news": [],
        }

    monkeypatch.setattr(stock_research, "_collect_evidence", fake_collect)
    monkeypatch.setattr("position_discussion.resolve_endpoint", lambda config: endpoint)
    monkeypatch.setattr("position_discussion.discussion_limits", lambda: limits)

    async def fake_call(_endpoint, _payload, _timeout, _limits, *, system_prompt):
        assert "只能依据输入 evidence" in system_prompt
        return _model_result(evidence, invalid_domain=invalid_domain)

    monkeypatch.setattr("position_discussion.call_model", fake_call)


def test_stock_research_requires_personal_question_and_horizon() -> None:
    with pytest.raises(stock_research.StockResearchError, match="问题和观察期限"):
        stock_research._personal_context({"personal_intent": "验证行业"})


def test_stock_research_collects_source_timing_and_keeps_missing_news_explicit(monkeypatch):
    monkeypatch.setattr(stock_research.astock, "tencent_quote", lambda _codes: {
        "600001": {"name": "测试公司", "price": 12.3, "as_of": "2026-10-30T14:55:00+08:00", "source": "tencent"},
    })
    monkeypatch.setattr(stock_research.astock, "individual_info", lambda _code: {"行业": "测试行业"})
    monkeypatch.setattr(stock_research.astock, "financials", lambda _code: {"period": "2026-06-30", "roe": 10})
    monkeypatch.setattr(stock_research.astock, "stock_news", lambda _code, limit: [])
    monkeypatch.setattr("decision_context.position_quote_issue", lambda *_args, **_kwargs: "")
    monkeypatch.setattr("stock_levels.load_stock_levels", lambda _code: {
        "as_of": "2026-10-30", "source": "tencent", "price_basis": "pit_as_of",
        "levels": [{"type": "rolling_range", "status": "available", "formula": "range",
                    "source": "tencent + PIT factors", "as_of": "2026-10-30", "price_basis": "pit_as_of",
                    "levels": [{"label": "区间压力", "price": 12.5}]}],
    })

    name, evidence, missing = asyncio.run(stock_research._collect_evidence("600001"))

    assert name == "测试公司"
    assert evidence["technical.levels.rolling_range"]["price_basis"] == "pit_as_of"
    assert evidence["financial.latest"]["as_of"] == "2026-06-30"
    assert missing["news"] == ["个股新闻源未返回可引用新闻"]


def test_stock_research_generates_draft_then_saves_to_tenant_history(report_store, monkeypatch):
    _patch_model(monkeypatch)

    report = asyncio.run(stock_research.run_stock_research("600001", PERSONAL_CONTEXT))

    assert report["status"] == "complete"
    assert report["saved"] is False
    assert report["dimensions"]["financial"]["findings"][0]["evidence_ids"] == ["financial.latest"]
    assert stock_research.list_saved_reports("600001") == []
    with pytest.raises(stock_research.StockResearchError, match="已保存报告不存在"):
        stock_research.get_saved_report(report["report_id"])
    saved = stock_research.save_report(report["report_id"])
    assert saved["saved"] is True
    assert saved["revision"] == 2
    assert stock_research.list_saved_reports("600001")[0]["report_id"] == report["report_id"]
    assert stock_research.get_saved_report(report["report_id"]) == saved

    history = decision_history.build_history(kind="research", symbol="600001")
    assert history["total"] == 1
    assert history["items"][0]["id"] == report["report_id"]
    assert history["items"][0]["href"] == (
        f"/stock/600001?report_id={report['report_id'].replace(':', '%3A')}"
        "#stock-research-analysis"
    )


def test_stock_research_store_is_tenant_scoped(report_store, monkeypatch):
    _patch_model(monkeypatch)
    report = asyncio.run(stock_research.run_stock_research("600001", PERSONAL_CONTEXT))
    stock_research.save_report(report["report_id"])

    token = tenant.current_tenant.set("u_other")
    try:
        assert stock_research.list_saved_reports() == []
        with pytest.raises(stock_research.StockResearchError, match="草稿不存在"):
            stock_research.save_report(report["report_id"])
    finally:
        tenant.current_tenant.reset(token)


def test_stock_research_rejects_cross_domain_or_fabricated_evidence(report_store, monkeypatch):
    _patch_model(monkeypatch, invalid_domain="news")

    with pytest.raises(stock_research.StockResearchModelError, match="证据校验"):
        asyncio.run(stock_research.run_stock_research("600001", PERSONAL_CONTEXT))

    assert stock_research.list_saved_reports() == []
    report_file = report_store / "stock_research_reports.json"
    assert not report_file.exists()


@pytest.mark.parametrize("field", ["summary", "finding", "missing"])
def test_stock_research_rejects_trade_directives_in_display_text(report_store, monkeypatch, field):
    _patch_model(monkeypatch)
    evidence = _evidence()

    async def unsafe_call(_endpoint, _payload, _timeout, _limits, *, system_prompt):
        result = json.loads(_model_result(evidence))
        if field == "summary":
            result["summary"] = "立即买入，目标价20元。"
        elif field == "finding":
            result["technical"]["findings"][0]["text"] = "建议减仓并设置止损。"
        else:
            result["financial"]["missing"] = ["考虑加仓前补齐财务数据"]
        return json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr("position_discussion.call_model", unsafe_call)
    with pytest.raises(stock_research.StockResearchModelError, match="交易指令或目标价"):
        asyncio.run(stock_research.run_stock_research("600001", PERSONAL_CONTEXT))

    assert not (report_store / "stock_research_reports.json").exists()


def test_stock_research_refuses_to_create_generic_report_for_current_holding(report_store, monkeypatch):
    monkeypatch.setattr(pf, "_load", lambda: {"holdings": [{"code": "600001"}]})
    called = False

    async def collect(_code):
        nonlocal called
        called = True
        return "", {}, {}

    monkeypatch.setattr(stock_research, "_collect_evidence", collect)
    with pytest.raises(stock_research.StockResearchError, match="持仓工作区"):
        asyncio.run(stock_research.run_stock_research("600001", PERSONAL_CONTEXT))
    assert called is False


def test_stock_research_routes_current_candidate_to_candidate_review(report_store, monkeypatch):
    monkeypatch.setattr(pf, "_load", lambda: {"holdings": []})
    monkeypatch.setattr(
        stock_research.aqsp_bridge,
        "candidate_payload",
        lambda *_args: {"symbol": "600001"},
    )
    monkeypatch.setattr(stock_research, "_collect_evidence", lambda *_args: pytest.fail("must not fetch evidence"))

    with pytest.raises(stock_research.StockResearchError, match="候选专属复核"):
        asyncio.run(stock_research.run_stock_research("600001", PERSONAL_CONTEXT))

