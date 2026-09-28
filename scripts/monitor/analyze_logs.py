#!/usr/bin/env python3
"""日志分析脚本。

解析 JSON Lines 格式的结构化日志，提供统计和分析功能。

功能：
- 按日志级别统计
- 提取错误和警告信息
- 时间范围过滤
- 字段值统计
- 生成简报

用法：
    # 分析单个日志文件
    python scripts/analyze_logs.py /path/to/aqsp.log

    # 分析多个日志文件
    python scripts/analyze_logs.py /path/to/logs/*.log

    # 指定时间范围
    python scripts/analyze_logs.py aqsp.log --since "2026-09-27" --until "2026-09-28"

    # 只看错误
    python scripts/analyze_logs.py aqsp.log --level error

    # 导出为 JSON
    python scripts/analyze_logs.py aqsp.log --output report.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def parse_log_line(line: str) -> dict[str, Any] | None:
    """解析一行 JSON Lines 日志。

    Args:
        line: JSON 字符串。

    Returns:
        解析后的字典，解析失败返回 None。
    """
    line = line.strip()
    if not line:
        return None

    try:
        return json.loads(line)
    except json.JSONDecodeError:
        # 可能是非结构化日志（迁移前的遗留日志）
        return None


def read_log_file(path: Path) -> list[dict[str, Any]]:
    """读取日志文件（支持 .gz 压缩文件）。

    Args:
        path: 日志文件路径。

    Returns:
        解析后的日志条目列表。
    """
    entries = []

    if path.suffix == ".gz":
        opener = gzip.open
        mode = "rt"
    else:
        opener = open
        mode = "r"

    try:
        with opener(path, mode, encoding="utf-8") as f:
            for line in f:
                entry = parse_log_line(line)
                if entry:
                    entries.append(entry)
    except Exception as e:
        print(f"警告: 无法读取 {path}: {e}", file=sys.stderr)

    return entries


def filter_entries(
    entries: list[dict[str, Any]],
    level: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """过滤日志条目。

    Args:
        entries: 日志条目列表。
        level: 只保留指定级别（不区分大小写）。
        since: 起始时间（包含）。
        until: 结束时间（不包含）。

    Returns:
        过滤后的条目列表。
    """
    filtered = entries

    if level:
        level_lower = level.lower()
        filtered = [e for e in filtered if e.get("level", "").lower() == level_lower]

    if since or until:
        filtered_by_time = []
        for entry in filtered:
            ts_str = entry.get("timestamp")
            if not ts_str:
                continue

            try:
                # 解析 ISO 8601 时间戳
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

                if since and ts < since:
                    continue
                if until and ts >= until:
                    continue

                filtered_by_time.append(entry)
            except (ValueError, TypeError):
                # 时间戳格式不对，跳过
                continue

        filtered = filtered_by_time

    return filtered


def analyze_levels(entries: list[dict[str, Any]]) -> dict[str, int]:
    """统计各级别日志数量。

    Args:
        entries: 日志条目列表。

    Returns:
        级别 -> 数量的字典。
    """
    counter = Counter(e.get("level", "unknown").upper() for e in entries)
    return dict(counter)


def analyze_events(entries: list[dict[str, Any]], top_n: int = 10) -> dict[str, int]:
    """统计最常见的事件。

    Args:
        entries: 日志条目列表。
        top_n: 返回前 N 个事件。

    Returns:
        事件名 -> 数量的字典。
    """
    counter = Counter(e.get("event", "unknown") for e in entries)
    return dict(counter.most_common(top_n))


def extract_errors(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取错误和严重错误日志。

    Args:
        entries: 日志条目列表。

    Returns:
        错误条目列表。
    """
    return [
        e
        for e in entries
        if e.get("level", "").lower() in ("error", "critical")
    ]


