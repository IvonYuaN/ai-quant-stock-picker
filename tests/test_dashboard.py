from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from scripts.export_dashboard_db import export_db
from scripts.render_dashboard import (
    latest_candidate_date,
    read_candidates,
    read_ledger_rows,
    render_dashboard,
    summarize_ledger,
    summarize_paper,
)


def test_dashboard_renders_candidates_and_ledger_stats_when_inputs_exist(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    pd.DataFrame(
        [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "score": "71",
                "rating": "buy_candidate",
                "strategies": "ma_pullback",
                "ideal_buy": "1500",
                "close": "1498",
                "stop_loss": "1420",
                "take_profit": "1680",
                "position": "10%-30%",
                "reasons": "趋势回踩",
                "risks": "RSI偏热",
            }
        ]
    ).to_csv(csv_path, index=False)
    ledger_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-05-28",
                        "symbol": "600519",
                        "score": 71,
                        "status": "pending",
                        "thresholds_version": "1.0.0",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "signal_date": "2026-05-20",
                        "symbol": "300750",
                        "score": 63,
                        "status": "validated",
                        "win": True,
                        "return_pct": 2.4,
                        "thresholds_version": "1.0.0",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    candidates = read_candidates(csv_path)
    rows = read_ledger_rows(ledger_path)
    paper_rows = [
        {
            "symbol": "600519",
            "status": "open",
            "entry_date": "2026-05-29",
            "entry_price": 1501,
        },
        {
            "symbol": "300750",
            "status": "closed",
            "return_pct": 1.2,
        },
        {
            "symbol": "000001",
            "status": "pending_entry",
            "signal_date": "2026-05-29",
        },
    ]
    html = render_dashboard(candidates, rows, "测试面板", paper_rows)
    stats = summarize_ledger(rows)
    paper_stats = summarize_paper(paper_rows)

    assert stats.total == 2
    assert stats.pending == 1
    assert stats.validated == 1
    assert paper_stats.open_positions == 1
    assert paper_stats.closed == 1
    assert paper_stats.pending_entry == 1
    assert "测试面板" in html
    assert "600519" in html
    assert "贵州茅台" in html
    assert "阈值版本 1.0.0" in html
    assert "候选数据日" in html
    assert "虚拟盘" in html
    assert "虚拟持仓" in html
    assert "等待入场数据" in html
    assert "等待 2026-05-29 次日开盘" in html


def test_dashboard_warns_when_candidates_are_stale(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    pd.DataFrame(
        [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "date": "2025-05-20",
                "score": "48",
            }
        ]
    ).to_csv(csv_path, index=False)

    candidates = read_candidates(csv_path)
    html = render_dashboard(candidates, [], "陈旧面板")

    assert latest_candidate_date(candidates) == "2025-05-20"
    assert "不是今天" in html
    assert "不要按这个页面下单" in html


def test_dashboard_handles_missing_inputs() -> None:
    html = render_dashboard([], [], "空面板")

    assert "空面板" in html
    assert "本次没有候选股" in html
    assert "暂无真实候选输出" in html
    assert "暂无 ledger 记录" in html
    assert "暂无虚拟盘记录" in html


def test_read_candidates_handles_empty_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    csv_path.write_text("", encoding="utf-8")

    assert read_candidates(csv_path) == []


def test_export_dashboard_db_writes_candidates_ledger_and_meta(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    db_path = tmp_path / "dashboard" / "aqsp.db"

    pd.DataFrame(
        [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "score": "71",
            }
        ]
    ).to_csv(csv_path, index=False)
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-05-29",
                "symbol": "600519",
                "strategies": ["ma_pullback"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    export_db(csv_path, ledger_path, db_path)

    with sqlite3.connect(db_path) as conn:
        candidate_count = conn.execute("select count(*) from latest_candidates").fetchone()
        ledger_count = conn.execute("select count(*) from ledger").fetchone()
        meta = conn.execute(
            "select candidate_count, ledger_count from run_meta"
        ).fetchone()
        strategies = conn.execute("select strategies from ledger").fetchone()

    assert candidate_count == (1,)
    assert ledger_count == (1,)
    assert meta == (1, 1)
    assert strategies == ('["ma_pullback"]',)


def test_export_dashboard_db_handles_empty_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    db_path = tmp_path / "dashboard" / "aqsp.db"

    csv_path.write_text("", encoding="utf-8")
    export_db(csv_path, ledger_path, db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        meta = conn.execute(
            "select candidate_count, ledger_count from run_meta"
        ).fetchone()

    assert {"latest_candidates", "ledger", "run_meta"} <= tables
    assert meta == (0, 0)
