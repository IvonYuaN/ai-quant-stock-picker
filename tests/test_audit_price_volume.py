"""行情数据质量审计 CLI 的单元测试。

全部用临时 sqlite 小样本构造，不碰生产库、不联网、不装包。
覆盖：全 ok、四类告警/硬伤、exit code 分级、缺表与空库 fail-closed，
以及只读、--start 过滤、JSON 输出与直接函数调用。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "audit_price_volume.py"

# 直接以文件加载模块，供「直接调用函数」的自测使用，不依赖包名或 PYTHONPATH。
_spec = importlib.util.spec_from_file_location("audit_price_volume", str(SCRIPT))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_SCHEMA = (
    "CREATE TABLE daily_qfq ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, "
    "open REAL, high REAL, low REAL, close_qfq REAL, volume INTEGER, amount REAL, "
    "open_qfq REAL, high_qfq REAL, low_qfq REAL, close REAL, "
    "UNIQUE(ts_code, trade_date))"
)
_INSERT = (
    "INSERT INTO daily_qfq (ts_code, trade_date, close, close_qfq, volume) "
    "VALUES (?,?,?,?,?)"
)


def _code(board: str) -> str:
    # 6 开头归上交所，其余归深交所，前 3 位正好是板块分组键。
    suffix = "SH" if board.startswith("6") else "SZ"
    return board + "001." + suffix


def _write_db(path, rows, with_table=True) -> None:
    con = sqlite3.connect(path)
    if with_table:
        con.execute(_SCHEMA)
        con.executemany(_INSERT, rows)
    else:
        con.execute("CREATE TABLE unrelated (x INTEGER)")
    con.commit()
    con.close()


def _healthy_rows(
    boards=("000", "600"),
    months=("202601", "202602", "202603"),
    days_per_month=3,
    base_vol=1_000_000,
    vol_by_board=None,
    qfq_ratio=0.9,
):
    """构造四类检查全 ok 的健康样本：真复权、月份连续、量级一致、价格平稳。"""
    vol_by_board = vol_by_board or {}
    rows = []
    for board in boards:
        code = _code(board)
        p = 10.0
        for ym in months:
            for day in range(1, days_per_month + 1):
                p *= 1.001  # 相邻收益约 0.001，远小于 0.22
                close = round(p, 4)
                rows.append((code, ym + f"{day:02d}", close, round(p * qfq_ratio, 4),
                             vol_by_board.get(board, base_vol)))
    return rows


def _cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def _run(db, *extra):
    """跑 CLI 并同时取回结构化 JSON 结果。"""
    json_path = str(db) + ".json"
    proc = _cli("--db", str(db), "--json", json_path, *extra)
    result = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return proc, result


def _check(result, cid):
    return next(c for c in result["checks"] if c["id"] == cid)


def _snapshot(path):
    con = sqlite3.connect(path)
    count = con.execute("SELECT COUNT(*) FROM daily_qfq").fetchone()[0]
    tables = tuple(
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    )
    con.close()
    return count, tables


def test_all_ok_exit_0(tmp_path):
    db = tmp_path / "ok.db"
    _write_db(db, _healthy_rows())
    proc, res = _run(db)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert res["overall"] == "ok"
    assert res["exit_code"] == 0
    assert [c["status"] for c in res["checks"]] == ["ok", "ok", "ok", "ok"]
    assert "结论: ok" in proc.stdout
    assert "不是真复权" not in proc.stdout


def test_qfq_identity_warn_not_adjusted(tmp_path):
    # close_qfq 与 close 逐值相同 => 不是真复权，明确告警。
    db = tmp_path / "qfq.db"
    _write_db(db, _healthy_rows(qfq_ratio=1.0))
    proc, res = _run(db)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    c = _check(res, 1)
    assert c["status"] == "warn"
    assert c["verdict"] == "NOT_ADJUSTED"
    assert c["evidence"]["same_ratio"] == 1.0
    assert c["evidence"]["same"] == c["evidence"]["comparable"]
    assert "不是真复权" in proc.stdout
    assert "NOT_ADJUSTED" in proc.stdout
    assert all(_check(res, i)["status"] == "ok" for i in (2, 3, 4))


def test_sector_block_gap_warn(tmp_path):
    # 000/600 四个月连续有数据，688 缺 202607/202608 整块、前后月都有数据。
    months = ("202606", "202607", "202608", "202609")
    rows = _healthy_rows(boards=("000", "600"), months=months, days_per_month=2)
    p = 10.0
    for ym in ("202606", "202609"):
        for day in (1, 2):
            p *= 1.001
            rows.append(("688001.SH", ym + f"{day:02d}", round(p, 4),
                         round(p * 0.9, 4), 1_000_000))
    db = tmp_path / "gap.db"
    _write_db(db, rows)
    proc, res = _run(db)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    g = _check(res, 2)
    assert g["status"] == "warn"
    assert g["verdict"] == "BOARD_MONTH_GAP"
    assert g["evidence"]["gap_segments"] >= 1
    assert any(
        x["board"] == "688" and x["gap_start"] == "202607" and x["gap_end"] == "202608"
        for x in g["evidence"]["gaps"]
    )
    assert "202607" in proc.stdout


def test_volume_scale_warn(tmp_path):
    # 同月里 688 的 volume 中位数是其它板块的 12 倍，超过 5 倍阈值。
    db = tmp_path / "vol.db"
    _write_db(db, _healthy_rows(
        boards=("000", "600", "688"),
        vol_by_board={"688": 12_000_000},
    ))
    proc, res = _run(db)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    c = _check(res, 3)
    assert c["status"] == "warn"
    assert c["verdict"] == "VOLUME_SCALE_MISMATCH"
    assert c["evidence"]["max_ratio"] >= 12.0
    top = c["evidence"]["discrepancies"][0]
    assert top["max_median"] >= 12_000_000
    assert top["min_median"] <= 1_000_000
    assert "比值" in proc.stdout


def test_abnormal_gaps_fail(tmp_path):
    # 30 天里制造 3 个 -80% 除权级暴跌，占比超过 fail 阈值。
    dates = [ym + f"{d:02d}"
             for ym in ("202601", "202602", "202603") for d in range(1, 11)]
    crash = {5, 12, 20}
    rows = []
    p = 100.0
    for i, date in enumerate(dates):
        p *= 0.2 if i in crash else 1.001
        rows.append(("000001.SZ", date, round(p, 4), round(p * 0.9, 4), 1_000_000))
    db = tmp_path / "gapfail.db"
    _write_db(db, rows)
    proc, res = _run(db)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    c = _check(res, 4)
    assert c["status"] == "fail"
    assert c["verdict"] == "MASSIVE_GAPS"
    assert c["evidence"]["abnormal"] >= 2
    assert c["evidence"]["negative"] >= 2
    assert c["evidence"]["samples"]
    assert "除权跳空被当暴跌" in proc.stdout


def test_abnormal_gaps_warn(tmp_path):
    # 仅 1 个暴跌、样本足够多，占比低于 fail 阈值，只告警。
    dates = [ym + f"{d:02d}"
             for ym in ("202601", "202602") for d in range(1, 11)]
    dates += ["202603" + f"{d:02d}" for d in range(1, 6)]
    rows = []
    p = 100.0
    for i, date in enumerate(dates):
        p *= 0.2 if i == 10 else 1.001
        rows.append(("000001.SZ", date, round(p, 4), round(p * 0.9, 4), 1_000_000))
    db = tmp_path / "gapwarn.db"
    _write_db(db, rows)
    proc, res = _run(db)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    c = _check(res, 4)
    assert c["status"] == "warn"
    assert c["verdict"] == "GAPS_PRESENT"
    assert c["evidence"]["abnormal"] == 1
    assert c["evidence"]["abnormal_ratio"] < c["evidence"]["fail_ratio"]


def test_fail_overrides_warn(tmp_path):
    # 同时存在 qfq 恒等 warn、volume 量级 warn 与异常跳空 fail，整体应为 fail/2。
    rows = []
    for ym in ("202601", "202602", "202603"):
        p = 10.0
        for day in range(1, 11):
            p *= 0.2 if day in (5, 8) else 1.001
            rows.append(("000001.SZ", ym + f"{day:02d}", round(p, 4),
                         round(p, 4), 1_000_000))  # qfq 恒等
            rows.append(("688001.SH", ym + f"{day:02d}", 10.0, 10.0, 12_000_000))
    db = tmp_path / "mix.db"
    _write_db(db, rows)
    proc, res = _run(db)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert res["overall"] == "fail"
    statuses = [c["status"] for c in res["checks"]]
    assert "fail" in statuses and "warn" in statuses
    assert _check(res, 1)["status"] == "warn"  # warn 仍如实保留，不被吞掉
    assert _check(res, 3)["status"] == "warn"
    assert _check(res, 4)["status"] == "fail"


def test_missing_table_fail_closed(tmp_path):
    db = tmp_path / "notable.db"
    _write_db(db, [], with_table=False)
    proc, res = _run(db)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert res["overall"] == "fail"
    assert res["exit_code"] == 2
    assert res["checks"] == []
    assert "缺表" in res["fail_closed"] or "daily_qfq" in res["fail_closed"]
    assert "FAIL-CLOSED" in proc.stdout


def test_empty_db_fail_closed(tmp_path):
    db = tmp_path / "empty.db"
    _write_db(db, [])  # 表存在但 0 行
    proc, res = _run(db)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert res["exit_code"] == 2
    assert "0 行" in res["fail_closed"] or "空库" in res["fail_closed"]
    assert "FAIL-CLOSED" in proc.stdout


def test_nonexistent_db_fail_closed(tmp_path):
    db = tmp_path / "nope.db"  # 故意不创建
    proc, res = _run(db)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert res["fail_closed"] and "只读打开" in res["fail_closed"]


def test_bad_start_fail_closed(tmp_path):
    db = tmp_path / "ok.db"
    _write_db(db, _healthy_rows())
    proc = _cli("--db", str(db), "--start", "2026")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "参数错误" in proc.stderr


def test_start_filters_rows(tmp_path):
    # 2025 的行 close_qfq==close（若被误计入会让 same>=1），2026 全部真复权。
    rows = []
    p = 10.0
    for day in range(1, 4):
        p *= 1.001
        rows.append(("000001.SZ", "202512" + f"{day:02d}", round(p, 4),
                     round(p, 4), 1_000_000))
    healthy = _healthy_rows(boards=("000", "600"),
                            months=("202601", "202602", "202603"), days_per_month=3)
    rows += healthy
    db = tmp_path / "start.db"
    _write_db(db, rows)

    proc, filtered = _run(db, "--start", "20260101")
    assert filtered["rows"] == len(healthy), proc.stdout + proc.stderr
    assert _check(filtered, 1)["evidence"]["same"] == 0
    assert _check(filtered, 1)["status"] == "ok"
    assert "start : 20260101" in proc.stdout

    _, full = _run(db)  # 不带 --start 作对照
    assert full["rows"] == len(rows)
    assert _check(full, 1)["evidence"]["same"] == 3


def test_json_output_structure(tmp_path):
    db = tmp_path / "json.db"
    _write_db(db, _healthy_rows(qfq_ratio=1.0))
    proc, res = _run(db)
    assert res["exit_code"] == proc.returncode
    assert res["readonly"]
    assert res["table"] == "daily_qfq"
    assert res["fail_closed"] is None
    assert len(res["checks"]) == 4
    for c in res["checks"]:
        assert c["status"] in {"ok", "warn", "fail"}
        assert isinstance(c["evidence"], dict)
        assert c["message"]


def test_readonly_does_not_write(tmp_path):
    db = tmp_path / "ro.db"
    _write_db(db, _healthy_rows())
    before = _snapshot(db)
    proc, res = _run(db)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert res["readonly"]
    assert _snapshot(db) == before  # 行数与表清单都没变
    # 把文件权限改成只读，审计仍成功，证明全程没有尝试写库。
    os.chmod(db, 0o444)
    try:
        proc2 = _cli("--db", str(db))
        assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    finally:
        os.chmod(db, 0o644)


def test_run_audit_direct_call(tmp_path):
    db = tmp_path / "direct.db"
    _write_db(db, _healthy_rows())
    res = mod.run_audit(str(db), None)
    assert res["overall"] == "ok"
    assert res["exit_code"] == 0
    assert len(res["checks"]) == 4
    failed = mod.run_audit(str(tmp_path / "missing.db"), None)
    assert failed["exit_code"] == 2
    assert failed["fail_closed"]


def test_month_helpers_cross_year():
    # 月份缺口判定要能正确跨年，202612 之后必须是 202701。
    assert mod._next_month("202612") == "202701"
    span = mod._month_span("202611", "202702")
    assert span == ["202611", "202612", "202701", "202702"]
