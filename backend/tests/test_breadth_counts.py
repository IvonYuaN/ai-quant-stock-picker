"""Full-A counts require source total, every symbol and an independent clock."""
from __future__ import annotations

from datetime import timedelta

import pytest

import astock
import decision_context as dc
import market
from breadth_fixtures import complete_breadth
from test_position_action_plan import NOW


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(astock, "_now_shanghai", lambda: NOW)
    monkeypatch.setattr(market, "now_shanghai", lambda: NOW)


def _rows(changes: list, *, offset: int = 0) -> list[dict]:
    return [{"f12": f"{offset+i:06d}", "f13": 0, "f3": change,
             "f124": int(NOW.timestamp())} for i, change in enumerate(changes)]


def _patch_pages(monkeypatch, pages: list[list | None], *, total: int | None = None) -> list[int]:
    calls: list[int] = []
    seq = list(pages)
    reported = total if total is not None else sum(len(page) for page in pages if page)

    def fake_em_get(url, params=None, headers=None, timeout=15):
        calls.append(int(params["pn"]))
        assert "clist" in url
        assert params["fid"] == "f12" and params["po"] == "0"
        assert params["fields"] == "f12,f13,f3,f124"
        page = seq.pop(0) if seq else []
        if page is None:
            raise RuntimeError("page failed")

        class Response:
            def json(self):
                return {"data": {"total": reported, "diff": page}}
        return Response()

    monkeypatch.setattr(astock, "em_get", fake_em_get)
    return calls


class TestMarketBreadthCounts:
    def test_counts_up_down_flat_across_pages(self, monkeypatch) -> None:
        rows = _rows([1.0] * 1000 + [-1.0] * 100 + [0.0] * 200)
        calls = _patch_pages(monkeypatch, [rows[:1000], rows[1000:]])
        got = astock.market_breadth_counts()
        assert {key: got[key] for key in ("up", "down", "flat", "total")} == {
            "up": 1000, "down": 100, "flat": 200, "total": 1300}
        assert got["coverage_complete"] is True and got["as_of"] == NOW.isoformat()
        assert calls == [1, 2]

    def test_suspended_stocks_are_not_counted_as_flat(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [_rows(["-", 2.5, 0, "-"])])
        got = astock.market_breadth_counts()
        assert {key: got[key] for key in ("up", "down", "flat", "total")} == {
            "up": 1, "down": 0, "flat": 1, "total": 4}

    def test_page_failure_returns_none(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [None])
        assert astock.market_breadth_counts() is None

    def test_mid_page_failure_returns_none(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [_rows([1.0] * 1000), None], total=1001)
        assert astock.market_breadth_counts() is None

    def test_first_page_empty_rejects_without_retrying_heavy_source(self, monkeypatch) -> None:
        calls = _patch_pages(monkeypatch, [[], _rows([3.0])], total=1)
        assert astock.market_breadth_counts() is None
        assert calls == [1]

    def test_first_page_empty_gives_up_as_none(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [[], [], []])
        assert astock.market_breadth_counts() is None

    def test_full_page_stops_only_when_source_total_is_satisfied(self, monkeypatch) -> None:
        calls = _patch_pages(monkeypatch, [_rows([1.0] * 1000), []], total=1000)
        assert astock.market_breadth_counts()["total"] == 1000
        assert calls == [1]

    def test_short_ranked_sample_cannot_claim_full_market(self, monkeypatch) -> None:
        calls = _patch_pages(monkeypatch, [_rows([1.0] * 100)], total=5400)
        assert astock.market_breadth_counts() is None and calls == [1]

    @pytest.mark.parametrize("fault", ["missing_total", "duplicate", "missing_clock", "old_clock", "total_changes"])
    def test_invalid_source_metadata_returns_none(self, monkeypatch, fault) -> None:
        rows = _rows([1, -1])
        payload = {"data": {"total": 2, "diff": rows}}
        if fault == "missing_total":
            del payload["data"]["total"]
        elif fault == "duplicate":
            rows[1]["f12"] = rows[0]["f12"]
        elif fault == "missing_clock":
            rows[1].pop("f124")
        elif fault == "old_clock":
            rows[1]["f124"] = int((NOW - timedelta(days=1)).timestamp())
        else:
            # Exercise the shared parser's total-change rejection directly.
            from strategy_market_source import BreadthAccumulator, BreadthSourceLimits
            parser = BreadthAccumulator(BreadthSourceLimits(page_size=1, max_pages=8))
            assert parser.add_page({"data": {"total": 2, "diff": rows[:1]}}) is None
            with pytest.raises(ValueError):
                parser.add_page({"data": {"total": 3, "diff": rows[1:]}})
            return
        monkeypatch.setattr(astock, "_breadth_page", lambda pn: payload)
        assert astock.market_breadth_counts() is None

    def test_fs_uses_literal_plus_separator(self) -> None:
        assert "+" in astock._BREADTH_FS and " " not in astock._BREADTH_FS
        for prefix in ("m:0+t:6", "m:0+t:80", "m:1+t:2", "m:1+t:23", "m:0+t:81"):
            assert prefix in astock._BREADTH_FS


