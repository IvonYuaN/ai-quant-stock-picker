"""AQSP bridge contract tests; all inputs are local synthetic snapshots."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("VR_DATA_DIR", "/tmp/aqsp-vibe-bridge-test-data")
os.environ.setdefault("VR_REPORTS_DIR", "/tmp/aqsp-vibe-bridge-test-reports")

import aqsp_bridge  # noqa: E402
import app as app_module  # noqa: E402


client = TestClient(app_module.app)


def _snapshot(selected_date: str, symbol: str = "600001") -> dict:
    return {
        "schema_version": "v1",
        "generated_at": f"{selected_date}T18:00:00+08:00",
        "stale_after": "2099-01-01T00:00:00+08:00",
        "selected_date": selected_date,
        "available_dates": [selected_date],
        "candidates": [
            {
                "symbol": symbol,
                "display_name": f"{symbol} 示例",
                "score": 72.5,
                "research_status": "纸面复核",
                "next_step": "确认量能承接",
                "context": "海外产业映射",
                "deterministic_reasons": ["MA20 斜率向上"],
                "strategies": ["ma_pullback"],
                "evidence_status": "有独立规则证据",
            }
        ],
        "debates": [
            {
                "symbol": symbol,
                "display_name": f"{symbol} 示例",
                "conclusion": "维持纸面复核",
                "primary_risk_gate": "量能承接",
                "next_trigger": "放量确认",
                "active_roles": ["risk"],
                "round_count": 2,
                "round_summaries": ["首轮提出假设", "二轮复核风险"],
                "support_points": ["量价同步"],
                "opposition_points": ["量能尚未确认"],
                "risk_warnings": ["高开回撤"],
                "watch_items": ["观察开盘承接"],
                "real_message_evidence": ["公告: 产品发布"],
                "cross_market_evidence": ["海外同业订单增长"],
                "rule_transmission_evidence": ["产品发布 -> 产业链订单"],
                "pending_confirmations": ["确认板块扩散"],
                "process_recorded": True,
                "conclusion_recorded": True,
                "debate_quality_issues": [],
                "viewpoint_buckets": {
                    "technical": ["均线多头"],
                    "risk_counterevidence": ["量能未确认"],
                },
                "disagreement_points": ["看空质询看多"],
                "uncertainty_points": ["等待板块扩散"],
                "advisory_only": True,
                "deterministic_score": 72.5,
                "deterministic_score_unchanged": True,
                "advisory_boundary_ok": True,
            }
        ],
        "summaries": ["仅供研究复核"],
        "source": {
            "effective": "sqlite",
            "latest_trade_date": selected_date,
            "lag_days": 0,
            "status": "fresh",
        },
        "coldstart": {"status": "ready", "detail": "样本已就绪"},
        "messages": [
            {
                "title": "测试消息",
                "summary": "仅供契约测试",
                "impact": "中性",
                "category": "市场",
                "source": "fixture",
                "published_at": "2026-07-14T09:00:00+08:00",
                "event_type": "产业政策",
                "affected_sectors": ["设备更新"],
                "affected_symbols": ["600001"],
                "transmission_hypothesis": "政策 -> A股产业链映射",
                "supporting_evidence": ["fixture: 测试消息"],
                "source_url": "https://example.test/news",
            }
        ],
    }


def _write_single(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "home_dashboard_snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_index(tmp_path: Path) -> Path:
    current = _snapshot("2026-07-14")
    historical = _snapshot("2026-07-11", symbol="600002")
    path = tmp_path / "home_dashboard_snapshot_index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "v1-index",
                "generated_at": "2026-07-14T18:00:00+08:00",
                "stale_after": "2026-07-15T18:00:00+08:00",
                "selected_date": "2026-07-14",
                "days": [
                    {"date": "2026-07-14", "snapshot": current},
                    {"date": "2026-07-11", "snapshot": historical},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return _write_single(tmp_path, current)


def _write_debates(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "debate_results.jsonl"
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _runtime_debate(*, date: str, symbol: str = "600002", score: float = 72.5) -> dict:
    return {
        "symbol": symbol,
        "debate_date": date,
        "research_verdict": "维持纸面复核",
        "primary_risk_gate": "量能承接",
        "next_trigger": "放量确认",
        "active_roles": ["bull", "bear", "risk"],
        "debate_rounds_completed": 2,
        "rounds": [{"summary": "多空完成复核"}],
        "support_points": ["量价同步"],
        "opposition_points": ["量能尚未确认"],
        "risk_warnings": ["高开回撤"],
        "watch_items": ["观察开盘承接"],
        "advisory_only": True,
        "deterministic_score": score,
        "deterministic_score_unchanged": True,
        "advisory_boundary_ok": True,
        "process_recorded": True,
        "conclusion_recorded": True,
    }


def test_aqsp_bridge_attaches_date_matched_runtime_debate_to_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _snapshot("2026-07-14")
    historical = _snapshot("2026-07-11", symbol="600002")
    historical["debates"] = []
    index_path = tmp_path / "home_dashboard_snapshot_index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "v1-index",
                "generated_at": "2026-07-14T18:00:00+08:00",
                "stale_after": "2099-01-01T00:00:00+08:00",
                "selected_date": "2026-07-14",
                "days": [
                    {"date": "2026-07-14", "snapshot": current},
                    {"date": "2026-07-11", "snapshot": historical},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    snapshot_path = _write_single(tmp_path, current)
    debate_path = _write_debates(
        tmp_path, _runtime_debate(date="2026-07-11", symbol="600002")
    )
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))
    monkeypatch.setenv("AQSP_DEBATE_RESULTS", str(debate_path))

    response = client.get("/api/aqsp/snapshot?date=2026-07-11")

    assert response.status_code == 200
    debate = response.json()["data"]["debates"][0]
    assert debate["symbol"] == "600002"
    assert debate["round_summaries"] == ["多空完成复核"]
    assert response.json()["data"]["candidates"][0]["score"] == 72.5


def test_aqsp_bridge_resolves_runtime_debates_from_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["debates"] = []
    snapshot_path = _write_single(tmp_path, payload)
    runtime_root = tmp_path / "runtime"
    debate_path = runtime_root / "data" / "debate_results.jsonl"
    debate_path.parent.mkdir(parents=True)
    debate_path.write_text(
        json.dumps(_runtime_debate(date="2026-07-14", symbol="600001")) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))
    monkeypatch.delenv("AQSP_DEBATE_RESULTS", raising=False)
    monkeypatch.setenv("AQSP_RUNTIME_ROOT", str(runtime_root))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()["data"]["debates"]] == [
        "600001"
    ]


def test_aqsp_bridge_does_not_attach_unmatched_runtime_debate_to_current_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["debates"] = []
    snapshot_path = _write_single(tmp_path, payload)
    debate_path = _write_debates(
        tmp_path, _runtime_debate(date="2026-07-13", symbol="600001")
    )
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))
    monkeypatch.setenv("AQSP_DEBATE_RESULTS", str(debate_path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert response.json()["data"]["debates"] == []


def test_aqsp_bridge_skips_runtime_debate_when_deterministic_score_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["debates"] = []
    snapshot_path = _write_single(tmp_path, payload)
    debate_path = _write_debates(
        tmp_path, _runtime_debate(date="2026-07-14", symbol="600001", score=71.0)
    )
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))
    monkeypatch.setenv("AQSP_DEBATE_RESULTS", str(debate_path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert response.json()["data"]["debates"] == []
    assert response.json()["data"]["candidates"][0]["score"] == 72.5


def test_aqsp_bridge_uses_candidate_signal_date_over_debate_run_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["debates"] = []
    snapshot_path = _write_single(tmp_path, payload)
    record = _runtime_debate(date="2026-07-14", symbol="600001")
    record["candidate_signal_date"] = "2026-07-12"
    debate_path = _write_debates(tmp_path, record)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))
    monkeypatch.setenv("AQSP_DEBATE_RESULTS", str(debate_path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert response.json()["data"]["debates"] == []


def test_aqsp_bridge_skips_incomplete_runtime_debate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["debates"] = []
    snapshot_path = _write_single(tmp_path, payload)
    record = _runtime_debate(date="2026-07-14", symbol="600001")
    record["conclusion_recorded"] = False
    debate_path = _write_debates(tmp_path, record)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))
    monkeypatch.setenv("AQSP_DEBATE_RESULTS", str(debate_path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert response.json()["data"]["debates"] == []


def test_aqsp_bridge_snapshot_returns_typed_candidate_payload_when_snapshot_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_single(tmp_path, _snapshot("2026-07-14"))
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["selected_date"] == "2026-07-14"
    assert payload["candidates"][0]["score"] == 72.5
    assert payload["messages"][0]["event_type"] == "产业政策"
    assert payload["messages"][0]["affected_sectors"] == ["设备更新"]
    assert payload["messages"][0]["supporting_evidence"] == ["fixture: 测试消息"]
    assert isinstance(
        aqsp_bridge.load_surface().current.candidates[0], aqsp_bridge.AQSPCandidate
    )


def test_aqsp_bridge_preserves_variant_position_names_and_previous_holdings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["variants"] = [
        {
            "variant_id": "trend_follow",
            "label": "趋势跟随",
            "initial_cash": 100000.0,
            "final_equity": 101000.0,
            "return_pct": 1.0,
            "filled_orders": 2,
            "rejected_orders": 0,
            "start_date": "2026-07-01",
            "end_date": "2026-07-14",
            "data_mode": "historical_raw_unadjusted",
            "cash": 40000.0,
            "total_pnl": 1000.0,
            "rank": 1,
            "strategy": "趋势跟随",
            "holdings": [{"symbol": "600001", "quantity": 100, "average_price": 10.0, "last_price": 11.0, "market_value": 1100.0, "unrealized_pnl": 100.0, "name": "示例公司"}],
            "previous_holdings": [{"symbol": "600002", "quantity": 100, "average_price": 9.0, "last_price": 9.5, "market_value": 950.0, "unrealized_pnl": 50.0, "name": "昨日公司"}],
            "adjustments": [{"action": "added", "symbol": "600001", "evidence": ["MACD柱体转强"]}],
            "recent_actions": ["2026-07-14 卖出 昨日公司 600002 100 股"],
        }
    ]
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    variant = response.json()["data"]["variants"][0]
    assert variant["holdings"][0]["name"] == "示例公司"
    assert variant["previous_holdings"][0]["name"] == "昨日公司"
    assert variant["adjustments"][0]["action"] == "added"
    assert variant["recent_actions"] == ["2026-07-14 卖出 昨日公司 600002 100 股"]


def test_aqsp_bridge_exposes_variant_universe_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["variant_universe"] = {
        "symbol_count": 828,
        "board_scope": "沪深主板+创业板",
        "excluded": ["ST", "科创板", "其他板块"],
        "latest_trade_date": "2026-07-13",
        "coverage_pct": 100.0,
        "sources": ["eastmoney", "sina"],
    }
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert response.json()["data"]["variant_universe"] == payload["variant_universe"]


def test_aqsp_bridge_rejects_candidates_from_incomplete_market_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["universe"] = {
        "batch_active": True,
        "coverage_pct": 0.25,
        "total": 5000,
    }
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 503


def test_aqsp_bridge_serves_read_only_status_without_partial_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["universe"] = {
        "batch_active": True,
        "coverage_pct": 0.25,
        "total": 5000,
    }
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))
    monkeypatch.setenv("AQSP_ALLOW_STALE_SNAPSHOT", "1")

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["candidates"] == []
    assert body["data"]["debates"] == []
    assert any("全市场批次未完成" in item for item in body["data"]["summaries"])


def test_aqsp_bridge_dates_and_candidate_use_exact_historical_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_index(tmp_path)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    dates_response = client.get("/api/aqsp/dates")
    candidate_response = client.get("/api/aqsp/candidate/600002?date=2026-07-11")

    assert dates_response.status_code == 200
    assert dates_response.json()["data"]["available_dates"] == [
        "2026-07-14",
        "2026-07-11",
    ]
    assert candidate_response.status_code == 200
    assert candidate_response.json()["data"]["date"] == "2026-07-11"
    assert candidate_response.json()["data"]["symbol"] == "600002"

    historical_snapshot = client.get("/api/aqsp/snapshot?date=2026-07-11")
    assert historical_snapshot.status_code == 200
    assert historical_snapshot.json()["data"]["available_dates"] == [
        "2026-07-14",
        "2026-07-11",
    ]


def test_aqsp_bridge_does_not_replace_missing_history_with_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_index(tmp_path)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot?date=2026-07-12")

    assert response.status_code == 404
    assert "未替换为最新日期" in response.json()["detail"]


def test_aqsp_bridge_rejects_snapshot_index_that_points_to_an_older_current_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _snapshot("2026-07-14")
    historical = _snapshot("2026-07-11", symbol="600002")
    snapshot_path = _write_single(tmp_path, current)
    index_path = tmp_path / "home_dashboard_snapshot_index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "v1-index",
                "generated_at": "2026-07-14T18:00:00+08:00",
                "stale_after": "2099-01-01T00:00:00+08:00",
                "selected_date": "2026-07-11",
                "days": [
                    {"date": "2026-07-14", "snapshot": current},
                    {"date": "2026-07-11", "snapshot": historical},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(snapshot_path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 503
    assert "拒绝回退旧日期" in response.json()["detail"]


def test_aqsp_bridge_marks_historical_payload_as_archive_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    historical = _snapshot("2026-07-11", symbol="600002")
    historical["source"]["status"] = "fresh"
    historical["candidates"][0]["freshness"] = "fresh"
    historical["message_status"] = "ok"
    path = _write_index(tmp_path)
    index_path = tmp_path / "home_dashboard_snapshot_index.json"
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    index_payload["days"][1]["snapshot"] = historical
    index_path.write_text(
        json.dumps(index_payload, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot?date=2026-07-11")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["source"]["status"] == "historical"
    assert body["data"]["candidates"][0]["freshness"] == "historical"
    assert body["data"]["message_status"] == "历史记录"
    assert body["meta"]["freshness"] == {
        "candidates": "historical",
        "messages": "historical",
        "cross_market": "unavailable",
    }


def test_aqsp_bridge_does_not_advertise_unloaded_history_without_an_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["available_dates"] = ["2026-07-14", "2026-07-11"]
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/dates")

    assert response.status_code == 200
    assert response.json()["data"]["available_dates"] == ["2026-07-14"]


def test_aqsp_bridge_rejects_index_without_an_explicit_current_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _snapshot("2026-07-14")
    historical = _snapshot("2026-07-11", symbol="600002")
    path = tmp_path / "home_dashboard_snapshot_index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "v1-index",
                "generated_at": "2026-07-14T18:00:00+08:00",
                "stale_after": "2026-07-15T18:00:00+08:00",
                "selected_date": "",
                "days": [
                    {"date": "2026-07-14", "snapshot": current},
                    {"date": "2026-07-11", "snapshot": historical},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 503


@pytest.mark.parametrize("payload_kind", ["missing", "invalid_json", "invalid_schema"])
def test_aqsp_bridge_returns_explicit_503_for_unusable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload_kind: str
) -> None:
    path = tmp_path / "snapshot.json"
    if payload_kind == "invalid_json":
        path.write_text("not-json", encoding="utf-8")
    elif payload_kind == "invalid_schema":
        path.write_text(json.dumps({"schema_version": "bad"}), encoding="utf-8")
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 503
    assert "AQSP 研究快照不可用" in response.json()["detail"]


def test_aqsp_bridge_rejects_invalid_date_and_unknown_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_single(tmp_path, _snapshot("2026-07-14"))
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    invalid_date = client.get("/api/aqsp/snapshot?date=2026/07/14")
    unknown_candidate = client.get("/api/aqsp/candidate/600999")

    assert invalid_date.status_code == 400
    assert unknown_candidate.status_code == 404


def test_aqsp_bridge_blocks_all_current_reads_when_snapshot_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["stale_after"] = "2026-07-13T18:00:00+08:00"
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    responses = [
        client.get("/api/aqsp/snapshot"),
        client.get("/api/aqsp/dates"),
        client.get("/api/aqsp/candidate/600001"),
    ]

    assert [response.status_code for response in responses] == [503, 503, 503]
    with pytest.raises(aqsp_bridge.AQSPSnapshotStale):
        aqsp_bridge.snapshot_payload()


def test_aqsp_bridge_fails_closed_when_current_snapshot_lacks_stale_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload.pop("stale_after")
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 503


def test_aqsp_bridge_allows_historical_snapshot_without_stale_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_index(tmp_path)
    index_path = tmp_path / "home_dashboard_snapshot_index.json"
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    index_payload["days"][1]["snapshot"].pop("stale_after")
    index_path.write_text(json.dumps(index_payload), encoding="utf-8")
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot?date=2026-07-11")

    assert response.status_code == 200
    assert response.json()["meta"] == {
        "historical": True,
        "stale": True,
        "freshness": {
            "candidates": "unavailable",
            "messages": "no_data",
            "cross_market": "unavailable",
        },
    }


@pytest.mark.parametrize(
    "published_at",
    ["2026-07-14T09:00:00", "2026-07-14", "not-a-timestamp"],
)
def test_aqsp_bridge_rejects_message_published_at_without_timezone_or_iso_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    published_at: str,
) -> None:
    payload = _snapshot("2026-07-14")
    payload["messages"][0]["published_at"] = published_at
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 503


def test_aqsp_bridge_normalizes_legacy_shanghai_message_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["messages"][0]["published_at"] = "2026-07-14 09:00:00"
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert response.json()["data"]["messages"][0]["published_at"] == (
        "2026-07-14T09:00:00+08:00"
    )


@pytest.mark.parametrize(
    "route",
    [
        "/api/aqsp/snapshot",
        "/api/aqsp/candidate/600001",
    ],
)
def test_aqsp_bridge_rejects_illegal_date_at_every_date_addressable_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    path = _write_single(tmp_path, _snapshot("2026-07-14"))
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get(route, params={"date": "2026/07/14"})

    assert response.status_code == 400


def test_aqsp_bridge_rejects_malformed_symbol_as_bad_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_single(tmp_path, _snapshot("2026-07-14"))
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/candidate/%20")

    assert response.status_code == 400


def test_aqsp_bridge_exposes_no_portfolio_or_chat_write_route() -> None:
    aqsp_routes = {
        route.path: set(route.methods or ())
        for route in app_module.app.routes
        if route.path.startswith("/api/aqsp")
    }

    assert aqsp_routes
    assert all(methods == {"GET"} for methods in aqsp_routes.values())
    assert not any(
        path.startswith(("/api/aqsp/portfolio", "/api/aqsp/chat"))
        for path in aqsp_routes
    )


@pytest.mark.parametrize(
    "boundary_fields",
    [
        {"advisory_only": False},
        {"deterministic_score_unchanged": False},
        {"advisory_boundary_ok": False},
        {"deterministic_score": 99.0},
    ],
)
def test_aqsp_bridge_rejects_agent_boundary_violation_without_changing_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary_fields: dict[str, object],
) -> None:
    payload = _snapshot("2026-07-14")
    payload["debates"][0].update(boundary_fields)
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 503


def test_aqsp_bridge_keeps_matching_deterministic_score_when_advisory_metadata_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    payload["debates"][0].update(
        {
            "advisory_only": True,
            "deterministic_score": 72.5,
            "deterministic_score_unchanged": True,
            "advisory_boundary_ok": True,
        }
    )
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    assert response.json()["data"]["candidates"][0]["score"] == 72.5


def test_aqsp_bridge_exposes_structured_debate_process_without_changing_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_single(tmp_path, _snapshot("2026-07-14"))
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    debate = response.json()["data"]["debates"][0]
    assert debate["active_roles"] == ["risk"]
    assert debate["round_count"] == 2
    assert debate["round_summaries"] == ["首轮提出假设", "二轮复核风险"]
    assert debate["support_points"] == ["量价同步"]
    assert debate["opposition_points"] == ["量能尚未确认"]
    assert debate["risk_warnings"] == ["高开回撤"]
    assert debate["primary_risk_gate"] == "量能承接"
    assert debate["next_trigger"] == "放量确认"
    assert debate["evidence"] == [
        {"kind": "message", "text": "公告: 产品发布"},
        {"kind": "cross_market", "text": "海外同业订单增长"},
        {"kind": "transmission", "text": "产品发布 -> 产业链订单"},
    ]
    assert debate["viewpoint_buckets"] == {
        "technical": ["均线多头"],
        "risk_counterevidence": ["量能未确认"],
    }
    assert debate["disagreement_points"] == ["看空质询看多"]
    assert debate["uncertainty_points"] == ["等待板块扩散"]
    assert debate["advisory_only"] is True
    assert debate["deterministic_score"] == 72.5
    assert debate["deterministic_score_unchanged"] is True
    assert response.json()["data"]["candidates"][0]["score"] == 72.5


def test_aqsp_bridge_does_not_invent_evidence_for_legacy_debate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _snapshot("2026-07-14")
    legacy = payload["debates"][0]
    for key in (
        "round_summaries",
        "support_points",
        "opposition_points",
        "risk_warnings",
        "real_message_evidence",
        "cross_market_evidence",
        "rule_transmission_evidence",
    ):
        legacy.pop(key, None)
    path = _write_single(tmp_path, payload)
    monkeypatch.setenv("AQSP_RESEARCH_SURFACE_SNAPSHOT", str(path))

    response = client.get("/api/aqsp/snapshot")

    assert response.status_code == 200
    debate = response.json()["data"]["debates"][0]
    assert debate["evidence"] == []
    assert debate["support_points"] == []
    assert debate["opposition_points"] == []
