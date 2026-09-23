"""Tests for GET /api/catalyst (news catalyst event hub endpoint).

策略：
- 直接单测 `load_catalyst_report_artifact` + `serialize_catalyst_report`（与 cli.py
  产物格式一致），构造脱敏 CatalystReport 写入临时 artifact 并断言可加载、字段正确。
- 失败降级：artifact 缺失时 endpoint 返回 200 且 events 为空。
- 若 `backend.app` 在当前环境能导入（即 fastapi 及可选依赖齐全），再跑完整路由集成
  测试；否则跳过并明确标注原因（环境缺依赖，非代码问题）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

try:
    from aqsp.news.catalysts import (
        CatalystEvent,
        CatalystReport,
        load_catalyst_report_artifact,
        serialize_catalyst_report,
    )
except Exception as exc:  # noqa: BLE001 — 环境缺依赖时优雅跳过直接单测
    CatalystEvent = CatalystReport = load_catalyst_report_artifact = (
        serialize_catalyst_report
    ) = None
    _AQSP_IMPORT_ERROR = exc
else:
    _AQSP_IMPORT_ERROR = None

# 与 cli.py run_news_catalysts --json-output 写入的相对路径保持一致。
CATALYST_ARTIFACT = "data/runtime/news_catalysts_latest.json"
# 过去的时间戳，确保 load_catalyst_report_artifact 的「未来时间」检查不误杀。
GENERATED_AT = "2026-09-20T09:30:00+08:00"


def _make_report() -> "CatalystReport":
    events = (
        CatalystEvent(
            title="某半导体公司获大基金二期增资",
            source="财联社",
            published_at="2026-09-20T08:00:00+08:00",
            symbol="688981",
            name="中芯国际",
            impact="positive",
            category="产业资本",
            summary="大基金二期拟出资参与定增，强化扩产能力",
            weight=3,
            confidence=0.72,
            source_count=2,
            verification="多源印证",
            affected_sectors=("半导体", "国产替代"),
            affected_symbols=("688981", "002049"),
            transmission_path=("资金面", "产能扩张预期", "估值重估"),
            transmission_hypothesis="增资强化扩产能力，改善中长期供给预期",
            time_horizon="中期",
        ),
    )
    return CatalystReport(
        date="2026-09-20",
        generated_at=GENERATED_AT,
        events=events,
        source_status="ok",
        warnings=(),
    )


def _write_artifact(tmp_root: Path, report: "CatalystReport") -> None:
    artifact = tmp_root / CATALYST_ARTIFACT
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _try_import_app() -> Any:
    try:
        import sys

        # TestClient 依赖 httpx（不在 [api] extra 里）；缺失时路由集成测试应跳过而非报错。
        from fastapi.testclient import TestClient  # noqa: F401

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from backend.app import app  # noqa: F401

        return app
    except Exception as exc:  # noqa: BLE001 — 缺可选依赖，路由集成测试跳过
        return exc


_APP = _try_import_app()
_ROUTE_AVAILABLE = not isinstance(_APP, Exception)


# ---------------------------------------------------------------------------
# 直接单测（不依赖 backend.app 导入）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _AQSP_IMPORT_ERROR is not None,
    reason=f"aqsp.news.catalysts 导入失败（{_AQSP_IMPORT_ERROR}），跳过直接单测",
)
def test_load_catalyst_report_when_present(tmp_path, monkeypatch):
    report = _make_report()
    _write_artifact(tmp_path, report)
    monkeypatch.setenv("AQSP_PROJECT_ROOT", str(tmp_path))

    loaded = load_catalyst_report_artifact(CATALYST_ARTIFACT)
    assert loaded is not None
    assert loaded.date == "2026-09-20"
    assert loaded.events[0].impact == "positive"
    assert loaded.events[0].affected_symbols == ("688981", "002049")
    assert loaded.events[0].transmission_path == ("资金面", "产能扩张预期", "估值重估")


@pytest.mark.skipif(
    _AQSP_IMPORT_ERROR is not None,
    reason=f"aqsp.news.catalysts 导入失败（{_AQSP_IMPORT_ERROR}），跳过直接单测",
)
def test_serialize_contains_expected_fields(tmp_path, monkeypatch):
    report = _make_report()
    _write_artifact(tmp_path, report)
    monkeypatch.setenv("AQSP_PROJECT_ROOT", str(tmp_path))

    loaded = load_catalyst_report_artifact(CATALYST_ARTIFACT)
    assert loaded is not None
    payload = serialize_catalyst_report(loaded)
    assert payload["schema_version"] == 2
    ev = payload["events"][0]
    assert ev["title"]
    assert ev["impact"] in {"positive", "negative", "neutral"}
    assert ev["affected_symbols"] == ["688981", "002049"]
    assert ev["transmission_path"] == ["资金面", "产能扩张预期", "估值重估"]


# ---------------------------------------------------------------------------
# 完整路由集成测试（依赖 backend.app 可导入；否则跳过并标注原因）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _ROUTE_AVAILABLE,
    reason=f"backend.app 导入失败（{_APP}），可选依赖缺失，跳过完整路由集成测试",
)
def test_catalyst_endpoint_serves_events(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    report = _make_report()
    _write_artifact(tmp_path, report)
    monkeypatch.setenv("AQSP_PROJECT_ROOT", str(tmp_path))

    client = TestClient(_APP)
    resp = client.get("/api/catalyst")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["events"], "有报告时应返回非空 events"
    ev = data["events"][0]
    assert ev["title"]
    assert ev["impact"] in {"positive", "negative", "neutral"}
    assert ev["affected_symbols"] == ["688981", "002049"]
    assert ev["transmission_path"] == ["资金面", "产能扩张预期", "估值重估"]


@pytest.mark.skipif(
    not _ROUTE_AVAILABLE,
    reason=f"backend.app 导入失败（{_APP}），可选依赖缺失，跳过完整路由集成测试",
)
def test_catalyst_endpoint_failsoft_when_absent(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    # 指向一个不含 artifact 的空目录，模拟报告尚未生成。
    monkeypatch.setenv("AQSP_PROJECT_ROOT", str(tmp_path))

    client = TestClient(_APP)
    resp = client.get("/api/catalyst")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["events"] == []
    assert data["generated_at"] is None
    assert data["source_status"] == "no_data"
    assert data["warnings"]


@pytest.mark.skipif(
    not _ROUTE_AVAILABLE,
    reason=f"backend.app 导入失败（{_APP}），可选依赖缺失，跳过完整路由集成测试",
)
def test_catalyst_endpoint_honors_news_json_output_env(tmp_path, monkeypatch):
    """消费端与生产端共用 AQSP_NEWS_JSON_OUTPUT。

    scripts/news_catalysts.sh 通过该 env 决定写到哪里；endpoint 必须读同一个 env，
    否则当 operator 只覆盖 AQSP_NEWS_JSON_OUTPUT（而 AQSP_PROJECT_ROOT 指向别处）时，
    生产/消费路径会分叉、事件中枢静默空数据。
    """
    from fastapi.testclient import TestClient

    # artifact 写到与 AQSP_PROJECT_ROOT 无关的目录，只有 env 能定位它。
    report = _make_report()
    artifact = tmp_path / "custom" / "catalyst.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    monkeypatch.setenv("AQSP_PROJECT_ROOT", str(empty_root))
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(artifact))

    client = TestClient(_APP)
    resp = client.get("/api/catalyst")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["events"], "应通过 AQSP_NEWS_JSON_OUTPUT 定位到 artifact"
    assert data["events"][0]["affected_symbols"] == ["688981", "002049"]