class TestSentimentEastmoney:
    def test_payload_uses_breadth_and_emotion_pool_counts(self, monkeypatch) -> None:
        monkeypatch.setattr(market, "_breadth_counts", lambda: complete_breadth(NOW, up=3200, down=1500, flat=210))
        got = market._sentiment(emotion={"date": "2026-10-23", "zt_count": 88, "dt_count": 6})
        assert (got["up"], got["down"], got["flat"]) == (3200, 1500, 210)
        assert got["zt"] == 88 and got["dt"] == 6
        assert got["zt_real"] == got["dt_real"] == 0
        assert got["active"] == got["speculation"] == ""
        assert got["date"] == "2026-10-23" and got["limit_pool_date"] == "2026-10-23"
        assert got["breadth"] == "偏强" and got["coverage_complete"] is True

    def test_breadth_unavailable_returns_empty(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [None])
        assert market._sentiment(emotion={"date": "2026-10-16"}) == {}

    def test_pool_failure_leaves_limit_counts_null(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [_rows([1.0] * 1000)])
        got = market._sentiment(emotion={})
        assert got["up"] == 1000 and got["zt"] is None and got["dt"] is None
        assert got["date"] == NOW.date().isoformat()

    def test_all_zero_counts_return_empty(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, [_rows(["-"] * 100)])
        assert market._sentiment(emotion={"date": "2026-10-16"}) == {}

    def test_breadth_memo_dedupes_within_window(self, monkeypatch) -> None:
        calls = _patch_pages(monkeypatch, [_rows([1.0] * 10)])
        market._breadth_counts()
        market._breadth_counts()
        assert calls == [1]

    def test_legacy_counts_keep_only_independent_limit_pool_evidence(self, monkeypatch) -> None:
        monkeypatch.setattr(market, "_breadth_counts", lambda: {"up": 100, "down": 0, "flat": 0, "total": 100})
        got = market._sentiment(emotion={"date": "2026-10-23", "zt_count": 12, "dt_count": 3})
        assert got == {"zt": 12, "dt": 3, "zt_real": 0, "dt_real": 0, "limit_pool_date": "2026-10-23"}


@pytest.mark.parametrize("pool_date", ["2026-10-16", "", None])
@pytest.mark.parametrize("cached", [True, False])
def test_current_breadth_never_labels_another_pool_day_as_current(monkeypatch, pool_date, cached) -> None:
    breadth = complete_breadth(NOW)
    original = {**breadth, "zt": 12, "dt": 3, "zt_real": 12, "dt_real": 3,
                "limit_pool_date": pool_date}
    if cached:
        got = market.sanitize_market_overview({"sentiment": original})["sentiment"]
    else:
        monkeypatch.setattr(market, "_breadth_counts", lambda: breadth)
        got = market._sentiment(emotion={"date": pool_date, "zt_count": 12, "dt_count": 3})
    assert got["zt"] is None and got["dt"] is None
    assert got["zt_real"] == got["dt_real"] == 0
    assert got["coverage_complete"] is True and got["up"] == breadth["up"]
    assert original["zt"] == 12


@pytest.mark.parametrize("mode", ["get_overview", "cached_strategy_read"])
@pytest.mark.parametrize("fault", ["legacy", "old_source"])
def test_overview_sanitizes_legacy_cache_before_market_decisions(monkeypatch, tmp_path, mode, fault) -> None:
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(market.time, "time", lambda: NOW.timestamp())
    old = {"sentiment": {"up": 100, "down": 0, "breadth": "冰点", "date": "2026-10-23", "zt": 12}, "sectors": []}
    if fault == "old_source":
        old["sentiment"] = complete_breadth(NOW - timedelta(days=1))
    market._write_cache("overview", old)
    monkeypatch.setattr(astock, "market_breadth_counts", lambda: pytest.fail("cached read must not fetch"))
    got = market.get_overview() if mode == "get_overview" else dc.load_cached_market_overview(checked_at=NOW)
    assert got["sentiment"] == {}
    assert dc._market_context(got, NOW).status == "unknown"
    assert old["sentiment"]["up"] == (100 if fault == "legacy" else 800)
