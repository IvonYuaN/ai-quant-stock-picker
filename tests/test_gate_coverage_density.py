"""生产 gate 覆盖判定的回归测试。

**这是 2026-09 事故的回归护栏。**

事故经过：gate 配了 5 年窗口（2021-09-12 起），而原始库真实数据只从 2023-09-05
起才密集。旧校验只看 `MIN(trade_date)` / `MAX(trade_date)`，几百行零散残留就让
5 年窗口"看起来有覆盖"。gate 跑到第 1/36 期，训练窗口落在空缺区，整批取数为空，
抛 DataError 退出 —— **连挂 6 天没人发现**（monitor 对外恒返回 0）。

本测试用一个复现该形态的合成库，验证：
1. 覆盖判定会把窗口起点**收敛**到实际可用处，并如实记录；
2. 整窗都不可用时**快速失败**，而不是硬跑 36 期。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts.run_production_walkforward_gate import (
    inspect_raw_coverage_window_with_symbols,
)

# 原始库的 raw-only 约定：qfq 列全 NULL，price_mode() 才判定为 "raw"。
_SCHEMA = """
CREATE TABLE stocks (ts_code TEXT PRIMARY KEY);
CREATE TABLE daily_qfq (
    ts_code TEXT, trade_date TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    open_qfq REAL, high_qfq REAL, low_qfq REAL, close_qfq REAL
);
"""


def _trade_days(start: str, end: str) -> list[str]:
    """按工作日近似交易日（测试用，不依赖真实日历）。"""
    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    out: list[str] = []
    while cursor <= last:
        if cursor.weekday() < 5:
            out.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return out


def _build_db(path: Path, *, dense_start: str, symbols: int = 20) -> None:
    """复现事故形态：dense_start 之前几乎没数据，之后全市场齐备。"""
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    codes = [f"{600000 + index:06d}.SH" for index in range(symbols)]
    conn.executemany("INSERT INTO stocks (ts_code) VALUES (?)", [(code,) for code in codes])
    rows = []
    for day in _trade_days("20210913", "20230901"):
        if day >= dense_start:
            continue
        # 空缺期只有极少数零散行（模拟残留）
        if day.endswith("13"):
            rows.append((codes[0], day, 10.0, 10.5, 9.8, 10.2, 100.0, None, None, None, None))
    for day in _trade_days(dense_start, "20230901"):
        for code in codes:
            rows.append((code, day, 10.0, 10.5, 9.8, 10.2, 100.0, None, None, None, None))
    conn.executemany(
        "INSERT INTO daily_qfq VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


def _inspect(path: Path, **overrides):
    params = dict(
        requested_start="2021-09-13",
        requested_end="2023-09-01",
        coverage_start="2021-09-13",
        coverage_end="2023-09-01",
        coverage_mode="auto_recent_window",
        lookback_years=5,
        listing_aware=True,
    )
    params.update(overrides)
    return inspect_raw_coverage_window_with_symbols(path, **params)


def test_window_is_clamped_to_dense_start(tmp_path: Path):
    """窗口起点超出数据覆盖时，必须收敛到实际可用起点。"""
    db = tmp_path / "raw.db"
    _build_db(db, dense_start="20230601")

    inspection = _inspect(db)
    summary = inspection.summary

    assert summary.window_clamped is True
    assert summary.requested_window_start == "2021-09-13"
    assert summary.coverage_window_start == "2023-06-01"
    # first_trade_date 沿用库里的紧凑格式；窗口字段对外是 ISO
    assert summary.first_trade_date == "20230601"
    # 收敛这件事必须写进产物，让结论带着真实窗口
    assert "早于数据覆盖" in summary.density_note
    assert "实际可用起点为 2023-06-01" in summary.density_note
    assert summary.sparse_spans, "缺口区间必须被记录"


def test_window_untouched_when_data_is_dense(tmp_path: Path):
    """数据本就密集时不该动窗口 —— 不能"修"出一个更短的窗口。"""
    db = tmp_path / "raw.db"
    _build_db(db, dense_start="20210913")

    summary = _inspect(db).summary
    assert summary.window_clamped is False
    assert summary.coverage_window_start == "2021-09-13"
    assert summary.density_note.startswith("窗口 2021-09-13~2023-09-01 数据密集")


def test_fully_empty_window_is_flagged_not_raised(tmp_path: Path):
    """整窗不可用时**标记**而非抛错。

    分层：inspect_* 是只读检查 API，只报告；是否放弃跑批由主流程决定。
    在检查函数里 raise 会让所有小样本调用方（测试、诊断脚本）一起炸 ——
    这是实现过程中真实踩到的坑。
    """
    db = tmp_path / "raw.db"
    _build_db(db, dense_start="20230601")

    # 取一段**完全早于任何数据**的窗口（数据从 2021-09-13 起）
    summary = _inspect(db, coverage_start="2021-01-01", coverage_end="2021-06-30").summary
    assert summary.density_ok is False
    assert "没有任何一段密集区间" in summary.density_note
    # 不可用时不得"修"出更短的窗口
    assert summary.window_clamped is False


def test_sparse_spans_report_the_gap(tmp_path: Path):
    """缺口区间要能说清"缺了哪一段"，否则用户无从下手。"""
    db = tmp_path / "raw.db"
    _build_db(db, dense_start="20230601")

    spans = _inspect(db).summary.sparse_spans
    assert len(spans) >= 1
    first = spans[0]
    assert first["start"] == "20210913"
    assert first["end"] < "20230601"
    assert int(first["trade_days"]) > 0


def test_coverage_payload_carries_density_fields(tmp_path: Path):
    """状态文件必须带上密度结论，供人事后追溯（事故时缺的正是这些）。"""
    from scripts.run_production_walkforward_gate import _coverage_payload_from_summary

    db = tmp_path / "raw.db"
    _build_db(db, dense_start="20230601")

    payload = _coverage_payload_from_summary(_inspect(db).summary)
    assert payload["window_clamped"] is True
    assert payload["requested_window_start"] == "2021-09-13"
    assert payload["coverage_window_start"] == "2023-06-01"
    assert payload["density_threshold"] > 0
    assert payload["density_note"]
    assert isinstance(payload["sparse_spans"], list)
