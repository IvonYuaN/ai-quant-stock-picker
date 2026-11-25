"""响应边界非有限浮点净化（`backend/json_safe.py` + FastAPI 接线）契约测试。

背景（#648）：starlette 的 `JSONResponse.render` 是 `json.dumps(..., allow_nan=False)`，
payload 里只要有一个 NaN / ±inf 就抛 ValueError ⇒ **整个端点 500**。
`_sectors()` 当时就是这么把 `/api/market/overview` 打挂的 —— 一个板块的涨跌幅缺失
让整页打不开。后端有 93 个端点各自拼 payload，靠逐点写对是守不住的，所以在
响应边界加了一层兜底网。

契约（本文件逐条断言）：

1. 非有限浮点 → `null`（语义 = 未知），**不是** 0、不是丢字段；
2. 干净的 payload **原样返回同一个对象**（零复制，热路径不付代价）；
3. 有限数（含 `0.0` / `-0.0` / `True` / 字符串 `"nan"`）一律不动 ——
   字符串 `"nan"` 是文本不是非数，改它才是失真；
4. 入参不被就地改写（payload 可能是被 SWR 缓存共享的只读快照）；
5. 命中必须留 warning 并带字段路径 —— 否则「本该是数字的字段变成 NaN」会被悄悄吃掉；
6. `app` 的 `default_response_class` 必须真的挂上（否则本模块是死代码）；
7. 端到端：`/api/market/overview` 在嵌套 NaN 下仍返回 200 + `null`。
"""

from __future__ import annotations

import json
import logging
import math

import pytest
from fastapi.testclient import TestClient

import app as app_module
import json_safe

NAN = float("nan")
INF = float("inf")
NEG_INF = float("-inf")


def _nested_payload() -> dict:
    """三层嵌套：dict → list → dict，模拟 `/api/market/overview` 的板块资金流。"""
    return {
        "data": {
            "as_of": "2026-11-25T15:00",
            "sectors": [
                {"name": "电池", "pct": 1.23, "net": 100.0},
                {"name": "地产", "pct": NAN, "net": -980.0},
            ],
            "ladder": [{"boards": 3, "count": 2}],
        }
    }


# --- 1. 非有限浮点 → null ---------------------------------------------------


def test_nested_nan_becomes_null():
    cleaned, found = json_safe.scrub_non_finite(_nested_payload())
    assert cleaned["data"]["sectors"][1]["pct"] is None
    assert found == ["data.sectors[1].pct"]


def test_nan_is_not_zero_and_field_is_kept():
    """🔴 关键区分：`None` 而不是 0，且字段不能消失。

    收敛成 0 会把「没取到」渲染成「持平 0.00%」；删字段会让前端连「有这个字段但没值」
    都看不出来。只有 `None` 能让 `formatSignedPct` 渲染成「—」。
    """
    cleaned, _ = json_safe.scrub_non_finite({"pct": NAN})
    assert "pct" in cleaned
    assert cleaned["pct"] is None
    assert cleaned["pct"] != 0


@pytest.mark.parametrize("bad", [NAN, INF, NEG_INF])
def test_all_non_finite_values_become_null(bad: float):
    cleaned, found = json_safe.scrub_non_finite({"v": bad})
    assert cleaned["v"] is None
    assert found == ["v"]


def test_infinity_is_scrubbed_not_just_nan():
    """原 `_events_jsonable` 只认 NaN（`value != value`），±inf 会漏过去仍然 500。"""
    cleaned, found = json_safe.scrub_non_finite([INF, NEG_INF, NAN])
    assert cleaned == [None, None, None]
    assert found == ["[0]", "[1]", "[2]"]


# --- 2. 干净 payload 零复制 -------------------------------------------------


def _clean_payload() -> dict:
    """与 `_nested_payload()` 同形状但没有非有限数（对照组的基线）。"""
    return {
        "data": {
            "as_of": "2026-11-25T15:00",
            "sectors": [
                {"name": "电池", "pct": 1.23, "net": 100.0},
                {"name": "地产", "pct": -2.4, "net": -980.0},
            ],
            "ladder": [{"boards": 3, "count": 2}],
        }
    }


def test_clean_payload_is_returned_as_the_same_object():
    """热路径不付代价：没有非有限数时不复制容器、不拼路径字符串。"""
    payload = _clean_payload()
    cleaned, found = json_safe.scrub_non_finite(payload)
    assert cleaned is payload
    assert found == []


def test_has_non_finite_is_false_for_clean_payload():
    assert json_safe.has_non_finite(_clean_payload()) is False


def test_has_non_finite_detects_deep_value():
    assert json_safe.has_non_finite({"a": [{"b": [NAN]}]}) is True


