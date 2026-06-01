from __future__ import annotations

from datetime import date
import json
import tempfile


from aqsp.universe.t1_filter import (
    _previous_trading_day,
    get_yesterday_buys,
    filter_t1_held,
)


def test_previous_trading_day_skip_weekend():
    """周一 -> 周五"""
    monday = date(2026, 5, 25)
    assert _previous_trading_day(monday) == date(2026, 5, 22)


def test_previous_trading_day_within_week():
    """周三 -> 周二"""
    wednesday = date(2026, 5, 27)
    assert _previous_trading_day(wednesday) == date(2026, 5, 26)


def test_previous_trading_day_uses_trade_calendar_when_holiday(monkeypatch):
    monkeypatch.setattr(
        "aqsp.universe.t1_filter.resolve_previous_trading_day",
        lambda d: date(2026, 9, 30),
    )
    assert _previous_trading_day(date(2026, 10, 8)) == date(2026, 9, 30)


def test_get_yesterday_buys_empty_ledger():
    """空文件返回空集合"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("")
        f.flush()
        result = get_yesterday_buys(f.name, date(2026, 5, 27))
        assert result == set()


def test_get_yesterday_buys_returns_signals(tmp_path):
    """返回昨日 signal_date 匹配且可交易的 symbol"""
    ledger_path = tmp_path / "ledger.jsonl"
    records = [
        {
            "signal_date": "2026-05-26",
            "symbol": "600519",
            "status": "pending",
            "rating": "buy_candidate",
        },
        {
            "signal_date": "2026-05-26",
            "symbol": "000858",
            "status": "pending",
            "rating": "watch",
        },
        {
            "signal_date": "2026-05-25",
            "symbol": "000001",
            "status": "pending",
            "rating": "buy_candidate",
        },
    ]
    ledger_path.write_text("\n".join(json.dumps(r) for r in records))
    result = get_yesterday_buys(ledger_path, date(2026, 5, 27))
    assert result == {"600519"}


def test_get_yesterday_buys_ignores_non_tradable_signals(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    records = [
        {
            "signal_date": "2026-05-26",
            "symbol": "600519",
            "status": "pending",
            "rating": "avoid",
        },
        {
            "signal_date": "2026-05-26",
            "symbol": "000001",
            "status": "pending",
            "rating": "watch",
        },
        {
            "signal_date": "2026-05-26",
            "symbol": "000858",
            "status": "pending",
            "rating": "buy_candidate",
        },
    ]
    ledger_path.write_text("\n".join(json.dumps(r) for r in records))

    result = get_yesterday_buys(ledger_path, date(2026, 5, 27))

    assert result == {"000858"}


def test_filter_t1_held_removes_yesterday_signals(tmp_path):
    """剔除昨日有信号的 symbol"""
    ledger_path = tmp_path / "ledger.jsonl"
    records = [
        {
            "signal_date": "2026-05-26",
            "symbol": "600519",
            "status": "pending",
            "rating": "buy_candidate",
        },
        {
            "signal_date": "2026-05-26",
            "symbol": "000858",
            "status": "pending",
            "rating": "watch",
        },
    ]
    ledger_path.write_text("\n".join(json.dumps(r) for r in records))
    candidates = ["600519", "000858", "000001", "002475"]
    kept, removed = filter_t1_held(candidates, ledger_path, date(2026, 5, 27))
    assert kept == ["000858", "000001", "002475"]
    assert removed == ["600519"]


def test_filter_t1_held_skips_invalid_lines(tmp_path):
    """优雅处理格式错误的 JSON"""
    ledger_path = tmp_path / "ledger.jsonl"
    content = """{"signal_date": "2026-05-26", "symbol": "600519", "status": "pending", "rating": "buy_candidate"}
invalid json line
{"signal_date": "2026-05-26", "symbol": "000858", "status": "pending", "rating": "watch"}
"""
    ledger_path.write_text(content)
    candidates = ["600519", "000858", "000001"]
    kept, removed = filter_t1_held(candidates, ledger_path, date(2026, 5, 27))
    assert kept == ["000858", "000001"]
    assert removed == ["600519"]


def test_filter_t1_held_no_ledger():
    """ledger 文件不存在时返回所有 candidates"""
    candidates = ["600519", "000858"]
    kept, removed = filter_t1_held(candidates, "/tmp/nonexistent.jsonl", date(2026, 5, 27))
    assert kept == candidates
    assert removed == []


def test_get_yesterday_buys_deduplicates(tmp_path):
    """相同 symbol 多条记录只返回一个"""
    ledger_path = tmp_path / "ledger.jsonl"
    records = [
        {
            "signal_date": "2026-05-26",
            "symbol": "600519",
            "status": "pending",
            "rating": "buy_candidate",
        },
        {
            "signal_date": "2026-05-26",
            "symbol": "600519",
            "status": "executed",
            "rating": "buy_candidate",
        },
    ]
    ledger_path.write_text("\n".join(json.dumps(r) for r in records))
    result = get_yesterday_buys(ledger_path, date(2026, 5, 27))
    assert result == {"600519"}
