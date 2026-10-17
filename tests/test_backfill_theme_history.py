"""`scripts.data.backfill_theme_history` 单测（#406）：K 线解析 + 行集构造 + 退出码。

守卫重点：
- K 线行 8 字段（末列振幅不是涨跌幅），畸形行跳过；
- 首日（无前收）不产生涨跌幅行；除零保护；
- dry-run 不落盘；空板块全集/大面积空 K 线 ⇒ 漂移告警退出码 2；
- 硬失败（板块全集抓取失败）退出码 1。
"""

from __future__ import annotations

from pathlib import Path


from scripts.data.backfill_theme_history import (
    build_backfill_rows,
    fetch_board_universe,
    fetch_kline,
    main,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """按 URL 特征返回预置 payload；其余路径返回空 payload。"""

    def __init__(self, bkzj=None, klines=None, *, bkzj_raises=False):
        self._bkzj = bkzj if bkzj is not None else {"data": {"diff": []}}
        self._klines = klines if klines is not None else {}
        self._bkzj_raises = bkzj_raises
        self.calls: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if "bkzj" in url:
            if self._bkzj_raises:
                raise ConnectionError("boom")
            return _FakeResponse(self._bkzj)
        if "kline" in url:
            secid = (params or {}).get("secid", "")
            code = secid.split(".")[-1]
            return _FakeResponse({"data": {"klines": self._klines.get(code, [])}})
        return _FakeResponse({})


# ---------------------------------------------------------------------------
# build_backfill_rows（纯函数）
# ---------------------------------------------------------------------------


def test_build_backfill_rows_skips_first_day() -> None:
    """每板块首日无前收 ⇒ 不产生涨跌幅行。"""
    universe = {"BK0001": "板块A"}
    klines = {"BK0001": [("2026-10-12", 100.0), ("2026-10-13", 110.0)]}

    rows = build_backfill_rows(universe, klines)

    assert rows == [("2026-10-13", "板块A", 10.0)]


def test_build_backfill_rows_skips_zero_prev_close() -> None:
    """前收为 0（异常数据）跳过，不产生 inf/除零。"""
    universe = {"BK0001": "板块A"}
    klines = {"BK0001": [("2026-10-12", 0.0), ("2026-10-13", 10.0)]}

    assert build_backfill_rows(universe, klines) == []


def test_build_backfill_rows_multi_board_negative_pct() -> None:
    """多板块聚合 + 负涨跌幅正确（跌日同样入库）。"""
    universe = {"BK0001": "板块A", "BK0002": "板块B"}
    klines = {
        "BK0001": [("2026-10-12", 100.0), ("2026-10-13", 90.0)],
        "BK0002": [("2026-10-12", 50.0), ("2026-10-13", 60.0)],
    }

    rows = build_backfill_rows(universe, klines)

    assert rows == [("2026-10-13", "板块A", -10.0), ("2026-10-13", "板块B", 20.0)]


# ---------------------------------------------------------------------------
# fetch_* 解析（打桩 session）
# ---------------------------------------------------------------------------


def test_fetch_board_universe_parses_code_name() -> None:
    session = _FakeSession(
        bkzj={"data": {"diff": [{"f12": "BK0477", "f14": "酿酒概念"}, {"f12": "", "f14": "坏"}]}}
    )

    assert fetch_board_universe(session) == {"BK0477": "酿酒概念"}


def test_fetch_kline_parses_and_skips_malformed() -> None:
    session = _FakeSession(
        klines={
            "BK0477": [
                "2026-10-15,39458.47,39649.56,40548.34,39355.15,4915740,18863040237.00,3.00",
                "短行,坏",
                "2026-10-16,39581.12,40014.29,40234.61,39581.12,5265253,16320566581.00,1.65",
            ]
        }
    )

    series = fetch_kline(session, "BK0477", beg="20260101", end="20500101")

    assert series == [("2026-10-15", 39649.56), ("2026-10-16", 40014.29)]


def test_fetch_kline_normalizes_bare_date() -> None:
    """K 线首字段若是 YYYYMMDD 裸格式 ⇒ 规范化成 YYYY-MM-DD。"""
    session = _FakeSession(
        klines={"BK0001": ["20261015,1,2,3,4,5,6,7"]}
    )

    series = fetch_kline(session, "BK0001", beg="20260101", end="20500101")

    assert series == [("2026-10-15", 2.0)]


# ---------------------------------------------------------------------------
# main 退出码语义（打桩 _session + --dry-run 不落盘）
# ---------------------------------------------------------------------------


def _dry_run_argv(tmp_path: Path) -> list[str]:
    return [
        "--beg", "20260102",
        "--dry-run",
        "--out", str(tmp_path / "theme_history.csv"),
        "--interval", "0",
    ]


def test_main_dry_run_success(monkeypatch, tmp_path: Path) -> None:
    # 全集规模须 >= MIN_UNIVERSE(100)，否则触发漂移告警（rc=2 是正确行为）
    universe = {f"BK{idx:04d}": f"板块{idx}" for idx in range(105)}
    klines = {
        code: [
            "2026-10-12,100.0,100.0,101.0,99.0,1000,1e6,1.0",
            "2026-10-13,110.0,110.0,111.0,109.0,1000,1e6,1.0",
        ]
        for code in universe
    }
    session = _FakeSession(
        bkzj={"data": {"diff": [{"f12": c, "f14": n} for c, n in universe.items()]}},
        klines=klines,
    )
    monkeypatch.setattr("scripts.data.backfill_theme_history._session", lambda: session)

    rc = main(_dry_run_argv(tmp_path))

    assert rc == 0
    assert not (tmp_path / "theme_history.csv").exists()  # dry-run 不落盘


def test_main_universe_too_small_warns_but_keeps_data(monkeypatch, tmp_path: Path) -> None:
    """板块全集异常收缩 ⇒ 漂移告警退出码 2（有效数据仍落盘/可回流）。"""
    universe = {"BK0001": "板块A"}  # 1 < MIN_UNIVERSE
    klines = {"BK0001": ["2026-10-12,100.0,100.0,101.0,99.0,1000,1e6,1.0",
                         "2026-10-13,110.0,110.0,111.0,109.0,1000,1e6,1.0"]}
    session = _FakeSession(
        bkzj={"data": {"diff": [{"f12": c, "f14": n} for c, n in universe.items()]}},
        klines=klines,
    )
    monkeypatch.setattr("scripts.data.backfill_theme_history._session", lambda: session)

    rc = main(_dry_run_argv(tmp_path))

    assert rc == 2


def test_main_empty_klines_ratio_warns(monkeypatch, tmp_path: Path) -> None:
    """大面积空 K 线 ⇒ 漂移告警退出码 2。"""
    universe = {f"BK{idx:04d}": f"板块{idx}" for idx in range(5)}
    klines = {"BK0000": ["2026-10-12,100.0,100.0,101.0,99.0,1000,1e6,1.0",
                         "2026-10-13,110.0,110.0,111.0,109.0,1000,1e6,1.0"]}  # 1/5 有数据
    session = _FakeSession(
        bkzj={"data": {"diff": [{"f12": c, "f14": n} for c, n in universe.items()]}},
        klines=klines,
    )
    monkeypatch.setattr("scripts.data.backfill_theme_history._session", lambda: session)

    rc = main(_dry_run_argv(tmp_path))

    assert rc == 2  # 空占比 80% > MAX_EMPTY_RATIO


def test_main_universe_fetch_failure_returns_1(monkeypatch, tmp_path: Path) -> None:
    session = _FakeSession(bkzj_raises=True)
    monkeypatch.setattr("scripts.data.backfill_theme_history._session", lambda: session)

    rc = main(_dry_run_argv(tmp_path))

    assert rc == 1


def test_main_real_write_goes_through_append(monkeypatch, tmp_path: Path) -> None:
    """非 dry-run：行集真的经 append_backfill_rows 落盘（NaN 语义保留）。"""

    from aqsp.data.theme_history import load_board_history

    universe = {"BK0001": "板块A", "BK0002": "板块B"}
    klines = {
        code: [
            "2026-10-12,100.0,100.0,101.0,99.0,1000,1e6,1.0",
            "2026-10-13,110.0,110.0,111.0,109.0,1000,1e6,1.0",
        ]
        for code in universe
    }
    session = _FakeSession(
        bkzj={"data": {"diff": [{"f12": c, "f14": n} for c, n in universe.items()]}},
        klines=klines,
    )
    monkeypatch.setattr("scripts.data.backfill_theme_history._session", lambda: session)

    rc = main(
        [
            "--beg", "20260102",
            "--out", str(tmp_path / "theme_history.csv"),
            "--interval", "0",
            "--force",
        ]
    )

    assert rc == 2  # 板块全集 2 < MIN_UNIVERSE ⇒ 漂移告警，但数据已落盘
    frame = load_board_history(path=tmp_path / "theme_history.csv")
    assert len(frame) == 2
    assert frame["change_pct"].notna().all()
    assert frame["up_count"].isna().all()
