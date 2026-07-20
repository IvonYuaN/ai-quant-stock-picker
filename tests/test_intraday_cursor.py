from datetime import date

import pytest

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
    first = cursor.select(
        ["000001", "000002"], trade_date=date(2026, 7, 20), batch_size=1
    )
    cursor.commit(first, scanned_count=1)
    reset = cursor.select(
        ["000001", "000009"], trade_date=date(2026, 7, 21), batch_size=1
    )
    assert reset.offset == 0
    assert reset.symbols == ("000001",)


def test_cursor_keeps_offset_when_live_ranking_order_changes(tmp_path) -> None:
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    first = cursor.select(
        ["000003", "000001", "000002"],
        trade_date=date(2026, 7, 20),
        batch_size=1,
    )
    assert first.symbols == ("000001",)
    cursor.commit(first, scanned_count=1)

    second = cursor.select(
        ["000002", "000003", "000001"],
        trade_date=date(2026, 7, 20),
        batch_size=1,
    )
    assert second.offset == 1
    assert second.symbols == ("000002",)


def test_cursor_covers_full_large_live_universe_before_repeating(tmp_path) -> None:
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    symbols = [f"{index:06d}" for index in range(1, 5002)]
    seen: set[str] = set()

    for _ in range(78):
        batch = cursor.select(
            symbols,
            trade_date=date(2026, 7, 20),
            batch_size=64,
        )
        assert not seen.intersection(batch.symbols)
        seen.update(batch.symbols)
        cursor.commit(batch, scanned_count=len(batch.symbols))

    final_batch = cursor.select(
        symbols,
        trade_date=date(2026, 7, 20),
        batch_size=64,
    )
    seen.update(final_batch.symbols)
    assert seen == set(symbols)


def test_cursor_rejects_commit_that_did_not_scan_selected_batch(tmp_path) -> None:
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    batch = cursor.select(
        ["000001", "000002"],
        trade_date=date(2026, 7, 20),
        batch_size=2,
    )

    with pytest.raises(ValueError, match="scanned_count must equal"):
        cursor.commit(batch, scanned_count=1)


def test_cursor_caps_batch_to_universe_without_duplicate_symbols(tmp_path) -> None:
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    batch = cursor.select(
        ["000001", "000002", "000003"],
        trade_date=date(2026, 7, 20),
        batch_size=64,
    )

    assert batch.batch_size == 3
    assert batch.symbols == ("000001", "000002", "000003")
    assert len(set(batch.symbols)) == len(batch.symbols)


def test_cursor_keeps_short_final_batch_without_wrapping_to_head(tmp_path) -> None:
    cursor = IntradayUniverseCursor(tmp_path / "cursor.json")
    symbols = [f"{index:06d}" for index in range(1, 5002)]
    seen: set[str] = set()

    for _ in range(79):
        batch = cursor.select(
            symbols,
            trade_date=date(2026, 7, 20),
            batch_size=64,
        )
        assert not seen.intersection(batch.symbols)
        seen.update(batch.symbols)
        cursor.commit(batch, scanned_count=len(batch.symbols))

    assert len(seen) == len(symbols)
    assert len(batch.symbols) == 9
