"""``scripts/data/resettle_ledger_dirty.py`` 的口径与行为测试。

这个脚本补的是「台账物理不可能收益脏行**没有重结算入口**」的缺口：
`backfill_ledger_excess.py` 只补 NULL 超额，对 `return_pct` 已写错的 19 条脏行
无能为力（它遇到已有 return_pct 直接跳过）。根因见 commit ``2ad9f975``：
`_resolve_exit` 用行内**绝对价** stop_loss/take_profit 出场，标度不一致时止损价
高于市价 ⇒ 当日误触发 ⇒ 算出物理不可能收益（实测最大 +317%）。

本测试锁三件事（离线、确定性、合成数据）：
1. **脏行判据**：`is_dirty_row` 只命中「当日了结 + |收益|>30% + validated + 非模拟」；
2. **重算正确**：`resettle_row` 把脏止损（257>入场124）忽略、回退持有到期，
   317% ⇒ 1.61%；正常行零改动；
3. **口径单一事实源**：内联 `_exit`/`_benchmark_return` 与生产
   `aqsp.ledger.base._resolve_exit`/`_benchmark_return` **逐位对拍 max|diff|=0**
   （AGENTS.md「复用口径要写断言」红线；若生产语义再变更须同步本脚本）。
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from aqsp.ledger.base import _benchmark_return, _resolve_exit
from scripts.data import resettle_ledger_dirty as resettle


# ---------------------------------------------------------------- 合成行情库


@pytest.fixture()
def bench_db(tmp_path) -> str:
    """含 301312.SZ（脏行标的）+ 000300.SH（基准）的最小日线库。"""
    db = str(tmp_path / "astocks_raw.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE daily_qfq "
        "(ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL)"
    )

    def seed(ts: str, rows) -> None:
        for d, o, h, low, c in rows:
            con.execute(
                "INSERT INTO daily_qfq VALUES (?,?,?,?,?,?)", (ts, d, o, h, low, c)
            )

    # 301312：信号日 04-13 收盘 122.70；04-14 开 124（入场）；04-15 收 126（持有到期）
    seed(
        "301312.SZ",
        [
            ("20260413", 120.0, 125.0, 119.0, 122.70),
            ("20260414", 124.0, 126.0, 123.0, 125.0),
            ("20260415", 125.0, 127.0, 124.0, 126.0),
        ],
    )
    # 基准 000300
    seed(
        "000300.SH",
        [
            ("20260414", 4000.0, 4010.0, 3990.0, 4005.0),
            ("20260415", 4005.0, 4015.0, 4000.0, 4010.0),
        ],
    )
    con.commit()
    con.close()
    return db


def _dirty_row() -> dict:
    """301312 脏行：stop_loss=257（高于市价，复权基准不一致），当日了结，旧收益 +317%。"""
    return {
        "id": "abcdef12",
        "symbol": "301312.SZ",
        "benchmark_symbol": "000300",
        "signal_date": "2026-04-13",
        "entry_date": "2026-04-14",
        "exit_date": "2026-04-14",
        "stop_loss": 257.4,
        "take_profit": 0,
        "horizon_days": 2,
        "fee_bps": 0,
        "slippage_bps": 0,
        "status": "validated",
        "is_simulated": False,
        "return_pct": 317.0,
        "excess_return_pct": 300.0,
        "win": True,
    }


# ---------------------------------------------------------------- 脏行判据


def test_is_dirty_row_when_same_day_implausible_return() -> None:
    assert resettle.is_dirty_row(_dirty_row()) is True


def test_is_dirty_row_when_normal_return() -> None:
    row = _dirty_row()
    row["return_pct"] = 1.6
    assert resettle.is_dirty_row(row) is None or resettle.is_dirty_row(row) is False


def test_is_dirty_row_when_multiday_or_not_validated() -> None:
    row = _dirty_row()
    row["exit_date"] = "2026-04-16"  # 非当日了结
    assert resettle.is_dirty_row(row) is False
    row = _dirty_row()
    row["status"] = "pending"
    assert resettle.is_dirty_row(row) is False


def test_is_dirty_row_when_simulated() -> None:
    row = _dirty_row()
    row["is_simulated"] = True
    assert resettle.is_dirty_row(row) is False


def test_is_dirty_row_when_return_missing() -> None:
    row = _dirty_row()
    row["return_pct"] = None
    assert resettle.is_dirty_row(row) is False


# ---------------------------------------------------------------- 重算正确性


def test_resettle_row_when_dirty_stop_ignored_holds_to_horizon(bench_db: str) -> None:
    con = sqlite3.connect(f"file:{bench_db}?mode=ro", uri=True)
    try:
        new = resettle.resettle_row(con, _dirty_row())
    finally:
        con.close()
    assert new is not None
    # 脏止损 257 > 入场 124 ⇒ 语义校验忽略 ⇒ 按持有到期（04-15 收 126）
    assert new["exit_reason"] == "horizon_close"
    assert abs(new["return_pct"] - ((126.0 - 124.0) / 124.0 * 100)) < 1e-3
    assert new["win"] is True
    # 重算后不再是脏行（|return| < 30）⇒ 幂等
    assert resettle.is_dirty_row(new) is False


def test_resettle_row_when_benchmark_missing_returns_none_for_excess(bench_db: str) -> None:
    row = _dirty_row()
    row["benchmark_symbol"] = ""
    con = sqlite3.connect(f"file:{bench_db}?mode=ro", uri=True)
    try:
        new = resettle.resettle_row(con, row)
    finally:
        con.close()
    assert new is not None
    assert new["excess_return_pct"] is None  # 缺基准 ⇒ 超额 None（不回填 0）


def test_resettle_row_when_symbol_not_in_db_returns_none(bench_db: str) -> None:
    row = _dirty_row()
    row["symbol"] = "999999.SZ"
    con = sqlite3.connect(f"file:{bench_db}?mode=ro", uri=True)
    try:
        assert resettle.resettle_row(con, row) is None
    finally:
        con.close()


# ---------------------------------------------------------------- 口径对拍（max|diff|=0）


@pytest.mark.parametrize(
    ("stop", "take", "entry"),
    [
        (257.4, 0.0, 124.0),  # 脏止损：高于市价 ⇒ 忽略
        (120.0, 0.0, 124.0),  # 正常止损：低于入场
        (0.0, 130.0, 124.0),  # 正常止盈：高于入场
        (110.0, 130.0, 124.0),  # 双阈值同框
    ],
)
def test_inline_exit_matches_production_resolve_exit(
    stop: float, take: float, entry: float
) -> None:
    """内联 `_exit` 与生产 `base._resolve_exit`（2ad9f975 语义校验版）逐位对拍。

    这是单一事实源红线：若 `base._resolve_exit` 语义再变更，本脚本内联副本会
    先在这里被抓（而不是静默分叉）。
    """
    win_df = pd.DataFrame(
        [
            {"date": "20260414", "open": 124.0, "high": 126.0, "low": 123.0, "close": 125.0},
            {"date": "20260415", "open": 125.0, "high": 127.0, "low": 124.0, "close": 126.0},
        ]
    )
    bars = [
        (r.date, float(r.open), float(r.high), float(r.low), float(r.close))
        for r in win_df.itertuples(index=False)
    ]
    row = {"stop_loss": stop, "take_profit": take, "slippage_bps": 0.0}
    _, mine_price, mine_reason = resettle._exit(bars, row, entry)
    _, theirs_price, theirs_reason = _resolve_exit(win_df, row, entry)
    assert abs(mine_price - theirs_price) < 1e-9, (
        f"内联 {mine_price}/{mine_reason} vs 生产 {theirs_price}/{theirs_reason}"
    )
    assert mine_reason == theirs_reason


def test_inline_benchmark_matches_production(bench_db: str) -> None:
    """内联 `_benchmark_return`（纯 SQL）与生产 `base._benchmark_return`（DataFrame）对拍。"""
    con = sqlite3.connect(f"file:{bench_db}?mode=ro", uri=True)
    try:
        entry_bar = ("20260414", 4000.0, 4010.0, 3990.0, 4005.0)
        exit_bar = ("20260415", 4005.0, 4015.0, 4000.0, 4010.0)
        mine = resettle._benchmark_return(con, "000300", entry_bar, exit_bar)
    finally:
        con.close()
    bench_df = pd.DataFrame(
        [
            {"date": "20260414", "open": 4000.0, "close": 4005.0},
            {"date": "20260415", "open": 4005.0, "close": 4010.0},
        ]
    )
    theirs = _benchmark_return(
        {"000300": bench_df},
        {"benchmark_symbol": "000300"},
        pd.Series({"date": "20260414"}),
        pd.Series({"date": "20260415"}),
    )
    assert mine is not None and theirs is not None
    assert abs(mine - theirs) < 1e-9


# ---------------------------------------------------------------- CLI：dry-run / apply / 备份 / 幂等


def test_main_dry_run_then_apply_and_idempotent(
    bench_db: str, tmp_path, monkeypatch, capsys
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    rows = [
        _dirty_row(),
        {  # 正常多日了结行，绝不动
            "id": "bbb",
            "symbol": "600000.SH",
            "benchmark_symbol": "000300",
            "signal_date": "2026-04-13",
            "entry_date": "2026-04-14",
            "exit_date": "2026-04-16",
            "horizon_days": 3,
            "fee_bps": 2,
            "slippage_bps": 10,
            "status": "validated",
            "is_simulated": False,
            "return_pct": 1.2,
        },
    ]
    ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")

    # 600000.SH 库里没有（窗口不足）⇒ apply 时应「保留原值」不报错、不丢行
    monkeypatch.chdir(tmp_path)

    # dry-run：只报 1 条待重结算，不写盘
    rc = resettle.main(["--ledger", str(ledger), "--db", bench_db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "待重结算 1 条" in out
    assert "[dry-run] 未写入" in out
    first = json.loads(ledger.read_text().splitlines()[0])
    assert first["return_pct"] == 317.0, "dry-run 不应改盘"

    # apply：写 1 条 + 备份 + 正常行零改动
    rc = resettle.main(["--ledger", str(ledger), "--db", bench_db, "--apply"])
    assert rc == 0
    backups = list(tmp_path.glob("predictions.jsonl.bak-*"))
    assert len(backups) == 1, "apply 必须生成备份"
    lines = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
    assert lines[0]["return_pct"] == 1.6129 and lines[0]["exit_reason"] == "horizon_close"
    assert lines[1]["return_pct"] == 1.2, "正常行被误改"

    # 幂等：再跑 dry-run ⇒ 0 条命中
    resettle.main(["--ledger", str(ledger), "--db", bench_db])
    out = capsys.readouterr().out
    assert "待重结算 0 条" in out
    assert "无需改动" in out


def test_main_when_ledger_missing_returns_2(tmp_path, capsys) -> None:
    rc = resettle.main(["--ledger", str(tmp_path / "nope.jsonl"), "--db", "/dev/null"])
    assert rc == 2
    assert "台账不存在" in capsys.readouterr().err
