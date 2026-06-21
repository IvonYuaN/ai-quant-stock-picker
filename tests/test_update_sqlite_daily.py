from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from scripts import update_sqlite_daily


def test_update_sqlite_daily_cli_exposes_historical_backfill_flags() -> None:
    text = Path("scripts/update_sqlite_daily.py").read_text(encoding="utf-8")

    assert "--start-date" in text
    assert "--force-from-start" in text
    assert "--price-mode" in text
    assert "force_from_start=args.force_from_start" in text
    assert "price_mode=args.price_mode" in text


def test_update_sqlite_daily_requires_start_date_when_force_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "x.db"
    db.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        update_sqlite_daily, "_target_trade_day", lambda _raw: date(2026, 6, 18)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["update_sqlite_daily.py", str(db), "--force-from-start"],
    )

    with pytest.raises(SystemExit, match="--force-from-start requires --start-date"):
        update_sqlite_daily.main()


def test_update_sqlite_daily_creates_raw_database_schema_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeBaostock:
        def login(self):
            return type("Login", (), {"error_code": "0", "error_msg": ""})()

        def logout(self) -> None:
            return None

    db = tmp_path / "astocks_raw.db"
    monkeypatch.setattr(update_sqlite_daily, "_load_baostock", FakeBaostock)
    monkeypatch.setattr(update_sqlite_daily, "sync_stock_list", lambda conn, bs: [])

    summary = update_sqlite_daily.update_sqlite_daily(
        db,
        target_day=date(2026, 6, 18),
        sleep_seconds=0.0,
        limit=0,
        price_mode="raw",
    )

    assert summary.price_mode == "raw"
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master")}
    assert {"stocks", "daily_qfq"}.issubset(tables)


def test_adjustflag_for_price_mode_keeps_raw_unadjusted() -> None:
    assert update_sqlite_daily._adjustflag_for_price_mode("raw") == "1"
    assert update_sqlite_daily._adjustflag_for_price_mode("qfq") == "2"
