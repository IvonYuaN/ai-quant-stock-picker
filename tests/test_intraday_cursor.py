from datetime import date

from aqsp.universe.intraday_cursor import IntradayUniverseCursor


def test_cursor_rotates_batches_and_commits_only_after_success(tmp_path) -> None:
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    symbols = ["000001", "000002", "000003", "000004", "000005"]
    first = cursor.select(symbols, trade_date=date(2026, 7, 20), batch_size=2)
    assert first.symbols == ("000001", "000002")
    cursor.commit(first, scanned_count=2)
    assert '"last_batch_id": "2026-07-20:1:0"' in (tmp_path / "cursor.json").read_text()

    second = cursor.select(symbols, trade_date=date(2026, 7, 20), batch_size=2)
    assert second.symbols == ("000003", "000004")
    cursor.fail(second, "timeout")
    retry = cursor.select(symbols, trade_date=date(2026, 7, 20), batch_size=2)
    assert retry.offset == second.offset
    assert retry.symbols == second.symbols


def test_cursor_resets_when_trade_date_or_universe_changes(tmp_path) -> None:
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    first = cursor.select(["000001", "000002"], trade_date=date(2026, 7, 20), batch_size=1)
    cursor.commit(first, scanned_count=1)
    reset = cursor.select(["000001", "000009"], trade_date=date(2026, 7, 21), batch_size=1)
    assert reset.offset == 0
    assert reset.symbols == ("000001",)
