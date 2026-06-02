from __future__ import annotations

import struct
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from aqsp.core.errors import DataError
from aqsp.data.tdx_vipdoc_source import TdxVipdocSource


def _write_custom_day_file(
    path: Path, records: list[tuple[int, int, float, int]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for trade_date, close, amount, volume in records:
        payload.append((trade_date, close, close, close, close, amount, volume, 0))
    path.write_bytes(b"".join(struct.pack("<IIIIIfII", *record) for record in payload))


def _write_day_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        (20260527, 1000, 1050, 990, 1020, 123456.0, 100000, 0),
        (20260528, 1020, 1100, 1010, 1090, 234567.0, 200000, 0),
    ]
    path.write_bytes(b"".join(struct.pack("<IIIIIfII", *record) for record in records))


def test_tdx_vipdoc_reads_day_file_when_symbol_exists(tmp_path: Path) -> None:
    _write_day_file(tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day")

    source = TdxVipdocSource(tmp_path / "vipdoc")
    frames = source.fetch_daily(
        ["600519"],
        start=date(2026, 5, 27),
        end=date(2026, 5, 28),
    )

    df = frames["600519"]
    assert list(df["date"]) == ["2026-05-27", "2026-05-28"]
    assert df["open"].iloc[0] == 10.0
    assert df["high"].iloc[1] == 11.0
    assert df["close"].iloc[1] == 10.9
    assert df["volume"].iloc[0] == 100000
    assert df["amount"].iloc[1] == pytest.approx(234567.0)
    assert df["limit_up"].iloc[1] == pytest.approx(10.2 * 1.10)
    assert df["adj_factor"].iloc[0] == 1.0


def test_tdx_vipdoc_skips_missing_symbols(tmp_path: Path) -> None:
    (tmp_path / "vipdoc" / "sh" / "lday").mkdir(parents=True)

    source = TdxVipdocSource(tmp_path / "vipdoc")

    assert source.fetch_daily(["600519"], date(2026, 5, 27), date(2026, 5, 28)) == {}


def test_tdx_vipdoc_lists_available_symbols(tmp_path: Path) -> None:
    _write_day_file(tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day")
    _write_day_file(tmp_path / "vipdoc" / "sz" / "lday" / "sz300750.day")

    source = TdxVipdocSource(tmp_path / "vipdoc")

    assert source.get_available_symbols() == ["600519", "300750"]


def test_tdx_vipdoc_accepts_root_without_vipdoc_subdir(tmp_path: Path) -> None:
    _write_day_file(tmp_path / "sh" / "lday" / "sh600519.day")

    source = TdxVipdocSource(tmp_path)

    assert source.get_available_symbols() == ["600519"]


def test_tdx_vipdoc_rejects_adjusted_price_request(tmp_path: Path) -> None:
    _write_day_file(tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day")
    source = TdxVipdocSource(tmp_path / "vipdoc")

    with pytest.raises(DataError, match="不复权"):
        source.fetch_daily(
            ["600519"],
            start=date(2026, 5, 27),
            end=date(2026, 5, 28),
            adjust="qfq",
        )


def test_tdx_vipdoc_rejects_corrupt_day_file(tmp_path: Path) -> None:
    path = tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not-a-full-record")
    source = TdxVipdocSource(tmp_path / "vipdoc")

    with pytest.raises(DataError, match="长度异常"):
        source.fetch_daily(["600519"], date(2026, 5, 27), date(2026, 5, 28))


def test_tdx_vipdoc_liquid_symbols_only_uses_latest_market_date(tmp_path: Path) -> None:
    _write_custom_day_file(
        tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day",
        [(20260528, 1000, 9_000_000_000.0, 100_000)],
    )
    _write_custom_day_file(
        tmp_path / "vipdoc" / "sz" / "lday" / "sz000001.day",
        [(20260529, 1000, 100_000_000.0, 100_000)],
    )

    source = TdxVipdocSource(tmp_path / "vipdoc")

    assert source.get_liquid_symbols(limit=10, min_amount=50_000_000) == ["000001"]


def test_tdx_vipdoc_liquid_symbols_filters_non_common_a_share(tmp_path: Path) -> None:
    _write_custom_day_file(
        tmp_path / "vipdoc" / "sh" / "lday" / "sh510300.day",
        [(20260529, 1000, 9_000_000_000.0, 100_000)],
    )
    _write_custom_day_file(
        tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day",
        [(20260529, 1000, 100_000_000.0, 100_000)],
    )

    source = TdxVipdocSource(tmp_path / "vipdoc")

    assert source.get_liquid_symbols(limit=10, min_amount=50_000_000) == ["600519"]


def test_tdx_vipdoc_does_not_treat_sh000_index_as_stock(
    tmp_path: Path,
) -> None:
    _write_custom_day_file(
        tmp_path / "vipdoc" / "sh" / "lday" / "sh000891.day",
        [
            (20260528, 990, 90_000_000.0, 90_000),
            (20260529, 1000, 100_000_000.0, 100_000),
        ],
    )

    source = TdxVipdocSource(tmp_path / "vipdoc")
    frames = source.fetch_daily(
        ["000891"],
        start=date(2026, 5, 28),
        end=date(2026, 5, 29),
    )

    assert "000891" not in frames
    assert "000891" not in source.get_liquid_symbols(
        limit=10,
        min_amount=50_000_000,
    )


def test_tdx_vipdoc_uses_optional_sqlite_stock_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "names.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO stocks VALUES ('600519.SH', '贵州茅台\\x00')")
    monkeypatch.setenv("AQSP_STOCK_NAME_DB_PATH", str(db_path))
    _write_day_file(tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day")

    source = TdxVipdocSource(tmp_path / "vipdoc")
    frames = source.fetch_daily(
        ["600519"],
        start=date(2026, 5, 27),
        end=date(2026, 5, 28),
    )

    assert frames["600519"]["name"].iloc[0] == "贵州茅台"
