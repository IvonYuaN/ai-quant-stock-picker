#!/usr/bin/env python3
"""行情数据质量审计 CLI（只读）。

把 daily_qfq 里已知的几类数据硬伤变成可自动检测的能力：

1. qfq 恒等：close_qfq 与 close 逐值相同其实不是真复权，真复权应在
   src/aqsp/data/adjust.py 里用 PIT 因子现算。
2. 板块整块缺失：按 ts_code 前 3 位分组、按月份统计行数，找出某板块
   中间整月 0 行但前后相邻月都有数据的缺口。
3. volume 量级不一致：按板块加月份算 volume 中位数，板块间差异过大告警。
4. 异常跳空：逐票计算相邻收盘价收益，统计大幅跳空，识别除权跳空被当暴跌。

设计要点：
- 全程只用标准库，不依赖 pandas/numpy，不装包。
- 以 file:...?mode=ro 只读打开，只发 SELECT，绝不写库。
- 只用 trade_date >= --start 的行，不触碰未来数据。
- fail-closed：缺表、空库、查询报错、参数错误一律 exit 2 并打印原因，
  绝不静默通过。

exit code：出现任一 fail 为 2，仅 warn 为 1，全 ok 为 0。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

TABLE = "daily_qfq"
# 相邻收盘价收益超过此绝对值即视为异常跳空。
GAP_THRESHOLD = 0.22
# 异常跳空占有效收益数的比例超过此值时判定为数据不可用的 fail。
GAP_FAIL_RATIO = 0.05
# 板块间 volume 中位数倍数超过此值即告警。
VOLUME_RATIO_THRESHOLD = 5.0
# close_qfq 与 close 相同的行占比达到此值即判疑似拷贝、不是真复权。
QFQ_IDENTITY_WARN = 0.99
# 每类检查最多展示的样例条数。
SAMPLE_LIMIT = 5


class AuditError(Exception):
    """审计无法继续时的结构性错误，统一按 fail-closed 处理。"""


def _open_readonly(db_path: str) -> sqlite3.Connection:
    # 用百分号编码过的绝对文件路径拼 URI，兼容含中文或空格的路径。
    uri = "file:" + quote(str(Path(db_path).resolve())) + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _assert_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    ).fetchone()
    if row is None:
        raise AuditError(f"缺表: {TABLE} 不存在（fail-closed）")


def _base_conds(start: str | None) -> tuple[list[str], list[str]]:
    # 所有查询都从这里出发，保证只看 >= --start 的行。
    conds: list[str] = []
    params: list[str] = []
    if start is not None:
        conds.append("trade_date >= ?")
        params.append(start)
    return conds, params


def _sql_where(conds: list[str]) -> str:
    return " WHERE " + " AND ".join(conds) if conds else ""


def _count_rows(conn: sqlite3.Connection, start: str | None) -> int:
    conds, params = _base_conds(start)
    n = conn.execute(
        "SELECT COUNT(*) FROM " + TABLE + _sql_where(conds),
        tuple(params),
    ).fetchone()[0]
    if not n:
        raise AuditError("空库或起始日后无数据: daily_qfq 过滤后 0 行（fail-closed）")
    return int(n)


def _next_month(ym: str) -> str:
    y = int(ym[:4])
    m = int(ym[4:6]) + 1
    if m == 13:
        y += 1
        m = 1
    return f"{y:04d}{m:02d}"


def _month_span(first: str, last: str) -> list[str]:
    # 含首尾的连续日历月序列，跨年正确（202612 之后是 202701）。
    out: list[str] = []
    cur = first
    while cur <= last:
        out.append(cur)
        if len(out) > 1200:  # 防御异常跨度导致死循环
            break
        cur = _next_month(cur)
    return out


def check_qfq_identity(conn: sqlite3.Connection, start: str | None) -> dict:
    conds, params = _base_conds(start)
    total, comparable, same = conn.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN close IS NOT NULL AND close_qfq IS NOT NULL THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN close_qfq = close THEN 1 ELSE 0 END) "
        "FROM " + TABLE + _sql_where(conds),
        tuple(params),
    ).fetchone()
    comparable = int(comparable or 0)
    same = int(same or 0)
    evidence = {"rows": int(total), "comparable": comparable, "same": same, "same_ratio": 0.0}
    if comparable == 0:
        return {
            "id": 1,
            "name": "qfq_identity",
            "status": "fail",
            "verdict": "NO_COMPARABLE_ROWS",
            "message": "close/close_qfq 均为 NULL，无法判定是否真复权",
            "evidence": evidence,
        }
    ratio = same / comparable
    evidence["same_ratio"] = round(ratio, 6)
    if ratio >= QFQ_IDENTITY_WARN:
        not_adjusted = ratio == 1.0
        verdict = "NOT_ADJUSTED" if not_adjusted else "NEAR_IDENTITY"
        message = f"same_ratio={ratio:.3f} (same={same}/{comparable})"
        if not_adjusted:
            message += " NOT_ADJUSTED: close_qfq 与 close 逐值相同，不是真复权，真复权应 PIT 现算"
        else:
            message += " 疑似拷贝，绝大多数行未复权"
        return {
            "id": 1,
            "name": "qfq_identity",
            "status": "warn",
            "verdict": verdict,
            "message": message,
            "evidence": evidence,
        }
    return {
        "id": 1,
        "name": "qfq_identity",
        "status": "ok",
        "verdict": "ADJUSTED_LIKELY",
        "message": f"same_ratio={ratio:.3f} (same={same}/{comparable})，存在真实复权差异",
        "evidence": evidence,
    }


def check_sector_gaps(conn: sqlite3.Connection, start: str | None) -> dict:
    conds, params = _base_conds(start)
    board_month: dict[str, dict[str, int]] = defaultdict(dict)
    for board, ym, cnt in conn.execute(
        "SELECT substr(ts_code,1,3), substr(trade_date,1,6), COUNT(*) "
        "FROM " + TABLE + _sql_where(conds) + " GROUP BY 1,2",
        tuple(params),
    ):
        board_month[board][ym] = int(cnt)

    gaps: list[dict] = []
    month_set: set[str] = set()
    for board, counts in board_month.items():
        data_months = sorted(ym for ym, c in counts.items() if c > 0)
        month_set.update(data_months)
        if len(data_months) < 2:
            continue
        span = _month_span(data_months[0], data_months[-1])
        present = set(data_months)
        n = len(span)
        i = 0
        while i < n:
            if span[i] in present:
                i += 1
                continue
            j = i
            while j < n and span[j] not in present:
                j += 1
            prev_ok = i - 1 >= 0 and span[i - 1] in present
            next_ok = j < n and span[j] in present
            if prev_ok and next_ok:
                gaps.append({
                    "board": board,
                    "gap_start": span[i],
                    "gap_end": span[j - 1],
                    "prev_month": span[i - 1],
                    "next_month": span[j],
                    "missing_months": j - i,
                })
            i = j

    shown = ", ".join(
        f"{g['board']}:[{g['gap_start']}..{g['gap_end']}]"
        for g in gaps[:SAMPLE_LIMIT]
    )
    evidence = {
        "boards": len(board_month),
        "months": len(month_set),
        "gap_segments": len(gaps),
        "gaps": gaps[:SAMPLE_LIMIT],
    }
    if gaps:
        message = (
            f"板块数={len(board_month)} 月份数={len(month_set)} "
            f"缺口段={len(gaps)} 缺口区间: {shown}"
        )
        status, verdict = "warn", "BOARD_MONTH_GAP"
    else:
        message = f"板块数={len(board_month)} 月份数={len(month_set)}，未发现整块缺失"
        status, verdict = "ok", "NO_GAP"
    return {
        "id": 2,
        "name": "sector_block_gaps",
        "status": status,
        "verdict": verdict,
        "message": message,
        "evidence": evidence,
    }


def check_volume_scale(conn: sqlite3.Connection, start: str | None) -> dict:
    conds, params = _base_conds(start)
    conds.append("volume IS NOT NULL")
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for board, ym, vol in conn.execute(
        "SELECT substr(ts_code,1,3), substr(trade_date,1,6), volume "
        "FROM " + TABLE + _sql_where(conds),
        tuple(params),
    ):
        try:
            v = float(vol)
        except (TypeError, ValueError):
            continue
        if v > 0:  # 只统计正的成交量，0 或脏值不参与中位数
            groups[(board, ym)].append(v)

    board_month_median = {k: statistics.median(v) for k, v in groups.items()}
    by_month: dict[str, dict[str, float]] = defaultdict(dict)
    for (board, ym), med in board_month_median.items():
        by_month[ym][board] = med

    discrepancies: list[dict] = []
    months_compared = 0
    for ym, meds in by_month.items():
        pos = {b: v for b, v in meds.items() if v > 0}
        if len(pos) < 2:
            continue
        months_compared += 1
        hi_b = max(pos, key=pos.get)
        lo_b = min(pos, key=pos.get)
        ratio = pos[hi_b] / pos[lo_b]
        if ratio > VOLUME_RATIO_THRESHOLD:
            discrepancies.append({
                "month": ym,
                "max_board": hi_b,
                "max_median": pos[hi_b],
                "min_board": lo_b,
                "min_median": pos[lo_b],
                "ratio": round(ratio, 3),
            })
    discrepancies.sort(key=lambda d: d["ratio"], reverse=True)
    max_ratio = discrepancies[0]["ratio"] if discrepancies else 1.0
    evidence = {
        "board_month_groups": len(board_month_median),
        "months_compared": months_compared,
        "threshold": VOLUME_RATIO_THRESHOLD,
        "max_ratio": max_ratio,
        "discrepancies": discrepancies[:SAMPLE_LIMIT],
    }
    if discrepancies:
        top = discrepancies[0]
        message = (
            f"(板块,月)组={len(board_month_median)} 比较月份={months_compared} "
            f"最大板块间倍数={max_ratio:.1f}>{VOLUME_RATIO_THRESHOLD:.0f} "
            f"例: {top['month']} {top['max_board']}={top['max_median']:.0f} "
            f"vs {top['min_board']}={top['min_median']:.0f} 比值={top['ratio']:.1f}"
        )
        status, verdict = "warn", "VOLUME_SCALE_MISMATCH"
    else:
        message = (
            f"(板块,月)组={len(board_month_median)} 比较月份={months_compared} "
            f"最大板块间倍数={max_ratio:.1f}，量级一致"
        )
        status, verdict = "ok", "VOLUME_SCALE_OK"
    return {
        "id": 3,
        "name": "volume_scale",
        "status": status,
        "verdict": verdict,
        "message": message,
        "evidence": evidence,
    }


def check_abnormal_gaps(conn: sqlite3.Connection, start: str | None) -> dict:
    conds, params = _base_conds(start)
    sql = (
        "SELECT ts_code, trade_date, close FROM " + TABLE + _sql_where(conds)
        + " ORDER BY ts_code, trade_date"
    )
    abnormal = 0
    negative = 0
    total_ret = 0
    samples: list[dict] = []
    prev_code: str | None = None
    prev_close: float | None = None
    for code, trade_date, close in conn.execute(sql, tuple(params)):
        if code != prev_code:  # 换票重置，绝不跨票计算收益
            prev_code = code
            prev_close = None
        cur: float | None = None
        if close is not None:
            try:
                cv = float(close)
            except (TypeError, ValueError):
                cv = 0.0
            if cv > 0:
                cur = cv
        if prev_close is not None and cur is not None:
            ret = cur / prev_close - 1.0
            total_ret += 1
            if abs(ret) > GAP_THRESHOLD:
                abnormal += 1
                if ret < 0:
                    negative += 1
                if len(samples) < SAMPLE_LIMIT:
                    samples.append({
                        "ts_code": code,
                        "trade_date": trade_date,
                        "ret": round(ret, 6),
                        "prev_close": round(prev_close, 4),
                        "close": round(cur, 4),
                    })
        # 遇到 NULL 或非正收盘价就断开，不对缺失值做填充，等价于
        # pct_change(fill_method=None) 的相邻逐元素语义。
        prev_close = cur

    ratio = abnormal / total_ret if total_ret else 0.0
    sample_text = ", ".join(
        f"{s['ts_code']}@{s['trade_date']}={s['ret']:.2f}" for s in samples
    )
    evidence = {
        "threshold": GAP_THRESHOLD,
        "fail_ratio": GAP_FAIL_RATIO,
        "total_returns": total_ret,
        "abnormal": abnormal,
        "negative": negative,
        "abnormal_ratio": round(ratio, 6),
        "samples": samples,
    }
    message = (
        f"|ret|>{GAP_THRESHOLD}: {abnormal}/{total_ret} "
        f"(ratio={ratio:.3f}) 负向={negative}"
    )
    if sample_text:
        message += f" 样例: {sample_text}"

    if total_ret == 0:
        status, verdict = "warn", "INSUFFICIENT_SAMPLES"
        message += " 无相邻有效收盘价，无法判定跳空，不静默通过"
    elif ratio >= GAP_FAIL_RATIO:
        status, verdict = "fail", "MASSIVE_GAPS"
        message += " 除权跳空被当暴跌，收益序列不可用"
    elif abnormal > 0:
        status, verdict = "warn", "GAPS_PRESENT"
    else:
        status, verdict = "ok", "NO_GAPS"
    return {
        "id": 4,
        "name": "abnormal_gaps",
        "status": status,
        "verdict": verdict,
        "message": message,
        "evidence": evidence,
    }


def _fail_closed(base: dict, reason: str) -> dict:
    return {
        **base,
        "rows": 0,
        "overall": "fail",
        "exit_code": 2,
        "checks": [],
        "fail_closed": reason,
    }


def run_audit(db_path: str, start: str | None) -> dict:
    """执行一次完整审计，返回结构化结果，任何异常都转成 fail-closed。"""
    base = {"db": str(db_path), "start": start, "readonly": True, "table": TABLE}
    try:
        conn = _open_readonly(db_path)
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        return _fail_closed(base, "无法只读打开数据库(mode=ro): " + str(exc))
    try:
        _assert_table(conn)
        rows = _count_rows(conn, start)
        checks = [
            check_qfq_identity(conn, start),
            check_sector_gaps(conn, start),
            check_volume_scale(conn, start),
            check_abnormal_gaps(conn, start),
        ]
    except (AuditError, sqlite3.Error, OSError, ValueError, TypeError) as exc:
        return _fail_closed(base, str(exc))
    finally:
        conn.close()

    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        overall, code = "fail", 2
    elif "warn" in statuses:
        overall, code = "warn", 1
    else:
        overall, code = "ok", 0
    return {
        **base,
        "rows": rows,
        "overall": overall,
        "exit_code": code,
        "checks": checks,
        "fail_closed": None,
    }


def format_report(result: dict) -> str:
    """生成人读表，每项都带 status 与证据数字。"""
    lines = [
        "行情数据质量审计 | 表=" + TABLE + " | 只读=mode=ro | 只用 >= --start 的行",
        "DB    : " + result["db"],
        "start : " + (result["start"] or "ALL"),
    ]
    if result["fail_closed"] is not None:
        lines.append("[AUDIT FAIL-CLOSED] 原因: " + result["fail_closed"])
        lines.append("结论: fail  exit=2")
        return "\n".join(lines)

    lines.append("rows  : " + str(result["rows"]))
    lines.append("-" * 70)
    for c in result["checks"]:
        tag = {"ok": "[ok  ]", "warn": "[warn]", "fail": "[FAIL]"}[c["status"]]
        lines.append(f"{tag} {c['id']} {c['name']}: {c['message']}")
    lines.append("-" * 70)
    lines.append(f"结论: {result['overall']}  exit={result['exit_code']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, help="sqlite 库路径（以 mode=ro 只读打开）")
    p.add_argument("--start", default=None, help="起始交易日 YYYYMMDD，只审计 >= 该日的行")
    p.add_argument("--json", dest="json_path", default=None, help="可选，把结果写成 JSON")
    args = p.parse_args(argv)

    if args.start is not None and (len(args.start) != 8 or not args.start.isdigit()):
        print("参数错误: --start 必须是 YYYYMMDD 八位数字（fail-closed）", file=sys.stderr)
        return 2

    result = run_audit(args.db, args.start)
    print(format_report(result))

    if args.json_path:
        try:
            Path(args.json_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            print("写 JSON 失败: " + str(exc) + "（fail-closed）", file=sys.stderr)
            return 2
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
