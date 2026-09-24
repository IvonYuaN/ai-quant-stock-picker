"""限售解禁 fetcher 测试（全离线：合成 payload / 假 requests，不触网）。"""

from __future__ import annotations

import sys
import types
from datetime import date, timedelta

import pytest

from aqsp.data.lockup import (
    _PAGE_SIZE,
    DEFAULT_HORIZON_DAYS,
    LockupItem,
    LockupSource,
    _is_truncated,
    _norm_day,
    _parse_items,
)

_VALID_ROW = {
    "SECURITY_CODE": "300750",
    "SECURITY_NAME_ABBR": "宁德时代",
    "FREE_DATE": "2026-09-15 00:00:00",
    "FREE_SHARES": 1234.5,
    "FREE_RATIO": 0.0264,
    "FREE_SHARES_TYPE": "首发原股东限售股份",
}


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _install_fake_requests(monkeypatch, payloads: list, captured: dict) -> None:
    """按调用顺序回放 payloads（最后一次会重复用于后续调用），零网络。"""
    fake = types.ModuleType("requests")
    calls: list[dict] = []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params})
        return _FakeResponse(payloads[min(len(calls) - 1, len(payloads) - 1)])

    fake.get = _get  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "requests", fake)
    captured["calls"] = calls


def test_parse_real_shape():
    payload = {"result": {"data": [dict(_VALID_ROW)]}}
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "300750"
    assert it.lockup_type == "首发原股东限售股份"
    assert it.ratio == pytest.approx(0.0264)


