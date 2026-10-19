"""证据链（#317 W3）：行情端点必须自带**源给的**数据时间，且缺失时显式为 None。

这些测试守的是「不许拿取数时刻冒充数据时间」这条边界 —— 它是 W3 唯一
真正难的部分：加一个字段很容易，难的是保证这个字段在源没给时**不填假值**。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
import astock
import gstock


# --------------------------------------------------------------------------
# 腾讯行情：第 30 字段是成交时间，不是请求时刻
# --------------------------------------------------------------------------


def test_parse_gtimg_time_accepts_14_digit_stamp() -> None:
    assert astock.parse_gtimg_time("20261019105457") == "2026-10-19T10:54:57+08:00"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",  # 测试夹具里未赋值的字段就是 "0"
        "2026101910545",  # 13 位，短一位
        "202610191054570",  # 15 位
        "2026101910545X",  # 含非数字
        "20261319105457",  # 13 月，非法日期
        "20260230105457",  # 2 月 30 日，非法日期
        None,
        1792180784,  # 数值型（非字符串）不该被当成时间
    ],
)
def test_parse_gtimg_time_rejects_garbage(raw: object) -> None:
    assert astock.parse_gtimg_time(raw) is None


def test_parse_gtimg_carries_raw_quote_time() -> None:
    """解析层只透传原始字段，格式化交给 parse_gtimg_time —— 两者职责分开。"""
    parts = ["0"] * 55
    parts[1] = "上证指数"
    parts[3] = "3726.90"
    parts[30] = "20261019105457"
    line = 'v_sh000001="' + "~".join(parts) + '";'

    out = astock._parse_gtimg(line)
    assert out["000001"]["quote_time"] == "20261019105457"


def test_index_quote_exposes_as_of(monkeypatch) -> None:
    parts = ["0"] * 55
    parts[1] = "上证指数"
    parts[3] = "3726.90"
    parts[30] = "20261019105457"
    raw = 'v_sh000001="' + "~".join(parts) + '";'
    monkeypatch.setattr(astock, "_fetch_gtimg", lambda codes: raw)

    rows = astock.index_quote()
    assert len(rows) == 1
    assert rows[0]["as_of"] == "2026-10-19T10:54:57+08:00"


def test_index_quote_as_of_is_none_when_source_omits_it(monkeypatch) -> None:
    parts = ["0"] * 55
    parts[1] = "上证指数"
    parts[3] = "3726.90"  # parts[30] 保持 "0" ⇒ 源没给时间
    raw = 'v_sh000001="' + "~".join(parts) + '";'
    monkeypatch.setattr(astock, "_fetch_gtimg", lambda codes: raw)

    assert astock.index_quote()[0]["as_of"] is None


# --------------------------------------------------------------------------
# latest_as_of：汇总「最新」
# --------------------------------------------------------------------------


def test_latest_as_of_picks_newest_not_last() -> None:
    assert (
        astock.latest_as_of(
            ["2026-10-16T16:08:38+08:00", "2026-10-19T10:54:57+08:00", None]
        )
        == "2026-10-19T10:54:57+08:00"
    )


def test_latest_as_of_compares_by_instant_not_string() -> None:
    """偏移量写法不同时字符串比较会错：02:00Z 比 09:00+08:00 更晚（=10:00 上海）。"""
    assert astock.latest_as_of(
        ["2026-10-19T09:00:00+08:00", "2026-10-19T02:00:00+00:00"]
    ) == ("2026-10-19T02:00:00+00:00")


def test_latest_as_of_returns_none_when_nothing_usable() -> None:
    assert astock.latest_as_of([]) is None
    assert astock.latest_as_of([None, "", "not-a-time"]) is None


def test_latest_as_of_skips_unparseable_but_keeps_valid() -> None:
    assert astock.latest_as_of(["garbage", "2026-10-19T10:54:57+08:00"]) == (
        "2026-10-19T10:54:57+08:00"
    )


def test_latest_as_of_treats_naive_as_shanghai() -> None:
    """无时区的值按上海时区解释 —— 否则 naive/aware 混比会抛 TypeError。"""
    assert astock.latest_as_of(
        ["2026-10-19T10:00:00", "2026-10-19T09:00:00+08:00"]
    ) == ("2026-10-19T10:00:00")


# --------------------------------------------------------------------------
# 东财 push2：f86 是该行的行情更新时间
# --------------------------------------------------------------------------


def test_epoch_iso_converts_and_rejects_garbage() -> None:
    assert gstock._epoch_iso(1792180784) == "2026-10-17T03:59:44+08:00"
    assert gstock._epoch_iso(0) is None  # 东财未填充时给 0
    assert gstock._epoch_iso(-1) is None
    assert gstock._epoch_iso(None) is None
    assert gstock._epoch_iso("1792180784") is None  # 字符串不猜


def test_global_indices_requests_and_exposes_f86(monkeypatch) -> None:
    seen: list[str] = []

    def fake_get(secid: str, fields: str):
        seen.append(fields)
        return {"f43": 5200241, "f59": 2, "f170": 120, "f86": 1792180784}

    monkeypatch.setattr(gstock, "_push2_stock_get", fake_get)
    rows = gstock.global_indices()

    assert all("f86" in f for f in seen)  # 没有 f86 就拿不到数据时间
    assert rows and all(r["as_of"] == "2026-10-17T03:59:44+08:00" for r in rows)


def test_global_indices_as_of_none_when_f86_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        gstock,
        "_push2_stock_get",
        lambda secid, fields: {"f43": 1, "f59": 2, "f170": 0},
    )
    assert all(r["as_of"] is None for r in gstock.global_indices())


# --------------------------------------------------------------------------
# 端点契约：as_of 与 data 并列，缺失时为 None（不填假值）
# --------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app_module.app)


def test_indices_endpoint_reports_latest_as_of(client, monkeypatch) -> None:
    monkeypatch.setattr(
        app_module.astock,
        "index_quote",
        lambda: [
            {
                "name": "上证指数",
                "price": 1.0,
                "change_pct": 0.1,
                "change_amt": 1.0,
                "as_of": "2026-10-19T10:54:57+08:00",
            },
            {
                "name": "深证成指",
                "price": 2.0,
                "change_pct": 0.2,
                "change_amt": 2.0,
                "as_of": "2026-10-19T10:54:30+08:00",
            },
        ],
    )
    body = client.get("/api/indices").json()
    assert len(body["data"]) == 2
    assert body["as_of"] == "2026-10-19T10:54:57+08:00"


def test_indices_endpoint_as_of_null_when_source_silent(client, monkeypatch) -> None:
    monkeypatch.setattr(
        app_module.astock,
        "index_quote",
        lambda: [
            {
                "name": "上证指数",
                "price": 1.0,
                "change_pct": 0.1,
                "change_amt": 1.0,
                "as_of": None,
            }
        ],
    )
    body = client.get("/api/indices").json()
    assert body["as_of"] is None


def test_global_indices_endpoint_reports_latest_as_of(client, monkeypatch) -> None:
    monkeypatch.setattr(
        app_module.market,
        "get_global_indices",
        lambda: [
            {
                "key": "dji",
                "name": "道琼斯",
                "region": "美股",
                "price": 1.0,
                "change_pct": 0.5,
                "as_of": "2026-10-17T03:59:44+08:00",
            },
            {
                "key": "hsi",
                "name": "恒生指数",
                "region": "港股",
                "price": 2.0,
                "change_pct": -0.5,
                "as_of": "2026-10-16T16:08:38+08:00",
            },
        ],
    )
    body = client.get("/api/global/indices").json()
    assert body["as_of"] == "2026-10-17T03:59:44+08:00"
    assert [r["key"] for r in body["data"]] == ["dji", "hsi"]
