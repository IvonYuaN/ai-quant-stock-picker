"""复权路径的数据完整性 fail-closed 守卫。

背景：本仓 ``daily_qfq`` 表**只填了 ``close_qfq``**，``open_qfq`` / ``high_qfq`` /
``low_qfq`` 整列为 NULL（实测 100%）。而 ``sqlite_db_source.fetch_daily(..., adjust="qfq")``
会把 ``open_qfq as open`` 等直接取出来 —— 若不做数据完整性复核，就会**静默返回
open/high/low 全 NULL 的"行情"**，下游把它当真实价格使用。

本文件锁死两件事：
1. 取到数据但整批 qfq OHLC 全 NULL → **fail-closed 抛 DataError**（不静默返回 NULL）；
2. qfq OHLC 正常填充时 → **不误伤**，正常返回。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from aqsp.core.errors import DataError
from aqsp.data.sqlite_db_source import (
    _ALLOW_QFQ_SQLITE_SOURCE_ENV,
    SqliteDbSource,
)

_SYMBOLS = [("000001.SZ", "甲"), ("000002.SZ", "乙")]


def _build_db(db_path: Path, *, qfq_ohlc_filled: bool) -> None:
    """建最小库：raw OHLC 恒有值；qfq OHLC 依参数决定是否为 NULL。

    行列顺序与真实库一致：
    (trade_date, ts_code, open, high, low, close, volume, amount,
     open_qfq, high_qfq, low_qfq, close_qfq)
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE daily_qfq ("
            "trade_date TEXT, ts_code TEXT, open REAL, high REAL, low REAL,"
            " close REAL, volume REAL, amount REAL, open_qfq REAL,"
            " high_qfq REAL, low_qfq REAL, close_qfq REAL)"
        )
        conn.executemany("INSERT INTO stocks (ts_code, name) VALUES (?, ?)", _SYMBOLS)

        rows: list[tuple] = []
        for ts_code, _ in _SYMBOLS:
            cur = date(2024, 1, 2)
            for _i in range(10):
                td = cur.strftime("%Y%m%d")
                if qfq_ohlc_filled:
                    qfq = (10.0, 10.5, 9.5, 10.2)
                else:
                    # 复刻真实库：只有 close_qfq 有值，OHLC 三个 qfq 列为 NULL。
                    qfq = (None, None, None, 10.2)
                rows.append((td, ts_code, 10.0, 10.5, 9.5, 10.2, 1000.0, 10200.0, *qfq))
                cur = cur + timedelta(days=1)
        conn.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )


def _source(db_path: Path, monkeypatch: pytest.MonkeyPatch, *, allow_qfq: bool) -> SqliteDbSource:
    monkeypatch.setenv("AQSP_SQLITE_DB_PATH", str(db_path))
    if allow_qfq:
        monkeypatch.setenv(_ALLOW_QFQ_SQLITE_SOURCE_ENV, "1")
    else:
        monkeypatch.delenv(_ALLOW_QFQ_SQLITE_SOURCE_ENV, raising=False)
    return SqliteDbSource()


def test_qfq_request_raises_when_ohlc_columns_all_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本库不含前复权 OHLC → 必须 fail-closed，不得静默返回全 NULL 行情。"""
    db_path = tmp_path / "astocks_nullqfq.db"
    _build_db(db_path, qfq_ohlc_filled=False)
    source = _source(db_path, monkeypatch, allow_qfq=True)

    with pytest.raises(DataError, match="整批为 NULL"):
        source.fetch_daily(["000001"], date(2024, 1, 2), date(2024, 1, 15), "qfq")


def test_qfq_request_succeeds_when_ohlc_columns_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qfq OHLC 正常填充时不得误伤：正常返回数据。"""
    db_path = tmp_path / "astocks_okqfq.db"
    _build_db(db_path, qfq_ohlc_filled=True)
    source = _source(db_path, monkeypatch, allow_qfq=True)

    result = source.fetch_daily(["000001"], date(2024, 1, 2), date(2024, 1, 15), "qfq")

    assert set(result) == {"000001"}
    frame = result["000001"]
    assert not frame.empty
    assert frame["open"].notna().all()
    assert frame["high"].notna().all()
    assert frame["low"].notna().all()


def test_qfq_request_without_opt_in_still_raises_opt_in_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未 opt-in 时仍是原有的"须显式 opt-in"错误（本修复不改该前置守卫）。"""
    db_path = tmp_path / "astocks_okqfq2.db"
    _build_db(db_path, qfq_ohlc_filled=True)
    source = _source(db_path, monkeypatch, allow_qfq=False)

    with pytest.raises(DataError, match="opt-in"):
        source.fetch_daily(["000001"], date(2024, 1, 2), date(2024, 1, 15), "qfq")


def test_raw_path_unaffected_by_qfq_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw 路径（adjust=""）不受该守卫影响 —— 即便 qfq 列全 NULL 也正常取数。"""
    db_path = tmp_path / "astocks_raw.db"
    _build_db(db_path, qfq_ohlc_filled=False)
    source = _source(db_path, monkeypatch, allow_qfq=False)

    result = source.fetch_daily(["000001"], date(2024, 1, 2), date(2024, 1, 15))

    assert set(result) == {"000001"}
    assert result["000001"]["open"].notna().all()
