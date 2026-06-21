from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from scripts import update_sqlite_daily


def test_update_sqlite_daily_cli_exposes_historical_backfill_flags() -> None:
    text = Path("scripts/update_sqlite_daily.py").read_text(encoding="utf-8")

    assert "--start-date" in text
    assert "--force-from-start" in text
    assert "force_from_start=args.force_from_start" in text


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