def test_parse_skips_malformed_returns_at_least_valid():
    payload = {
        "result": {
            "data": [
                {"x": 1},
                {
                    "SECURITY_CODE": "000001",
                    "SECURITY_NAME_ABBR": "ok",
                    "FREE_DATE": "2026-01-01",
                    "FREE_SHARES": 1,
                    "FREE_RATIO": 0.01,
                    "FREE_SHARES_TYPE": "定向增发机构配售股份",
                },
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 2
    assert items[1].lockup_type == "定向增发机构配售股份"


def test_source_from_items():
    src = LockupSource().from_items(
        [
            LockupItem(
                symbol="000001",
                name="x",
                plan_date="2026-01-01",
                lockup_shares=1.0,
                ratio=0.01,
                lockup_type="首发",
            )
        ]
    )
    assert src.items(autoload=False)[0].symbol == "000001"


@pytest.mark.parametrize(
    "count,expected", [(0, False), (1, False), (499, False), (500, True), (501, True)]
)
def test_is_truncated_boundaries(count, expected):
    assert _is_truncated(count, _PAGE_SIZE) is expected


def test_norm_day_rejects_garbage():
    assert _norm_day("2026-09-22") == "2026-09-22"
    assert _norm_day("2026-09-22 00:00:00") == "2026-09-22"
    assert _norm_day("") == ""
    assert _norm_day("不是日期") == ""


def test_fetch_defaults_to_bounded_forward_window(monkeypatch):
    """回归：不传窗口时必须走「今天 → 今天+90d」有界窗口，绝不能退回全集。

    历史坑：无上界 + FREE_DATE 降序 ⇒ 只拿到最远未来的 500 条
    （实测 plan_date 落在 2028-10-17 ~ 2035-10-29），前瞻预警恒空。
    """
    monkeypatch.setattr("aqsp.data.lockup.today_shanghai", lambda: date(2026, 9, 22))
    assert (date(2026, 9, 22) + timedelta(days=DEFAULT_HORIZON_DAYS)).isoformat() == (
        "2026-12-21"
    )
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    src = LockupSource()
    src._fetch()
    params = captured["calls"][0]["params"]
    assert params["filter"] == ("(FREE_DATE>='2026-09-22')(FREE_DATE<='2026-12-21')")
    assert params["sortTypes"] == "1"  # 升序：页满时丢最远而非最近
    assert params["sortColumns"] == "FREE_DATE"
    assert params["pageSize"] == str(_PAGE_SIZE)
    assert params["pageNumber"] == "1"
    assert src.truncated is False


def test_fetch_honours_explicit_window(monkeypatch):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    LockupSource()._fetch(from_date="2026-01-01", to_date="2026-03-31")
    params = captured["calls"][0]["params"]
    assert params["filter"] == ("(FREE_DATE>='2026-01-01')(FREE_DATE<='2026-03-31')")


def test_fetch_from_only_still_gets_bounded_upper(monkeypatch):
    """只给 from_date 也必须补上界，否则又退回「只拿最远未来」。"""
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    LockupSource()._fetch(from_date="2026-01-01")
    params = captured["calls"][0]["params"]
    assert params["filter"] == ("(FREE_DATE>='2026-01-01')(FREE_DATE<='2026-04-01')")


def test_fetch_illegal_from_date_falls_back_to_today(monkeypatch):
    monkeypatch.setattr("aqsp.data.lockup.today_shanghai", lambda: date(2026, 9, 22))
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    LockupSource()._fetch(from_date="不是日期")
    params = captured["calls"][0]["params"]
    assert params["filter"].startswith("(FREE_DATE>='2026-09-22')")


def test_fetch_sets_truncated_and_warns_on_full_page(monkeypatch, caplog):
    full_page = [dict(_VALID_ROW, SECURITY_CODE=f"{i:06d}") for i in range(_PAGE_SIZE)]
    captured: dict = {}
    _install_fake_requests(monkeypatch, [{"result": {"data": full_page}}], captured)
    src = LockupSource()
    with caplog.at_level("WARNING", logger="aqsp.data.lockup"):
        items = src._fetch(from_date="2026-01-01", to_date="2026-03-31")
    assert len(items) == _PAGE_SIZE
    assert src.truncated is True
    assert any("截断" in r.message for r in caplog.records)


def test_fetch_not_truncated_below_page_size(monkeypatch, caplog):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch,
        [{"result": {"data": [dict(_VALID_ROW)] * (_PAGE_SIZE - 1)}}],
        captured,
    )
    src = LockupSource()
    with caplog.at_level("WARNING", logger="aqsp.data.lockup"):
        src._fetch(from_date="2026-01-01", to_date="2026-03-31")
    assert src.truncated is False
    assert not [r for r in caplog.records if "截断" in r.message]


def test_load_passes_window_and_caches(tmp_path, monkeypatch):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    cache = tmp_path / "lockup.csv"
    src = LockupSource(cache_path=str(cache))
    items = src.load(force=True, from_date="2026-01-01", to_date="2026-03-31")
    assert len(items) == 1
    assert cache.exists()
    assert captured["calls"][0]["params"]["filter"] == (
        "(FREE_DATE>='2026-01-01')(FREE_DATE<='2026-03-31')"
    )


def test_fetch_script_reports_real_covered_span():
    """预加载脚本打印的覆盖区间必须是实际数据的 min~max（不是请求窗口）。"""
    from scripts.fetch_lockup import _span

    assert _span(["2026-09-15 00:00:00", "2026-01-05"]) == "2026-01-05 ~ 2026-09-15"
    assert _span([]) == "-"
    assert _span(["", None]) == "-"


def test_load_preserves_leading_zeros_when_cache_read_back(tmp_path, monkeypatch):
    """缓存回读必须保住股票代码的前导零。

    裸 `pd.read_csv` 会把 `symbol` 整列推断成 **int**（`"000001"` → `1`）；
    若列里还存在空值，则整列升格成 **float**（`1.0`）。两种都**静默**丢失前导零，
    后果是消费方按 `"000001"` 恒查不到、甚至把记录错配到别的股票上。
    这里锁死 `dtype={"symbol": str}`。
    """
    row = dict(_VALID_ROW)
    row["SECURITY_CODE"] = "000001"
    captured: dict = {}
    _install_fake_requests(monkeypatch, [{"result": {"data": [row]}}], captured)
    cache = tmp_path / "lockup.csv"
    LockupSource(cache_path=str(cache)).load(force=True)
    assert cache.exists()

    # 全新实例（内存为空）⇒ 走缓存回读分支，而非重新联网
    fresh = LockupSource(cache_path=str(cache))
    items = fresh.load()
    assert [it.symbol for it in items] == ["000001"]