def extract_warnings(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取警告日志。

    Args:
        entries: 日志条目列表。

    Returns:
        警告条目列表。
    """
    return [e for e in entries if e.get("level", "").lower() == "warning"]


def analyze_field_values(
    entries: list[dict[str, Any]], field: str, top_n: int = 10
) -> dict[str, int]:
    """统计某个字段的值分布。

    Args:
        entries: 日志条目列表。
        field: 字段名（如 "module", "symbol", "source"）。
        top_n: 返回前 N 个值。

    Returns:
        字段值 -> 数量的字典。
    """
    values = [e.get(field) for e in entries if field in e]
    counter = Counter(values)
    return dict(counter.most_common(top_n))


def generate_report(
    entries: list[dict[str, Any]],
    include_errors: bool = True,
    include_warnings: bool = True,
) -> dict[str, Any]:
    """生成日志分析报告。

    Args:
        entries: 日志条目列表。
        include_errors: 是否包含错误详情。
        include_warnings: 是否包含警告详情。

    Returns:
        报告字典。
    """
    report: dict[str, Any] = {
        "total_entries": len(entries),
        "levels": analyze_levels(entries),
        "top_events": analyze_events(entries, top_n=10),
        "top_modules": analyze_field_values(entries, "module", top_n=10),
    }

    # 错误统计
    errors = extract_errors(entries)
    report["error_count"] = len(errors)

    if include_errors and errors:
        report["errors"] = [
            {
                "timestamp": e.get("timestamp"),
                "event": e.get("event"),
                "module": e.get("module"),
                "message": e.get("exception") or e.get("error") or "未知错误",
            }
            for e in errors[:20]  # 最多显示 20 条
        ]

    # 警告统计
    warnings = extract_warnings(entries)
    report["warning_count"] = len(warnings)

    if include_warnings and warnings:
        report["warnings"] = [
            {
                "timestamp": w.get("timestamp"),
                "event": w.get("event"),
                "module": w.get("module"),
            }
            for w in warnings[:20]  # 最多显示 20 条
        ]

    return report


def print_report(report: dict[str, Any]) -> None:
    """打印报告到控制台。

    Args:
        report: 报告字典。
    """
    print("=" * 60)
    print("日志分析报告".center(60))
    print("=" * 60)
    print()

    print(f"总条数: {report['total_entries']}")
    print()

    print("日志级别分布:")
    for level, count in sorted(report["levels"].items()):
        percentage = (count / report["total_entries"] * 100) if report["total_entries"] > 0 else 0
        print(f"  {level:10s}: {count:6d} ({percentage:5.1f}%)")
    print()

    if report.get("top_events"):
        print("最常见事件 (Top 10):")
        for event, count in report["top_events"].items():
            print(f"  {event:40s}: {count:6d}")
        print()

    if report.get("top_modules"):
        print("最活跃模块 (Top 10):")
        for module, count in report["top_modules"].items():
            print(f"  {module:40s}: {count:6d}")
        print()

    # 错误摘要
    error_count = report.get("error_count", 0)
    if error_count > 0:
        print(f"⚠️  发现 {error_count} 条错误日志")
        errors = report.get("errors", [])
        if errors:
            print()
            print("错误详情（最近 20 条）:")
            for i, err in enumerate(errors, 1):
                print(f"  {i}. [{err['timestamp']}] {err['event']}")
                print(f"     模块: {err['module']}")
                msg = err.get("message", "")
                if msg:
                    # 截断过长的消息
                    if len(msg) > 100:
                        msg = msg[:97] + "..."
                    print(f"     消息: {msg}")
                print()

    # 警告摘要
    warning_count = report.get("warning_count", 0)
    if warning_count > 0:
        print(f"⚠️  发现 {warning_count} 条警告日志")
        warnings = report.get("warnings", [])
        if warnings:
            print()
            print("警告详情（最近 20 条）:")
            for i, warn in enumerate(warnings, 1):
                print(f"  {i}. [{warn['timestamp']}] {warn['event']} @ {warn['module']}")

    print()
    print("=" * 60)


def main() -> int:
    """主函数。"""
    parser = argparse.ArgumentParser(
        description="分析 AQSP 结构化日志（JSON Lines 格式）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 分析单个日志文件
  %(prog)s /var/log/aqsp/aqsp.log

  # 分析多个日志文件
  %(prog)s /var/log/aqsp/*.log

  # 只看错误日志
  %(prog)s aqsp.log --level error

  # 指定时间范围
  %(prog)s aqsp.log --since "2026-09-27" --until "2026-09-28"

  # 导出为 JSON
  %(prog)s aqsp.log --output report.json
        """,
    )

    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="日志文件路径（支持 .gz 压缩文件）",
    )

    parser.add_argument(
        "--level",
        choices=["debug", "info", "warning", "error", "critical"],
        help="只分析指定级别的日志",
    )

    parser.add_argument(
        "--since",
        type=str,
        help="起始时间（ISO 格式，如 2026-09-27 或 2026-09-27T10:00:00）",
    )

    parser.add_argument(
        "--until",
        type=str,
        help="结束时间（ISO 格式，如 2026-09-28 或 2026-09-28T10:00:00）",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="输出报告为 JSON 文件",
    )

    parser.add_argument(
        "--no-errors",
        action="store_true",
        help="不包含错误详情",
    )

    parser.add_argument(
        "--no-warnings",
        action="store_true",
        help="不包含警告详情",
    )

    args = parser.parse_args()

    # 解析时间范围
    since = None
    until = None

    if args.since:
        try:
            since = datetime.fromisoformat(args.since)
        except ValueError:
            print(f"错误: 无法解析起始时间 '{args.since}'", file=sys.stderr)
            return 1

    if args.until:
        try:
            until = datetime.fromisoformat(args.until)
        except ValueError:
            print(f"错误: 无法解析结束时间 '{args.until}'", file=sys.stderr)
            return 1

    # 读取所有日志文件
    all_entries = []
    for path in args.files:
        if not path.exists():
            print(f"警告: 文件不存在 {path}", file=sys.stderr)
            continue

        print(f"读取 {path}...", file=sys.stderr)
        entries = read_log_file(path)
        all_entries.extend(entries)

    if not all_entries:
        print("错误: 没有找到有效的日志条目", file=sys.stderr)
        return 1

    print(f"共读取 {len(all_entries)} 条日志", file=sys.stderr)
    print(file=sys.stderr)

    # 过滤日志
    filtered = filter_entries(all_entries, level=args.level, since=since, until=until)

    if not filtered:
        print("错误: 过滤后没有日志条目", file=sys.stderr)
        return 1

    if len(filtered) < len(all_entries):
        print(f"过滤后剩余 {len(filtered)} 条日志", file=sys.stderr)
        print(file=sys.stderr)

    # 生成报告
    report = generate_report(
        filtered,
        include_errors=not args.no_errors,
        include_warnings=not args.no_warnings,
    )

    # 输出报告
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"✓ 报告已保存到 {args.output}", file=sys.stderr)
    else:
        print_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