# --- 3. 有限数 / 非浮点一律不动 ---------------------------------------------


@pytest.mark.parametrize(
    "value",
    [0, 0.0, -0.0, 1, -1.5, 1e308, True, False, None, "nan", "Infinity", "", [], {}],
)
def test_finite_and_non_float_values_are_untouched(value):
    """`"nan"` 是**文本**不是非数 —— 把它改成 null 才是失真。"""
    cleaned, found = json_safe.scrub_non_finite({"v": value})
    assert found == []
    assert cleaned["v"] == value
    assert type(cleaned["v"]) is type(value)


def test_bool_is_not_treated_as_number():
    """`bool` 是 `int` 子类；若按 `numbers.Real` 判会走进非数分支。"""
    cleaned, _ = json_safe.scrub_non_finite({"ok": True})
    assert cleaned["ok"] is True


def test_tuple_stays_tuple():
    cleaned, _ = json_safe.scrub_non_finite((1.0, NAN))
    assert isinstance(cleaned, tuple)
    assert cleaned == (1.0, None)


# --- 4. 入参不被就地改写 ----------------------------------------------------


def test_input_payload_is_not_mutated():
    """payload 可能是被 SWR 缓存共享的只读快照，就地改写会让它变味。"""
    payload = _nested_payload()
    json_safe.scrub_non_finite(payload)
    assert math.isnan(payload["data"]["sectors"][1]["pct"])


# --- 5. 命中留 warning + 字段路径 -------------------------------------------


def test_render_logs_warning_with_paths(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING, logger="json_safe")
    json_safe.SafeJSONResponse(content=_nested_payload()).body
    messages = [record.getMessage() for record in caplog.records]
    assert any("data.sectors[1].pct" in message for message in messages), messages


def test_render_does_not_log_when_clean(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING, logger="json_safe")
    json_safe.SafeJSONResponse(content={"pct": 1.5, "rows": [{"net": -980.0}]}).body
    assert [r for r in caplog.records if r.name == "json_safe"] == []


def test_long_path_list_is_truncated_in_log(caplog: pytest.LogCaptureFixture):
    """一条 warning 不该刷满日志：最多点名 8 个，其余报个数。"""
    caplog.set_level(logging.WARNING, logger="json_safe")
    payload = [NAN] * 20
    json_safe.SafeJSONResponse(content=payload).body
    message = next(r.getMessage() for r in caplog.records if r.name == "json_safe")
    assert "等 20 处" in message


# --- 6. 渲染结果本身 --------------------------------------------------------


def test_render_produces_valid_json_with_null():
    """浏览器侧 `JSON.parse` 必须能解析 —— `NaN` 字面量是非法 JSON。"""
    body = json_safe.SafeJSONResponse(content=_nested_payload()).body
    parsed = json.loads(body)
    assert parsed["data"]["sectors"][1]["pct"] is None
    assert parsed["data"]["sectors"][0]["pct"] == 1.23


def test_render_keeps_chinese_unescaped():
    body = json_safe.SafeJSONResponse(content={"name": "电池"}).body
    assert "电池".encode("utf-8") in body


# --- 7. 接线 + 端到端 -------------------------------------------------------


def test_app_default_response_class_is_safe():
    """没有这条接线，`json_safe` 就只是死代码，NaN 仍然 500。

    FastAPI 把 `default_response_class` 存在 router 上（`app.get` 最终走
    `APIRouter.add_api_route`，那里用 `response_class or self.default_response_class`），
    所以断言点在 `app.router` 而不是 `app`。
    """
    assert app_module.app.router.default_response_class is json_safe.SafeJSONResponse


def test_market_overview_with_nested_nan_does_not_500(monkeypatch: pytest.MonkeyPatch):
    """#648 的原始故障场景：嵌套 NaN 不得把整个端点打成 500。"""
    monkeypatch.setattr(app_module.market, "get_overview", lambda: _nested_payload()["data"])
    response = TestClient(app_module.app).get("/api/market/overview")
    assert response.status_code == 200
    assert response.json()["data"]["sectors"][1]["pct"] is None
    assert response.json()["data"]["sectors"][0]["pct"] == 1.23


def test_events_jsonable_is_now_deep_and_covers_infinity():
    """老的 `_events_jsonable` 只遍历顶层且只认 NaN；现在复用深递归实现。"""
    event = {"ratio": NAN, "nested": {"net_amount": INF}, "rows": [{"v": NAN}], "name": "解禁"}
    cleaned = app_module._events_jsonable(event)
    assert cleaned["ratio"] is None
    assert cleaned["nested"]["net_amount"] is None
    assert cleaned["rows"][0]["v"] is None
    assert cleaned["name"] == "解禁"
