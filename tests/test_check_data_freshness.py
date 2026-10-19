"""守卫：`check_data_freshness.py` 必须能区分「数据来自日更」与「数据来自推库」。

#402 连带缺陷（freshness 假绿）：10-16 周六推库把 runner 的库换成 prod 的新库后，
freshness 打出 `[DATA FRESH] behind=0`——但日更其实从 10-14 起就没成功过。
根因：freshness 只看库内 MAX(trade_date)，无法区分数据是谁写进去的。

修法：`update_sqlite_daily` 干净成功时写 `<db>.update_stamp.json`（只记**真的**
成功）；freshness 读它，库内 MAX(trade_date) 与印章记录不一致 ⇒ 点亮
`data_source=push(...)`。本文件锁死该契约。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from scripts.data import check_data_freshness, update_sqlite_daily


def _make_db(path: Path, max_trade_date: str) -> None:
    with sqlite3.connect(path) as conn:
        update_sqlite_daily.ensure_schema(conn)
        conn.execute(
            "INSERT INTO stocks(ts_code, name) VALUES(?, ?)", ("600000.SH", "PF Bank")
        )
        conn.execute(
            """
            INSERT INTO daily_qfq(
                ts_code, trade_date, open, high, low, close_qfq, volume, amount,
                open_qfq, high_qfq, low_qfq, close
            ) VALUES(?, ?, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
            """,
            ("600000.SH", max_trade_date),
        )
        conn.commit()


def _write_stamp(db: Path, *, finished_at: str, db_max: str | None) -> None:
    db.with_name(db.name + ".update_stamp.json").write_text(
        json.dumps(
            {
                "finished_at": finished_at,
                "target_day": db_max,
                "db_max_trade_date": db_max,
                "updated_rows": 7,
                "processed": "5400/5400",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_provenance_note_flags_push_replaced_db(tmp_path: Path) -> None:
    """库内 MAX(trade_date) 比上次成功日更的记录新 ⇒ 数据是推库换来的。"""
    db = tmp_path / "astocks_raw.db"
    _write_stamp(
        db, finished_at="2026-10-16T16:00:32+08:00", db_max="2026-10-15"
    )

    note = check_data_freshness._provenance_note(db, date(2026, 10, 16))

    assert "data_source=push" in note
    assert "stamp_db_max=2026-10-15" in note
    assert "last_daily_update=2026-10-16T16:00:32+08:00" in note


def test_provenance_note_silent_when_db_matches_last_successful_update(
    tmp_path: Path,
) -> None:
    """库内 MAX(trade_date) 与印章一致 ⇒ 不提 push（正常日更路径）。"""
    db = tmp_path / "astocks_raw.db"
    _write_stamp(
        db, finished_at="2026-10-19T16:00:32+08:00", db_max="2026-10-16"
    )

    note = check_data_freshness._provenance_note(db, date(2026, 10, 16))

    assert "data_source=push" not in note
    assert "stamp_db_max=2026-10-16" in note


def test_provenance_note_reports_missing_stamp(tmp_path: Path) -> None:
    """没有印章（从未成功日更）必须显式报 NONE，不能静默当成健康。"""
    db = tmp_path / "astocks_raw.db"

    note = check_data_freshness._provenance_note(db, date(2026, 10, 16))

    assert note == " last_daily_update=NONE"


def test_freshness_reports_push_source_in_status_line(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """端到端：数据是推库换的（日更自周五后没成功）⇒ FRESH 行带 push 注记。"""
    db = tmp_path / "astocks_raw.db"
    _make_db(db, "20261016")
    _write_stamp(
        db, finished_at="2026-10-16T16:00:32+08:00", db_max="2026-10-15"
    )

    def _prev_weekday(d: date) -> date:
        cur = d - timedelta(days=1)
        while cur.weekday() >= 5:
            cur -= timedelta(days=1)
        return cur

    monkeypatch.setattr(check_data_freshness, "today_shanghai", lambda: date(2026, 10, 19))
    monkeypatch.setattr(check_data_freshness, "get_previous_trading_day", _prev_weekday)
    monkeypatch.setattr(sys, "argv", ["check_data_freshness.py", str(db)])

    exit_code = check_data_freshness.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[DATA FRESH]" in out
    assert "data_source=push" in out
    assert "last_daily_update=2026-10-16T16:00:32+08:00" in out


def test_freshness_still_fails_closed_when_db_stale(
    monkeypatch, tmp_path: Path
) -> None:
    """原有契约不回退：库内数据真陈旧时照样非零退出。"""
    db = tmp_path / "astocks_raw.db"
    _make_db(db, "20261014")  # 落后于 10-16（expected）超过 1 个交易日阈值

    def _prev_weekday(d: date) -> date:
        cur = d - timedelta(days=1)
        while cur.weekday() >= 5:
            cur -= timedelta(days=1)
        return cur

    monkeypatch.setattr(check_data_freshness, "today_shanghai", lambda: date(2026, 10, 19))
    monkeypatch.setattr(check_data_freshness, "get_previous_trading_day", _prev_weekday)
    monkeypatch.setattr(sys, "argv", ["check_data_freshness.py", str(db)])

    assert check_data_freshness.main() == 2
