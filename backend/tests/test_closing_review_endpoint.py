"""AQSP 收评日报 6 段聚合端点契约测试（GET /api/aqsp/closing-review）。

市场级只读聚合端点：复用 `aqsp.briefing.closing_review.build_*_section()`
读全市场 pit_cache（默认走 runtime_data_root）。契约要点：

- 固定返回 6 个段，顺序与 key 稳定（前端卡片流依赖）。
- fail-soft：任一段缺数据/读失败 ⇒ 该段 available=False、markdown 空，
  整端点仍 200，绝不 500（与各 builder 的红线同级别）。
- 红线：只读，端点内绝不写回打分/排序/下单。

测试通过 monkeypatch 注入 builder 函数到 `aqsp.briefing.closing_review`
模块命名空间（端点是函数体内 `from ... import`，patch 模块属性即生效），
不依赖真实 pit_cache 文件，故在 CI/本地均确定性通过。
"""

from __future__ import annotations

from typing import Callable

import pytest
from fastapi.testclient import TestClient

import aqsp.briefing.closing_review as cr
import app as app_module

ROUTE = "/api/aqsp/closing-review"

EXPECTED_KEYS = [
    "factor_ic",
    "board_fund",
    "longhubang",
    "news",
    "announcements",
    "holder_concentration",
]


def _make_client() -> TestClient:
    return TestClient(app_module.app)


def test_closing_review_returns_six_sections_in_stable_order(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """6 段全有数据时：顺序、key、标题、available 全部正确。"""
    titles = {
        "factor_ic": "因子 IC 健康",
        "board_fund": "板块资金面",
        "longhubang": "龙虎榜关注",
        "news": "财经快讯",
        "announcements": "重大公告",
        "holder_concentration": "股东户数异动",
    }

    # 逐个 patch 6 个 builder 为「有数据」版本
    def _stub(key: str) -> Callable[[], str]:
        def _builder() -> str:
            return f"## {titles[key]}（test）\n证据行"

        return _builder

    for key in EXPECTED_KEYS:
        monkeypatch.setattr(cr, f"build_{key}_section", _stub(key))

    response = _make_client().get(ROUTE)
    assert response.status_code == 200
    data = response.json()["data"]

    keys = [s["key"] for s in data["sections"]]
    assert keys == EXPECTED_KEYS
    for s in data["sections"]:
        assert s["available"] is True
        assert s["markdown"].startswith("## ")
    assert data["as_of"]  # ISO 时间戳（北京时区）


def test_closing_review_fail_soft_when_a_section_is_empty(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """某段 builder 返回空串 ⇒ 该段 available=False、markdown 空，整端点仍 200。"""
    # 指向不存在的 runtime root ⇒ 所有读 pit_cache 的 builder 都拿不到文件，
    # 返回空串（降级安全），验证 fail-soft 路径。
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", "/nonexistent-aqsp-test-root")

    response = _make_client().get(ROUTE)
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["sections"]) == 6
    for s in data["sections"]:
        assert s["markdown"] == ""
        assert s["available"] is False


def test_closing_review_fail_soft_when_builder_raises(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """某段 builder 抛异常 ⇒ 端点捕获，该段落空，仍 200，绝不 500。"""
    monkeypatch.setattr(cr, "build_news_section", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    response = _make_client().get(ROUTE)
    assert response.status_code == 200
    data = response.json()["data"]
    news = next(s for s in data["sections"] if s["key"] == "news")
    assert news["markdown"] == ""
    assert news["available"] is False


def test_closing_review_endpoint_is_read_only() -> None:
    """端点只暴露 GET（只读红线，不出现写方法）。"""
    methods = [
        set(m for m in route.methods if m != "HEAD")
        for route in app_module.app.routes
        if getattr(route, "path", "") == ROUTE
    ]
    assert methods
    assert all(m == {"GET"} for m in methods)
