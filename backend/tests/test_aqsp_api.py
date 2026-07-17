"""AQSP research API contract tests.

These tests intentionally exercise the HTTP boundary only. The bridge remains
the implementation owner; this file defines the behaviour it must expose.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module


SNAPSHOT_ROUTE = "/api/aqsp/snapshot"
FORBIDDEN_COPY = re.compile(r"买入|下单")


def _snapshot(
    selected_date: str,
    *,
    stale_after: str,
    messages: list[dict] | None = None,
    debates: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "v1",
        "generated_at": f"{selected_date}T09:30:00+08:00",
        "selected_date": selected_date,
        "available_dates": ["2026-07-14", "2026-07-10"],
        "candidates": [
            {
                "symbol": "600519",
                "display_name": "贵州茅台",
                "score": 72.5,
                "research_status": "纸面复核",
                "next_step": "等待证据确认",
                "context": "合成测试快照",
                "deterministic_reasons": ["量价确认"],
                "strategies": ["demo"],
                "evidence_status": "充分",
            }
        ],
        "debates": debates if debates is not None else [],
        "summaries": ["研究快照已生成"],
        "source": {
            "effective": "fixture",
            "latest_trade_date": selected_date,
            "lag_days": 0,
            "status": "fresh",
        },
        "coldstart": {"status": "完成", "detail": ""},
        "stale_after": stale_after,
        "message_status": "ok" if messages else "未产出",
        "messages": messages if messages is not None else [],
    }


def _write_snapshot_files(
    tmp_path: Path,
    *,
    current: dict,
    historical: dict | None = None,
) -> None:
    snapshot_path = tmp_path / "home_dashboard_snapshot.json"
    index_path = tmp_path / "home_dashboard_snapshot_index.json"
    snapshot_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    days = [{"date": current["selected_date"], "snapshot": current}]
    if historical is not None:
        days.append({"date": historical["selected_date"], "snapshot": historical})
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "v1-index",
                "generated_at": current["generated_at"],
                "stale_after": current["stale_after"],
                "selected_date": current["selected_date"],
                "days": days,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def aqsp_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    current = _snapshot(
        "2026-07-14",
        stale_after="2099-01-01T09:30:00+08:00",
        messages=[
            {
                "title": "测试消息",
                "summary": "仅用于契约验收",
                "impact": "中性",
                "category": "市场",
                "source": "fixture",
                "published_at": "2026-07-14T09:00:00+08:00",
            }
        ],
        debates=[
            {
                "symbol": "600519",
                "display_name": "贵州茅台",
                "conclusion": "维持纸面复核",
                "primary_risk_gate": "证据确认",
                "next_trigger": "新增独立证据",
                "active_roles": ["risk"],
                "round_summaries": ["第1轮完成技术与风险初筛"],
            }
        ],
    )
    historical = _snapshot(
        "2026-07-10",
        stale_after="2026-07-11T09:30:00+08:00",
        messages=[],
        debates=[],
    )
    _write_snapshot_files(tmp_path, current=current, historical=historical)
    monkeypatch.setenv(
        "AQSP_RESEARCH_SURFACE_SNAPSHOT",
        str(tmp_path / "home_dashboard_snapshot.json"),
    )
    return TestClient(app_module.app)


def _aqsp_routes() -> list[tuple[str, set[str]]]:
    return sorted(
        (route.path, set(route.methods or ()))
        for route in app_module.app.routes
        if route.path.startswith("/api/aqsp")
    )


def test_aqsp_api_exposes_one_read_only_snapshot_route() -> None:
    assert _aqsp_routes()
    assert all(methods == {"GET"} for _path, methods in _aqsp_routes())
    assert not any(
        path.startswith("/api/aqsp/portfolio") for path, _methods in _aqsp_routes()
    )


def test_aqsp_api_returns_current_snapshot_with_messages_and_agents(
    aqsp_client: TestClient,
) -> None:
    response = aqsp_client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["selected_date"] == "2026-07-14"
    assert body["data"]["messages"]
    assert body["data"]["debates"]
    assert body["data"]["debates"][0]["round_summaries"] == [
        "第1轮完成技术与风险初筛"
    ]
    assert body["data"]["stale_after"]
    assert body["meta"] == {
        "historical": False,
        "stale": False,
        "freshness": {
            "candidates": "unavailable",
            "messages": "fresh",
            "cross_market": "unavailable",
        },
    }


def test_aqsp_api_returns_503_when_current_snapshot_lacks_stale_after(
    aqsp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current = _snapshot(
        "2026-07-14",
        stale_after="2099-01-01T09:30:00+08:00",
        messages=[],
        debates=[],
    )
    current.pop("stale_after")
    snapshot_path = tmp_path / "home_dashboard_snapshot.json"
    snapshot_path.write_text(json.dumps(current), encoding="utf-8")
    (tmp_path / "home_dashboard_snapshot_index.json").unlink()
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))

    response = aqsp_client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 503


def test_aqsp_api_returns_503_when_snapshot_source_is_missing(
    aqsp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(tmp_path / "missing.json"))

    response = aqsp_client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 503


@pytest.mark.parametrize("value", ["2026-7-14", "not-a-date", "2026-02-30"])
def test_aqsp_api_rejects_invalid_date_when_date_is_illegal(
    aqsp_client: TestClient,
    value: str,
) -> None:
    response = aqsp_client.get(SNAPSHOT_ROUTE, params={"date": value})

    assert response.status_code == 400


def test_aqsp_api_returns_exact_historical_date_without_substitution(
    aqsp_client: TestClient,
) -> None:
    response = aqsp_client.get(SNAPSHOT_ROUTE, params={"date": "2026-07-10"})

    assert response.status_code == 200
    assert response.json()["data"]["selected_date"] == "2026-07-10"
    assert response.json()["data"]["stale_after"] == "2026-07-11T09:30:00+08:00"
    assert response.json()["meta"] == {
        "historical": True,
        "stale": True,
        "freshness": {
            "candidates": "unavailable",
            "messages": "no_data",
            "cross_market": "unavailable",
        },
    }


def test_aqsp_api_allows_historical_snapshot_without_stale_after(
    aqsp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current = _snapshot(
        "2026-07-14",
        stale_after="2099-01-01T09:30:00+08:00",
        messages=[],
        debates=[],
    )
    historical = _snapshot(
        "2026-07-10",
        stale_after="2026-07-11T09:30:00+08:00",
        messages=[],
        debates=[],
    )
    historical.pop("stale_after")
    _write_snapshot_files(tmp_path, current=current, historical=historical)
    monkeypatch.setenv(
        "AQSP_RESEARCH_SURFACE_SNAPSHOT",
        str(tmp_path / "home_dashboard_snapshot.json"),
    )

    response = aqsp_client.get(SNAPSHOT_ROUTE, params={"date": "2026-07-10"})

    assert response.status_code == 200
    assert response.json()["data"]["selected_date"] == "2026-07-10"
    assert response.json()["meta"] == {
        "historical": True,
        "stale": True,
        "freshness": {
            "candidates": "unavailable",
            "messages": "no_data",
            "cross_market": "unavailable",
        },
    }


def test_aqsp_api_returns_404_when_requested_date_is_missing(
    aqsp_client: TestClient,
) -> None:
    response = aqsp_client.get(SNAPSHOT_ROUTE, params={"date": "2026-07-09"})

    assert response.status_code == 404


def test_aqsp_api_rejects_current_snapshot_after_ttl(
    aqsp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expired = _snapshot(
        "2026-07-14",
        stale_after="2026-07-13T09:30:00+08:00",
        messages=[],
        debates=[],
    )
    _write_snapshot_files(tmp_path, current=expired)
    monkeypatch.setenv(
        "AQSP_RESEARCH_SURFACE_SNAPSHOT",
        str(tmp_path / "home_dashboard_snapshot.json"),
    )

    response = aqsp_client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 503


def test_aqsp_api_preserves_explicit_empty_messages_and_agents(
    aqsp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    empty = _snapshot(
        "2026-07-14",
        stale_after="2099-01-01T09:30:00+08:00",
        messages=[],
        debates=[],
    )
    _write_snapshot_files(tmp_path, current=empty)
    monkeypatch.setenv(
        "AQSP_RESEARCH_SURFACE_SNAPSHOT",
        str(tmp_path / "home_dashboard_snapshot.json"),
    )

    body = aqsp_client.get(SNAPSHOT_ROUTE).json()["data"]

    assert body["messages"] == []
    assert body["debates"] == []
    assert body["message_status"] == "未产出"


def test_aqsp_api_does_not_expose_portfolio_writes_or_trade_copy(
    aqsp_client: TestClient,
) -> None:
    response = aqsp_client.get(SNAPSHOT_ROUTE)
    assert response.status_code == 200
    response_text = json.dumps(response.json(), ensure_ascii=False)
    assert "portfolio" not in response_text.lower()
    assert not FORBIDDEN_COPY.search(response_text)

    operation = app_module.app.openapi()["paths"][SNAPSHOT_ROUTE]["get"]
    operation_text = json.dumps(operation, ensure_ascii=False)
    assert "portfolio" not in operation_text.lower()
    assert not FORBIDDEN_COPY.search(operation_text)


def test_aqsp_snapshot_timestamp_contract_is_timezone_aware(
    aqsp_client: TestClient,
) -> None:
    body = aqsp_client.get(SNAPSHOT_ROUTE).json()["data"]

    assert datetime.fromisoformat(body["generated_at"]).utcoffset() is not None
    assert datetime.fromisoformat(body["stale_after"]).utcoffset() is not None


@pytest.mark.parametrize(
    "published_at",
    ["2026-07-14T09:00:00", "2026-07-14", "not-a-timestamp"],
)
def test_aqsp_api_rejects_message_published_at_without_timezone_or_iso_format(
    aqsp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published_at: str,
) -> None:
    current = _snapshot(
        "2026-07-14",
        stale_after="2099-01-01T09:30:00+08:00",
        messages=[
            {
                "title": "测试消息",
                "summary": "仅用于契约验收",
                "impact": "中性",
                "category": "市场",
                "source": "fixture",
                "published_at": published_at,
            }
        ],
        debates=[],
    )
    _write_snapshot_files(tmp_path, current=current)
    monkeypatch.setenv(
        "AQSP_RESEARCH_SURFACE_SNAPSHOT",
        str(tmp_path / "home_dashboard_snapshot.json"),
    )

    response = aqsp_client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 503


def test_aqsp_api_normalizes_legacy_shanghai_message_timestamp(
    aqsp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current = _snapshot(
        "2026-07-14",
        stale_after="2099-01-01T09:30:00+08:00",
        messages=[
            {
                "title": "旧源消息",
                "summary": "兼容旧版产出",
                "impact": "中性",
                "category": "市场",
                "source": "fixture",
                "published_at": "2026-07-14 09:00:00",
            }
        ],
        debates=[],
    )
    _write_snapshot_files(tmp_path, current=current)
    monkeypatch.setenv(
        "AQSP_RESEARCH_SURFACE_SNAPSHOT",
        str(tmp_path / "home_dashboard_snapshot.json"),
    )

    response = aqsp_client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 200
    assert response.json()["data"]["messages"][0]["published_at"] == (
        "2026-07-14T09:00:00+08:00"
    )
