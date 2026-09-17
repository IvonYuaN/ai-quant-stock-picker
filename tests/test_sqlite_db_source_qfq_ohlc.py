"""复权路径的数据完整性 fail-closed 守卫。

背景（均为只读真实库 ``A股量化分析数据/astocks_raw.db`` 实测得出）：

1. ``daily_qfq`` 的 ``open_qfq`` / ``high_qfq`` / ``low_qfq`` 自 **2026-06-01** 起
   100% NULL（此前为 1.7~1.8%）；而 ``fetch_daily(..., adjust="qfq")`` 会取
   ``open_qfq as open`` 等 —— 不复核就会**静默返回全 NULL 的"行情"**。
2. 更隐蔽的一种：``*_qfq`` 列**有值、但逐字等于 raw**。实测 ``close_qfq/close`` 在
   2010-01-04 ~ 2026-09-11 的全部采样日恰为 1.000000（``min=max=1``、``std=0``），
   ``open/high/low_qfq == raw`` 亦 100%。真前复权序列不可能零方差（除权除息必然产生
   偏离）⇒ 该库的复权列是**入库管道写入的复制品**。返回它同样是把不复权价当复权价
   使用，只是从"全 NULL"变成了"非 NULL 的静默错误"。
   反证：``amount/(close*volume) ≈ 1``（各采样日 89.6%~99.1%）⇒ raw 列确为不复权价。

本文件锁死四件事：
1. 整批 qfq OHLC 全 NULL → fail-closed 抛 ``DataError``（不静默返回 NULL）；
2. qfq 列为 raw 复制品（满足行数/跨度/相同比例门槛）→ 同样 fail-closed；
3. 复制品探针**保守**：行数或跨度不足时不触发（真前复权以最新日为基期，其比值在基期
   附近天然趋近 1，短窗口无法区分，宁可漏报不可误伤）；
4. qfq OHLC 为真复权值、以及 raw 路径 → 均不误伤。
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

# raw（不复权）OHLC 常量。
_RAW_OHLC = (10.0, 10.5, 9.5, 10.2)
# 真前复权：相对 raw 有系统性偏离（比例 ≠ 1），用于"不误伤"用例。
_REAL_QFQ_OHLC = (9.10, 9.555, 8.645, 9.282)


def _build_db(
    db_path: Path,
    *,
    qfq_ohlc_filled: bool,
    n_days: int = 10,
    qfq_equals_raw: bool = False,
) -> None:
    """建最小库：raw OHLC 恒有值；qfq OHLC 依参数决定为 NULL / raw 副本 / 真复权。

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
            for _i in range(n_days):
                td = cur.strftime("%Y%m%d")
                if not qfq_ohlc_filled:
                    # 复刻真实库 2026-06-01 起：仅 close_qfq 有值，OHLC 三个 qfq 列为 NULL。
                    qfq = (None, None, None, _RAW_OHLC[3])
                elif qfq_equals_raw:
                    # 复刻"复制品"缺陷：qfq 列逐字等于 raw 列。
                    qfq = _RAW_OHLC
                else:
                    qfq = _REAL_QFQ_OHLC
                rows.append((td, ts_code, *_RAW_OHLC, 1000.0, 10200.0, *qfq))
                cur = cur + timedelta(days=1)
        conn.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )


def _source(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, *, allow_qfq: bool
) -> SqliteDbSource:
    monkeypatch.setenv("AQSP_SQLITE_DB_PATH", str(db_path))
    if allow_qfq:
        monkeypatch.setenv(_ALLOW_QFQ_SQLITE_SOURCE_ENV, "1")
    else:
        monkeypatch.delenv(_ALLOW_QFQ_SQLITE_SOURCE_ENV, raising=False)
    return SqliteDbSource()


def test_qfq_request_raises_when_ohlc_columns_all_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本库 2026-06-01 起不含前复权 OHLC → 必须 fail-closed，不得静默返回全 NULL 行情。"""
    db_path = tmp_path / "astocks_nullqfq.db"
    _build_db(db_path, qfq_ohlc_filled=False)
    source = _source(db_path, monkeypatch, allow_qfq=True)

    with pytest.raises(DataError, match="整批为 NULL"):
        source.fetch_daily(["000001"], date(2024, 1, 2), date(2024, 1, 15), "qfq")


def test_qfq_request_succeeds_when_ohlc_columns_really_adjusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真前复权值（与 raw 有系统性偏离）不得误伤：正常返回数据。"""
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
    # 取到的必须是复权值本身，而非 raw 的副本。
    assert frame["open"].iloc[0] == pytest.approx(_REAL_QFQ_OHLC[0])
    assert frame["open"].iloc[0] != _RAW_OHLC[0]


def test_qfq_request_raises_when_columns_are_verbatim_raw_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复权列是 raw 复制品（足够行数/跨度）→ 必须 fail-closed，不得当复权价返回。"""
    db_path = tmp_path / "astocks_copyqfq.db"
    _build_db(db_path, qfq_ohlc_filled=True, n_days=250, qfq_equals_raw=True)
    source = _source(db_path, monkeypatch, allow_qfq=True)

    with pytest.raises(DataError, match="逐字相同"):
        source.fetch_daily(["000001", "000002"], date(2024, 1, 2), date(2025, 1, 1), "qfq")


def test_qfq_copy_probe_stays_quiet_on_short_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探针须保守：行数/跨度不足时不触发，避免把"基期附近天然相等"误判。

    本用例的数据与上一条同样是 raw 副本，只把窗口缩到 2 票 × 10 天 —— 此时真前复权
    与 raw 在基期附近本就可能相等，无法区分，故不得报错。
    """
    db_path = tmp_path / "astocks_copyshort.db"
    _build_db(db_path, qfq_ohlc_filled=True, n_days=10, qfq_equals_raw=True)
    source = _source(db_path, monkeypatch, allow_qfq=True)

    result = source.fetch_daily(["000001"], date(2024, 1, 2), date(2024, 1, 15), "qfq")

    assert set(result) == {"000001"}
    assert result["000001"]["open"].iloc[0] == _RAW_OHLC[0]


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
