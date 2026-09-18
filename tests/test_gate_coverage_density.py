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
import json
from datetime import date, timedelta
from pathlib import Path

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


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 收敛必须一路传到子进程（2026-09 修复最容易漏的一环）
# --------------------------------------------------------------------------


def _gate_args(**overrides):
    import argparse

    base = dict(
        start="2021-09-12",
        end="2026-09-11",
        grid_profile="stable_plus",
        report="/tmp/r.md",
        gate_path="/tmp/g.json",
        cache_path="/tmp/c.db",
        log="/tmp/l.log",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_command_uses_args_start():
    """`build_walkforward_command` 直接读 args.start。

    收敛是通过"把生效起点写回 args.start"实现的（见下一个测试），
    所以这里固定住这条契约：构造命令只看 args.start。
    """
    from scripts.run_production_walkforward_gate import build_walkforward_command

    command = build_walkforward_command(_gate_args(start="2023-09-05"))
    assert command[command.index("--start") + 1] == "2023-09-05"
    assert command[command.index("--end") + 1] == "2026-09-11"


def test_main_writes_clamped_start_back_to_args(tmp_path: Path, monkeypatch) -> None:
    """端到端：窗口被收敛后，子进程拿到的必须是**收敛后的**起点。

    这是本次事故修复最容易漏的一环：覆盖判定把窗口收敛了，但子进程若仍拿
    `--start 2023-01-01`（请求值），第一期训练窗口照旧落在空缺区，
    整批取数为空 → 同样崩。
    """
    import sqlite3

    import scripts.run_production_walkforward_gate as gate_mod

    db = tmp_path / "raw.db"
    # 数据只从 2024-01-02 开始；请求起点早了整整一年
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE stocks (ts_code TEXT PRIMARY KEY);
        CREATE TABLE daily_qfq (
            ts_code TEXT, trade_date TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            open_qfq REAL, high_qfq REAL, low_qfq REAL, close_qfq REAL
        );
        """
    )
    codes = [f"{600000 + i:06d}.SH" for i in range(5)]
    conn.executemany("INSERT INTO stocks (ts_code) VALUES (?)", [(c,) for c in codes])
    rows = []
    cursor = date(2024, 1, 2)
    while cursor <= date(2024, 1, 30):
        if cursor.weekday() < 5:
            for code in codes:
                rows.append(
                    (code, cursor.strftime("%Y%m%d"), 10.0, 11.0, 9.0, 10.0, 1e6, None, None, None, None)
                )
        cursor += timedelta(days=1)
    conn.executemany("INSERT INTO daily_qfq VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    seen: dict[str, object] = {}

    gate_path = tmp_path / "g.json"

    def fake_execute(*, command, env, cwd, timeout_seconds, status_path, args, coverage, effective_symbols):
        seen["start"] = args.start
        seen["command_start"] = command[command.index("--start") + 1]
        # 子进程成功时必须落下 sidecar，否则主流程会在"stamp metadata"处判失败
        gate_path.write_text(
            json.dumps(
                {
                    "run_date": "2026-06-27",
                    "deflated_sharpe": 1.1,
                    "pbo": 0.2,
                    "pbo_valid": True,
                    "dsr_pass": True,
                    "pbo_pass": True,
                    "both_pass": True,
                    "n_periods": 10,
                    "effective_symbols": 5,
                }
            ),
            encoding="utf-8",
        )
        return 0, 12345

    monkeypatch.setattr(gate_mod, "_execute_child_walkforward", fake_execute)
    monkeypatch.setattr(
        "sys.argv",
        [
            "scripts/run_production_walkforward_gate.py",
            "--db", str(db),
            "--start", "2023-01-01",
            "--end", "2024-01-30",
            "--min-symbols", "5",
            "--report", str(tmp_path / "r.md"),
            "--gate-path", str(tmp_path / "g.json"),
            "--log", str(tmp_path / "l.log"),
            "--cache-path", str(tmp_path / "c.db"),
            "--status-path", str(tmp_path / "s.json"),
            "--timeout-seconds", "30",
        ],
    )

    assert gate_mod.main() == 0
    # 请求的是 2023-01-01，数据只从 2024-01-02 起 → 必须收敛
    assert seen["start"] == "2024-01-02"
    assert seen["command_start"] == "2024-01-02"
