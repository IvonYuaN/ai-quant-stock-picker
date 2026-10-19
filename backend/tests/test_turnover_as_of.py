"""成交额榜的数据时间必须来自源，不能是取数时刻（W3 证据链）。

prod 实测（2026-10-19 11:27 CST，东财 clist）：

- 时间字段是 **`f124`**（逐行行情时间，比当前时间早 1–3 秒）；
- **`f86` 在 clist 上不是时间**，值形如 `11.64` / `29.02` / `"-"`；
- 而 `stock/get`（单指数）上时间在 `f86`、`f124` 恒为 0。

⇒ 字段编号不能跨端点套用。此前 `market_turnover_rank` 一个时间字段都没请求，
`get_turnover_top` 直接用 `datetime.now()` 顶上——盘中只差几秒看不出来，但盘前
（08:00 打开页面）会把**上一交易日收盘**的榜单标成 08:00，收盘后也会一直显示
当前时刻而不是 15:00。
"""

from __future__ import annotations

import pytest

import astock
import gstock
import market


class _Resp:
    def __init__(self, diff: list[dict]):
        self._diff = diff

    def json(self) -> dict:
        return {"data": {"diff": self._diff}}


def _row(code: str = "600000", f124: object = 1792380457) -> dict:
    return {
        "f12": code,
        "f14": "测试股",
        "f2": 10.5,
        "f3": 2.1,
        "f6": 1.2e9,
        "f20": 3.4e10,
        "f21": 2.1e10,
        "f100": "银行",
        "f124": f124,
    }


# --------------------------------------------------------------------------
# astock.market_turnover_rank：请求 f124 并映射成 as_of
# --------------------------------------------------------------------------


def test_turnover_rank_requests_f124_not_f86(monkeypatch) -> None:
    """clist 上的时间字段是 f124。请求 f86 拿回来的是比值，会静默变成垃圾时间。"""
    seen: dict = {}

    def fake_em_get(url, params=None, headers=None, timeout=15):
        seen["params"] = params
        return _Resp([_row()])

    monkeypatch.setattr(astock, "em_get", fake_em_get)
    astock.market_turnover_rank(5)

    fields = seen["params"]["fields"]
    assert "f124" in fields.split(",")
    assert "f86" not in fields.split(",")


def test_turnover_rank_maps_f124_to_as_of(monkeypatch) -> None:
    monkeypatch.setattr(astock, "em_get", lambda *a, **k: _Resp([_row()]))
    rows = astock.market_turnover_rank(5)

    assert rows[0]["as_of"] == astock.epoch_iso(1792380457)
    assert rows[0]["as_of"].startswith("2026-10-19T11:27:37")


@pytest.mark.parametrize("raw", [0, None, "-", "", -1, 1.5e18])
def test_turnover_rank_as_of_none_when_source_silent(monkeypatch, raw) -> None:
    """源没给（0/占位符/越界）就是 None，绝不回退成取数时刻。"""
    monkeypatch.setattr(astock, "em_get", lambda *a, **k: _Resp([_row(f124=raw)]))
    assert astock.market_turnover_rank(5)[0]["as_of"] is None


def test_turnover_rank_keeps_other_fields(monkeypatch) -> None:
    """加 as_of 不能把原有字段挤掉。"""
    monkeypatch.setattr(astock, "em_get", lambda *a, **k: _Resp([_row()]))
    row = astock.market_turnover_rank(5)[0]
    for key in ("code", "name", "price", "pct", "amount", "mcap", "float_cap", "industry"):
        assert key in row


# --------------------------------------------------------------------------
# market.get_turnover_top：updated 取源时间
# --------------------------------------------------------------------------


@pytest.fixture()
def isolated_cache(monkeypatch, tmp_path):
    """把 market 的落盘缓存挪到临时目录，避免测试间串味。"""
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path / "mc"))
    return tmp_path / "mc"


def test_turnover_top_updated_is_latest_source_time(monkeypatch, isolated_cache) -> None:
    rows = [
        {"code": "600000", "as_of": "2026-10-19T11:27:35+08:00"},
        {"code": "300308", "as_of": "2026-10-19T11:27:37+08:00"},
        {"code": "600487", "as_of": None},
    ]
    monkeypatch.setattr(astock, "market_turnover_rank", lambda n: rows)

    out = market.get_turnover_top()
    assert out["updated"] == "2026-10-19T11:27:37+08:00"


def test_turnover_top_updated_empty_when_source_silent(monkeypatch, isolated_cache) -> None:
    """源全没给时间 ⇒ 空串（前端显示「未标注」），不是 now()。"""
    rows = [{"code": "600000", "as_of": None}, {"code": "300308", "as_of": None}]
    monkeypatch.setattr(astock, "market_turnover_rank", lambda n: rows)

    out = market.get_turnover_top()
    assert out["updated"] == ""


def test_turnover_top_updated_not_now_even_before_open(monkeypatch, isolated_cache) -> None:
    """盘前场景：数据是上一交易日收盘的，标注必须是那个时刻，不能是「现在」。"""
    rows = [{"code": "600000", "as_of": "2026-10-16T15:00:00+08:00"}]
    monkeypatch.setattr(astock, "market_turnover_rank", lambda n: rows)

    out = market.get_turnover_top()
    assert out["updated"] == "2026-10-16T15:00:00+08:00"
    assert "2026-10-19" not in out["updated"]  # 今天


# --------------------------------------------------------------------------
# epoch_iso：一份实现，两个模块共用
# --------------------------------------------------------------------------


def test_gstock_reuses_astock_epoch_iso() -> None:
    """两个模块各写一份会对「0 = 未填充」这类细节产生分歧，必须是同一个函数。"""
    assert gstock._epoch_iso is astock.epoch_iso


@pytest.mark.parametrize("raw", [0, -1, None, "1792380457", [], {}])
def test_epoch_iso_rejects_non_positive_and_non_numeric(raw) -> None:
    assert astock.epoch_iso(raw) is None


def test_epoch_iso_converts_known_stamp() -> None:
    assert astock.epoch_iso(1792180784) == "2026-10-17T03:59:44+08:00"
