"""东财概念板块 fetcher 测试（字段按 2026-10-11 dataapi bkzj 实测响应，issue #333）。"""

from __future__ import annotations

import pytest

from aqsp.data.concept_board import (
    EM_BKZJ_PARAMS,
    EM_BKZJ_URL,
    ConceptBoardItem,
    ConceptBoardSource,
    _parse_items,
)
from aqsp.core.errors import DataError


def _bkzj_payload() -> dict:
    """按实测响应构造（f3/f184 为 ×100，f62 为元）。"""
    return {
        "rc": 0,
        "rt": 6,
        "data": {
            "total": 506,
            "diff": [
                {
                    "f12": "BK1172",
                    "f13": 90,
                    "f14": "AI语料",
                    "f3": 607,
                    "f62": 1628981392,
                    "f184": 815,
                    "f104": 36,
                    "f105": 1,
                },
                {
                    "f12": "BK0475",
                    "f13": 90,
                    "f14": "银行",
                    "f3": -123,
                    "f62": -285419600,
                    "f184": -606,
                    "f104": 4,
                    "f105": 40,
                },
            ],
        },
    }


def test_parse_real_bkzj_shape():
    items = _parse_items(_bkzj_payload())
    assert len(items) == 2
    it = items[0]
    assert it.board_code == "BK1172"
    assert it.board_name == "AI语料"
    assert it.up_count == 36
    assert it.down_count == 1
    # ×100 整数 → 百分数
    assert it.change_pct == pytest.approx(6.07)
    # 元 → 万元
    assert it.main_net_inflow == pytest.approx(162898.1392)
    assert it.main_net_ratio == pytest.approx(8.15)
    neg = items[1]
    assert neg.change_pct == pytest.approx(-1.23)
    assert neg.main_net_inflow == pytest.approx(-28541.96)
    assert neg.main_net_ratio == pytest.approx(-6.06)


def test_parse_skips_malformed_rows():
    payload = {
        "data": {
            "diff": [
                "junk",
                {"f12": "BK2", "f14": "n", "f3": 0, "f62": 0, "f184": 0},
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    assert items[0].board_name == "n"
    assert items[0].up_count == 0
    assert items[0].change_pct == pytest.approx(0.0)


def test_parse_empty_and_missing_fields():
    # 空响应各形态：非 dict / 无 data / diff 非列表 / 空列表
    assert _parse_items(None) == []
    assert _parse_items([]) == []
    assert _parse_items({"data": None}) == []
    assert _parse_items({"data": {"total": 0}}) == []
    assert _parse_items({"data": {"diff": []}}) == []
    # 字段缺失 → 兜底零值，不抛
    items = _parse_items({"data": {"diff": [{"f12": "BK9"}]}})
    assert len(items) == 1
    it = items[0]
    assert it.board_name == ""
    assert it.up_count == 0
    assert it.down_count == 0
    assert it.change_pct == pytest.approx(0.0)
    assert it.main_net_inflow == pytest.approx(0.0)
    assert it.main_net_ratio == pytest.approx(0.0)


def test_fetch_parses_mocked_response(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return _bkzj_payload()

    def _fake_get(url: str, params: dict | None = None, **kwargs: object):
        captured["url"] = url
        captured["params"] = params
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "get", _fake_get)
    items = ConceptBoardSource()._fetch()
    assert captured["url"] == EM_BKZJ_URL
    assert captured["params"] == EM_BKZJ_PARAMS
    assert len(items) == 2
    assert items[0].board_code == "BK1172"


def test_fetch_raises_data_error_on_request_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    import requests

    def _fake_get(*args: object, **kwargs: object):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(requests, "get", _fake_get)
    with pytest.raises(DataError, match="concept_board"):
        ConceptBoardSource()._fetch()


def test_fetch_raises_data_error_on_http_error(monkeypatch: pytest.MonkeyPatch):
    import requests

    class _Resp:
        def raise_for_status(self) -> None:
            raise requests.HTTPError("500 Server Error")

        def json(self) -> dict:  # pragma: no cover - 不可达
            return {}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(DataError, match="抓取失败"):
        ConceptBoardSource()._fetch()


def test_source_from_items():
    src = ConceptBoardSource().from_items(
        [
            ConceptBoardItem(
                board_code="BK1",
                board_name="x",
                up_count=1,
                down_count=0,
                change_pct=0.0,
                main_net_inflow=0.0,
                main_net_ratio=0.0,
            )
        ]
    )
    assert src.items(autoload=False)[0].board_code == "BK1"
