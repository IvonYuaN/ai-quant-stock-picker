"""板块资金流（`market._sectors`）的数值/文本边界：**不编数据**。

背景（2026-11-25）：原来每列都写成 `round(float(row.get(col, 0) or 0), 2)`。

- `float(nan or 0)` **兜不住 NaN** —— NaN 是真值，`nan or 0` 仍是 nan，
  `round(nan, 2)` 还是 nan，payload 里就出现了 NaN；
  而 starlette 的 `JSONResponse.render` 是 `json.dumps(..., allow_nan=False)`
  ⇒ 抛 `ValueError: Out of range float values are not JSON compliant`
  ⇒ **整个 `/api/market/overview` 500**。一个板块的涨跌幅缺失不该让整页打不开。
- `row.get(col, 0)` 在**整列缺失**时把「这一列没了」渲染成「全板块 0.00%」。
- `str(row["行业"])` 遇到 NaN 得到字面量 `"nan"`，缺名字的板块会以「nan」上榜；
  遇到整列缺失则 KeyError 冒到端点变 500。

本文件守的是：**每一种缺失都落成「显式未知」，且永不把 NaN 送进响应。**
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import market
import source_health

COLUMNS = ["行业", "行业-涨跌幅", "净额", "流入资金", "流出资金", "公司家数"]


@pytest.fixture(autouse=True)
def _reset_source_health():
    source_health.reset()
    yield
    source_health.reset()


def _patch_frame(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    class _FakeAk:
        def stock_fund_flow_industry(self, symbol: str = "即时") -> pd.DataFrame:  # noqa: ARG002
            return frame

    monkeypatch.setattr(market.astock, "_akshare", lambda: _FakeAk())


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)


def _unhealthy_sources() -> list[dict]:
    return [row for row in source_health.snapshot() if not row["healthy"]]


# ------------------------------------------------------------------ 单行缺值


def test_nan_cells_become_none_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """单行 NaN ⇒ `None`（显式未知）。**不是 0.00%**，也不是「持平」。"""
    _patch_frame(
        monkeypatch,
        _frame([
            {"行业": "半导体", "行业-涨跌幅": float("nan"), "净额": 100.0,
             "流入资金": float("nan"), "流出资金": 5.0, "公司家数": 10},
        ]),
    )
    sectors = market._sectors()
    assert len(sectors) == 1
    assert sectors[0]["pct"] is None
    assert sectors[0]["inflow"] is None
    # 有值的照旧（源是亿元，出口换成元：×1e8），且保留 2 位
    assert sectors[0]["outflow"] == 5.0 * 1e8
    assert sectors[0]["net"] == 100.0 * 1e8


def test_infinite_cell_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """inf 与 NaN 同类：JSON 也不合法（`allow_nan=False` 同样拒绝 Infinity）。"""
    _patch_frame(
        monkeypatch,
        _frame([
            {"行业": "半导体", "行业-涨跌幅": float("inf"), "净额": 1.0,
             "流入资金": 0.0, "流出资金": 0.0, "公司家数": 1},
        ]),
    )
    assert market._sectors()[0]["pct"] is None


def test_nan_net_row_is_dropped_not_ranked_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """净额不可用的行**整行不进榜**：它是排序键，编个 0 会排到中间冒充「资金持平」。"""
    _patch_frame(
        monkeypatch,
        _frame([
            {"行业": "有净额", "行业-涨跌幅": 1.0, "净额": 10.0,
             "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1},
            {"行业": "无净额", "行业-涨跌幅": 2.0, "净额": float("nan"),
             "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1},
        ]),
    )
    names = [item["name"] for item in market._sectors()]
    assert names == ["有净额"]


def test_blank_or_nan_name_row_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺名字的板块不上榜。`str(nan)` 会得到字面量 "nan"，不能让它冒充一个板块名。"""
    _patch_frame(
        monkeypatch,
        _frame([
            {"行业": "半导体", "行业-涨跌幅": 1.0, "净额": 10.0,
             "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1},
            {"行业": float("nan"), "行业-涨跌幅": 1.0, "净额": 9.0,
             "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1},
            {"行业": "   ", "行业-涨跌幅": 1.0, "净额": 8.0,
             "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1},
        ]),
    )
    names = [item["name"] for item in market._sectors()]
    assert names == ["半导体"]
    assert "nan" not in names


# ------------------------------------------------------------------ 整列缺失


def test_missing_name_column_is_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺「行业」列 ⇒ 按读失败记账并返回空（原来 `row["行业"]` 会 KeyError → 端点 500）。"""
    frame = _frame([{"行业-涨跌幅": 1.0, "净额": 10.0, "流入资金": 1.0,
                     "流出资金": 1.0, "公司家数": 1}]).drop(columns=["行业"])
    _patch_frame(monkeypatch, frame)
    assert market._sectors() == []
    unhealthy = _unhealthy_sources()
    assert unhealthy, "缺关键列必须记进 source_health"
    assert "缺列" in str(unhealthy[0].get("last_error", ""))


def test_missing_net_column_is_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺「净额」列 ⇒ 无法排序，按读失败处理（原来 `sort_values` 会 KeyError）。"""
    frame = _frame([{"行业": "半导体", "行业-涨跌幅": 1.0, "净额": 10.0,
                     "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1}]).drop(columns=["净额"])
    _patch_frame(monkeypatch, frame)
    assert market._sectors() == []
    assert _unhealthy_sources()


def test_missing_optional_column_yields_none_for_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺「行业-涨跌幅」整列 ⇒ 每行 `pct=None`，**不是全 0.00%**，行仍然照常展示。"""
    frame = _frame([{"行业": "半导体", "行业-涨跌幅": 1.0, "净额": 10.0,
                     "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1}]).drop(
        columns=["行业-涨跌幅"]
    )
    _patch_frame(monkeypatch, frame)
    sectors = market._sectors()
    assert len(sectors) == 1
    assert sectors[0]["pct"] is None


def test_missing_optional_column_does_not_mark_source_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺展示列**刻意不记「源不可达」**：源是通的，是结构变了。误诊比不诊断更糟。"""
    frame = _frame([{"行业": "半导体", "行业-涨跌幅": 1.0, "净额": 10.0,
                     "流入资金": 1.0, "流出资金": 1.0, "公司家数": 1}]).drop(
        columns=["行业-涨跌幅"]
    )
    _patch_frame(monkeypatch, frame)
    market._sectors()
    assert _unhealthy_sources() == []


# ------------------------------------------------------------------ 回归守卫


def test_payload_never_contains_nan(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 核心回归：payload 必须能过 `allow_nan=False`。

    starlette 的 `JSONResponse.render` 就是 `json.dumps(..., allow_nan=False)`，
    这里跑不过 ⇒ 线上 `/api/market/overview` 直接 500。
    """
    _patch_frame(
        monkeypatch,
        _frame([
            {"行业": "半导体", "行业-涨跌幅": float("nan"), "净额": 100.0,
             "流入资金": float("inf"), "流出资金": float("-inf"), "公司家数": 10},
        ]),
    )
    payload = {"data": {"sectors": market._sectors()}}
    # 不抛异常即通过；顺带确认没有把 NaN/Infinity 字面量写进 JSON。
    text = json.dumps(payload, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text


def test_normal_rows_keep_values_and_descending_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照组：正常数据逐字保留（含 2 位四舍五入），并按净额降序。"""
    _patch_frame(
        monkeypatch,
        _frame([
            {"行业": "小额", "行业-涨跌幅": 1.234, "净额": 5.678,
             "流入资金": 1.005, "流出资金": 2.004, "公司家数": 3},
            {"行业": "大额", "行业-涨跌幅": -2.345, "净额": 99.999,
             "流入资金": 3.0, "流出资金": 4.0, "公司家数": 4},
        ]),
    )
    sectors = market._sectors()
    assert [item["name"] for item in sectors] == ["大额", "小额"]
    # 净额 99.999 亿 → 元：`round(99.999 * 1e8, 2)`
    assert sectors[0]["net"] == round(99.999 * 1e8, 2)
    assert sectors[1]["pct"] == 1.23
    assert sectors[1]["inflow"] == round(1.005 * 1e8, 2)
    assert sectors[1]["outflow"] == round(2.004 * 1e8, 2)
    assert _unhealthy_sources() == []


def test_fetch_exception_still_records_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """对照组：取数本身抛异常 ⇒ 记错误 + 返回空（原有行为不变）。"""

    class _Boom:
        def stock_fund_flow_industry(self, symbol: str = "即时"):  # noqa: ARG002
            raise RuntimeError("同花顺挂了")

    monkeypatch.setattr(market.astock, "_akshare", lambda: _Boom())
    assert market._sectors() == []
    assert _unhealthy_sources()
