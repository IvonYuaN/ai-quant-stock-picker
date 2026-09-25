"""#161 静默失效链根治：排雷层两个新数据源（股东户数 / 近期公告标题）。

覆盖：解析契约、缓存读写、分页取全/截断守卫、默认窗口/季度推导、
脏行容错。网络层全部 mock（requests 不入测试路径）。
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from aqsp.data.announcement import (
    AnnouncementItem,
    AnnouncementSource,
    _parse_page,
)
from aqsp.data.holder_num import (
    HolderNumItem,
    HolderNumSource,
    _MAX_PAGES_PER_QUARTER,
    _parse_quarter_rows,
    _recent_quarter_ends,
)


def _resp(payload: dict) -> MagicMock:
    """构造带 raise_for_status / json() 的响应 mock。"""
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.json = MagicMock(return_value=payload)
    return m


# ---------- 解析层 ----------


class TestHolderNumParse:
    def test_parse_clean_rows(self):
        rows = [
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "HOLDER_NUM": 150000,
                "END_DATE": "2026-06-30 00:00:00",
                "HOLD_NOTICE_DATE": "2026-08-27 00:00:00",
            }
        ]
        items = _parse_quarter_rows(rows)
        assert items == [
            HolderNumItem(
                symbol="600000",
                name="浦发银行",
                quarter="2026-06-30",
                holder_count=150000.0,
                notice_date="2026-08-27",
            )
        ]

    def test_parse_skips_dirty_rows(self):
        rows = [
            {"SECURITY_CODE": "", "HOLDER_NUM": 1},  # 无代码 → 跳过
            {"SECURITY_CODE": "000001"},  # 缺其他字段 → 降级 0/空
            "garbage",  # 非 dict → 跳过
        ]
        items = _parse_quarter_rows(rows)
        assert len(items) == 1
        assert items[0].symbol == "000001"
        assert items[0].holder_count == 0.0


class TestRecentQuarterEnds:
    def test_freshness_cutoff_45d(self):
        # 2026-09-25：季末 2026-06-30 距今 87 天 > 45 ⇒ 入选；
        # 季末 2026-09-30 在未来 ⇒ 不入选；2026-03-31 距今 177 天 ⇒ 入选。
        ends = _recent_quarter_ends(date(2026, 9, 25), 4)
        assert ends[0] == "2026-06-30"
        assert "2026-09-30" not in ends
        assert "2026-03-31" in ends
        assert len(ends) == 4

    def test_count_capped_by_requested(self):
        ends = _recent_quarter_ends(date(2026, 12, 15), 8)
        # 45 天界 = 2026-11-01：近两年 8 个季末（03-31/06-30/09-30/12-31 ×2）
        # 全部早于界 ⇒ 满 8 个。
        assert len(ends) == 8
        assert ends[0] == "2026-09-30"

    def test_order_new_to_old(self):
        ends = _recent_quarter_ends(date(2026, 12, 15), 3)
        assert ends == ["2026-09-30", "2026-06-30", "2026-03-31"]


class TestAnnouncementParse:
    def test_parse_codes_multi_stock(self):
        payload = {
            "data": {
                "list": [
                    {
                        "notice_date": "2026-09-24 00:00:00",
                        "title": "某公司:关于收到行政处罚事先告知书的公告",
                        "codes": [
                            {"stock_code": "600000", "short_name": "浦发银行"},
                            {"stock_code": "000001", "short_name": "平安银行"},
                        ],
                    }
                ]
            }
        }
        items = _parse_page(payload)
        assert len(items) == 2
        assert items[0] == AnnouncementItem(
            symbol="600000",
            name="浦发银行",
            notice_date="2026-09-24",
            text="某公司:关于收到行政处罚事先告知书的公告",
        )

    def test_parse_empty_and_garbage(self):
        assert _parse_page(None) == []
        assert _parse_page({"data": {}}) == []
        assert _parse_page({"data": {"list": ["garbage", None]}}) == []

    def test_parse_titleless_rows_skipped(self):
        payload = {"data": {"list": [{"codes": [{"stock_code": "600000"}]}]}}
        assert _parse_page(payload) == []


# ---------- Source 层（网络全 mock） ----------


@pytest.fixture
def fake_requests(monkeypatch):
    """把 sys.modules['requests'] 换成 fake：_fetch 内 `import requests` 直接命中。"""
    import sys

    fake_mod = MagicMock()
    monkeypatch.setitem(sys.modules, "requests", fake_mod)
    return fake_mod


class TestHolderNumSourceFetch:
    def test_fetch_paged_until_count(self, fake_requests, tmp_path, monkeypatch):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        src = HolderNumSource()
        p1 = {
            "result": {
                "count": 900,
                "data": [
                    {"SECURITY_CODE": "600000", "HOLDER_NUM": i, "END_DATE": "2026-06-30"}
                    for i in range(500)
                ],
            }
        }
        p2 = {
            "result": {
                "count": 900,
                "data": [
                    {"SECURITY_CODE": "000001", "HOLDER_NUM": i, "END_DATE": "2026-06-30"}
                    for i in range(400)
                ],
            }
        }
        fake_requests.get.side_effect = [_resp(p1), _resp(p2)]
        items = src._fetch(quarters=["2026-06-30"])
        assert len(items) == 900
        assert src.truncated is False

    def test_fetch_truncated_at_page_cap(self, fake_requests, monkeypatch):
        src = HolderNumSource()
        full = {
            "result": {
                "data": [
                    {"SECURITY_CODE": "600000", "HOLDER_NUM": i, "END_DATE": "2026-06-30"}
                    for i in range(500)
                ]
            }
        }
        fake_requests.get.side_effect = [
            _resp(full) for _ in range(_MAX_PAGES_PER_QUARTER)
        ]
        items = src._fetch(quarters=["2026-06-30"])
        assert src.truncated is True
        assert len(items) == 500 * _MAX_PAGES_PER_QUARTER

    def test_fetch_network_error_raises_data_error(self, fake_requests):
        from aqsp.core.errors import DataError

        fake_requests.get.side_effect = ConnectionError("boom")
        with pytest.raises(DataError):
            HolderNumSource()._fetch(quarters=["2026-06-30"])

    def test_cache_roundtrip_symbol_zfill(self, fake_requests, tmp_path, monkeypatch):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        src = HolderNumSource()
        fake_requests.get.side_effect = [_resp({"result": {"data": []}})]
        src.load(force=True, quarters=["2026-06-30"])
        # 写一个 4 位代码进缓存，读回应 zfill(6)
        path = tmp_path / "pit_cache" / "holder_count.csv"
        path.write_text(
            "symbol,name,quarter,holder_count,notice_date\n1,测试,2026-06-30,100,2026-08-01\n",
            encoding="utf-8",
        )
        src2 = HolderNumSource()
        items = src2.load(quarters=["2026-06-30"])
        assert items[0].symbol == "000001"


class TestAnnouncementSourceFetch:
    def test_fetch_stops_at_short_page(self, fake_requests, tmp_path, monkeypatch):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        src = AnnouncementSource()
        first = {
            "data": {
                "list": [
                    {
                        "notice_date": "2026-09-24",
                        "title": f"公告{i}",
                        "codes": [{"stock_code": "600000", "short_name": "浦发"}],
                    }
                    for i in range(100)
                ]
            }
        }
        second = {
            "data": {
                "list": [
                    {
                        "notice_date": "2026-09-25",
                        "title": f"公告{i}",
                        "codes": [{"stock_code": "000001", "short_name": "平安"}],
                    }
                    for i in range(37)
                ]
            }
        }
        fake_requests.get.side_effect = [_resp(first), _resp(second)]
        items = src._fetch("2026-09-20", "2026-09-25")
        assert len(items) == 137
        assert src.truncated is False

    def test_fetch_truncated_at_cap(self, fake_requests, monkeypatch):
        import aqsp.data.announcement as ann

        src = AnnouncementSource()
        full = {
            "data": {
                "list": [
                    {
                        "notice_date": "2026-09-24",
                        "title": "x",
                        "codes": [{"stock_code": "600000"}],
                    }
                    for _ in range(100)
                ]
            }
        }
        fake_requests.get.side_effect = [
            _resp(full) for _ in range(ann._MAX_PAGES + 1)
        ]
        src._fetch("2026-09-15", "2026-09-25")
        assert src.truncated is True

    def test_cache_roundtrip(self, fake_requests, tmp_path, monkeypatch):
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        src = AnnouncementSource()
        fake_requests.get.side_effect = [_resp({"data": {"list": []}})]
        src.load(force=True, begin_time="2026-09-20", end_time="2026-09-25")
        path = tmp_path / "pit_cache" / "announcements.csv"
        path.write_text(
            "symbol,name,notice_date,title\n"
            "7,测试,2026-09-24,某公司:涉及立案调查的公告\n",
            encoding="utf-8",
        )
        src2 = AnnouncementSource()
        items = src2.load(begin_time="2026-09-20", end_time="2026-09-25")
        assert items[0].symbol == "000007"
        assert "立案调查" in items[0].text

    def test_load_defaults_window(self, fake_requests, tmp_path, monkeypatch):
        import aqsp.data.announcement as ann

        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        monkeypatch.delenv("AQSP_ANN_WINDOW_DAYS", raising=False)
        src = AnnouncementSource()
        captured: list[dict] = []

        def capture(url, params=None, headers=None, timeout=None):
            captured.append(params or {})
            return _resp({"data": {"list": []}})

        fake_requests.get.side_effect = capture
        src.load(force=True)
        p = captured[0]
        assert p["end_time"] == ann.today_shanghai().isoformat()
        assert p["begin_time"] == (
            ann.today_shanghai() - timedelta(days=10)
        ).isoformat()
