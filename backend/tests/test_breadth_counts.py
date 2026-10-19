"""#417：全市场涨跌家数的东财逐只 f3 口径 —— 取代反爬的乐咕乐股。

守卫点（按失效模式排序）：
1. **绝不重复计数**：口径是全 A 逐只 f3 计数，不是行业板块 f104/f105 求和
   （后者已实测否证：层级树把同一批成分股算两遍，6064 > 5400）；
2. **分页聚合**：pz=1000 多页，短页即止；
3. **三态可区分**：取数失败 ⇒ None；`-`（停牌）不计涨跌也不计平盘；
4. **抖空重试**：首页空 diff 重试（东财 clist 约 1/4 概率抖空，#415 教训）；
5. **fs 分隔符**：必须按字面 `+`（requests 编码成 %2B 才是东财认的形态）。
"""

from __future__ import annotations

import astock
import market


def _resp(items: list) -> object:
    class _R:
        def json(self):
            return {"data": {"diff": items}}

    return _R()


def _patch_pages(monkeypatch, pages: list[list | None]) -> list[int]:
    """按序返回各页；None = 该页取数失败。记录每次请求的 pn。"""
    calls: list[int] = []
    seq = list(pages)

    def fake_em_get(url, params=None, headers=None, timeout=15):
        calls.append(int(params["pn"]))
        if "clist" not in url:
            raise AssertionError("涨跌家数只应打 clist 端点")
        page = seq.pop(0) if seq else []
        if page is None:
            raise RuntimeError("page failed")
        return _resp(page)

    monkeypatch.setattr(astock, "em_get", fake_em_get)
    return calls


class TestMarketBreadthCounts:
    def test_counts_up_down_flat_across_pages(self, monkeypatch) -> None:
        page1 = [{"f3": 1.0}] * 1000 + [{"f3": -1.0}] * 100
        page2 = [{"f3": 0.0}] * 200
        _patch_pages(monkeypatch, [page1, page2])

        got = astock.market_breadth_counts()

        assert got == {"up": 1000, "down": 100, "flat": 200, "total": 1300}
        # 短页（200 < 1000）即止，不再打第三页
        assert astock._BREADTH_PAGE_SIZE == 1000

    def test_suspended_stocks_are_not_counted_as_flat(self, monkeypatch) -> None:
        # '-'（停牌/无行情）不是「平盘」：计入 total 但不进任何一档
        _patch_pages(monkeypatch, [[{"f3": "-"}, {"f3": 2.5}, {"f3": 0}, {"f3": "-"}]])

        got = astock.market_breadth_counts()

        assert got == {"up": 1, "down": 0, "flat": 1, "total": 4}

    def test_page_failure_returns_none(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [None])

        assert astock.market_breadth_counts() is None

    def test_mid_page_failure_returns_none(self, monkeypatch) -> None:
        page1 = [{"f3": 1.0}] * 1000
        _patch_pages(monkeypatch, [page1, None])

        assert astock.market_breadth_counts() is None

    def test_first_page_empty_retries_then_recovers(self, monkeypatch) -> None:
        monkeypatch.setattr(astock, "_INDUSTRY_RETRY_SLEEP", 0)
        calls = _patch_pages(monkeypatch, [[], [{"f3": 3.0}]])

        got = astock.market_breadth_counts()

        assert got == {"up": 1, "down": 0, "flat": 0, "total": 1}
        assert calls == [1, 1]  # 首页重试的是同一页

    def test_first_page_empty_gives_up_as_none_after_retries(self, monkeypatch) -> None:
        monkeypatch.setattr(astock, "_INDUSTRY_RETRY_SLEEP", 0)
        _patch_pages(monkeypatch, [[], [], []])

        # 重试仍空 ⇒ 源侧不可用 ⇒ None（调用方据此显示「未返回」而不是 0 家上涨）
        assert astock.market_breadth_counts() is None

    def test_full_page_then_empty_page_stops_pagination(self, monkeypatch) -> None:
        page1 = [{"f3": 1.0}] * 1000
        calls = _patch_pages(monkeypatch, [page1, []])

        got = astock.market_breadth_counts()

        assert got == {"up": 1000, "down": 0, "flat": 0, "total": 1000}
        assert calls == [1, 2]

    def test_fs_uses_literal_plus_separator(self) -> None:
        """fs 分隔符必须按字面 `+`（requests → %2B；%20 会被东财当成个股口径）。"""
        assert "+" in astock._BREADTH_FS
        assert " " not in astock._BREADTH_FS
        # 五类 A 股：沪A/深A/创业板/科创/北证，缺一类就是口径残缺
        for prefix in ("m:0+t:6", "m:0+t:80", "m:1+t:2", "m:1+t:23", "m:0+t:81"):
            assert prefix in astock._BREADTH_FS


class TestSentimentEastmoney:
    """_sentiment 的换源契约：#417。"""

    def test_payload_uses_breadth_and_emotion_pool_counts(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [[{"f3": 3.0}] * 3200 + [{"f3": -1.0}] * 1500 + [{"f3": 0}] * 210])

        got = market._sentiment(
            emotion={"date": "2026-10-16", "zt_count": 88, "dt_count": 6}
        )

        assert got["up"] == 3200
        assert got["down"] == 1500
        assert got["flat"] == 210
        assert got["zt"] == 88  # 涨停池口径直接复用
        assert got["dt"] == 6
        # 乐咕专有口径不编数：zt_real/dt_real 置 0（前端回退 zt）、active/speculation 空
        assert got["zt_real"] == 0
        assert got["dt_real"] == 0
        assert got["active"] == ""
        assert got["speculation"] == ""
        assert got["date"] == "2026-10-16"
        assert got["breadth"] == "偏强"

    def test_breadth_unavailable_returns_empty(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [None])

        # 源取数失败 ⇒ 空对象（「未返回」），不是「全市场平盘」的假陈述
        assert market._sentiment(emotion={"date": "2026-10-16"}) == {}

    def test_pool_failure_leaves_limit_counts_null(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [[{"f3": 1.0}] * 3000])

        got = market._sentiment(emotion={})  # 涨停池没取到

        assert got["up"] == 3000  # 涨跌家数照常
        assert got["zt"] is None  # 涨停数不冒充 0
        assert got["dt"] is None
        assert got["date"] == ""

    def test_all_zero_counts_return_empty(self, monkeypatch) -> None:
        # 真实交易日不可能 0 家上涨 ⇒ 全 0 一律当「没数据」
        _patch_pages(monkeypatch, [[{"f3": "-"}] * 100])

        assert market._sentiment(emotion={"date": "2026-10-16"}) == {}

    def test_breadth_memo_dedupes_within_window(self, monkeypatch) -> None:
        calls = _patch_pages(monkeypatch, [[{"f3": 1.0}] * 10])

        market._breadth_counts()
        market._breadth_counts()

        assert calls == [1]  # 30s 内第二次走记忆，不再打源
