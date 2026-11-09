"""腾讯回补器的 volume 单位口径。

腾讯 fqkline 的 volume 只对主板/创业板返回「手」，科创板（688/689）直接
返回「股」。统一乘 100 会把科创板成交量放大两个数量级，而库里这些行
``amount`` 为 NULL（腾讯不提供成交额），事后无法用
``amount / (volume * close)`` 自检，污染会静默留存在库中。
"""
from __future__ import annotations

import sqlite3
import sys

import pytest

from scripts.data import backfill_from_tencent as backfill


@pytest.mark.parametrize(
    ("ts_code", "expected"),
    [
        ("688001.SH", 1),
        ("688469.SH", 1),
        ("689009.SH", 1),
        ("600000.SH", 100),
        ("601398.SH", 100),
        ("000001.SZ", 100),
        ("300001.SZ", 100),
        ("301001.SZ", 100),
    ],
)
def test_volume_scale_when_board_varies(ts_code: str, expected: int) -> None:
    assert backfill.volume_scale(ts_code) == expected


def test_volume_scale_when_given_tx_form_is_not_silently_wrong() -> None:
    """``sh688001`` 不是本脚本的 ``ts_code`` 形态。

    这是缺陷的原始成因：判断写成 ``startswith("sh688")`` 却被
    ``ts_code`` 调用，于是永远走 100 倍分支。契约固定在 ``ts_code``，
    ``to_tx`` 形态不在支持范围内 —— 用文档化的行为锁住这一点，
    避免有人再把两种形态混用。
    """
    assert backfill.volume_scale(backfill.to_tx("688001.SH")) == 100


def _make_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE daily_qfq ("
        "ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL,"
        " close REAL, close_qfq REAL, volume REAL, amount REAL,"
        " PRIMARY KEY (ts_code, trade_date))"
    )
    # 已存在一行旧数据，用于确认 INSERT OR IGNORE 不会覆盖历史行
    conn.execute(
        "INSERT INTO daily_qfq VALUES ('600000.SH','20261105',1,1,1,1,1,123,456)"
    )
    # 科创板必须有更早的历史行，否则它不在 `pending` 里、不会被回补
    conn.execute(
        "INSERT INTO daily_qfq VALUES ('688001.SH','20261105',1,1,1,1,1,777,888)"
    )
    conn.commit()
    conn.close()


def test_main_writes_star_volume_without_hundredfold_scaling(tmp_path, monkeypatch) -> None:
    db = tmp_path / "raw.db"
    _make_db(db)

    bars = {
        "sh688001": [["2026-11-06", "35.690", "36.680", "37.660", "35.100", "8630559.000"]],
        "sh600000": [["2026-11-06", "9.910", "10.030", "10.070", "9.840", "642707.000"]],
    }

    def fake_fetch(sym: str, start: str, end: str):
        return bars[sym]

    monkeypatch.setattr(backfill, "fetch", fake_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["backfill_from_tencent.py", str(db), "--target", "2026-11-06", "--batch", "10"],
    )

    assert backfill.main() == 0

    conn = sqlite3.connect(db)
    try:
        star = conn.execute(
            "SELECT volume, amount, close FROM daily_qfq"
            " WHERE ts_code='688001.SH' AND trade_date='20261106'"
        ).fetchone()
        main_board = conn.execute(
            "SELECT volume, amount FROM daily_qfq WHERE ts_code='600000.SH' AND trade_date='20261106'"
        ).fetchone()
        untouched = conn.execute(
            "SELECT volume, amount FROM daily_qfq WHERE ts_code='600000.SH' AND trade_date='20261105'"
        ).fetchone()
        star_untouched = conn.execute(
            "SELECT volume, amount FROM daily_qfq WHERE ts_code='688001.SH' AND trade_date='20261105'"
        ).fetchone()
    finally:
        conn.close()

    assert star == (8630559, None, 36.68)
    assert main_board == (64270700, None)
    assert untouched == (123, 456)
    assert star_untouched == (777, 888)
