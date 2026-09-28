#!/usr/bin/env python3
"""数据源健康监控服务

作为独立进程运行,定期检查所有数据源的健康状态,
在故障时发送告警,并记录健康指标。

环境变量:
    AQSP_ENABLE_SOURCE_MONITOR: 启用监控(默认false)
    AQSP_SOURCE_HEALTH: 健康状态文件路径(默认 data/source_health.json)
    AQSP_MONITOR_CHECK_INTERVAL_SECONDS: 检查间隔(默认300秒)
    AQSP_MONITOR_FAILURE_THRESHOLD: 失败阈值(默认3次)
    AQSP_MONITOR_CHECK_TIMEOUT_SECONDS: 单次检查超时(默认10秒)
    AQSP_MONITOR_DAEMON: 以守护进程模式运行(默认false)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

# Add project root to path（向上找 pyproject.toml，兼容脚本被移动到子目录/经软链调用）
for _candidate in Path(__file__).resolve().parent, *Path(__file__).resolve().parent.parents:
    if (_candidate / "pyproject.toml").is_file():
        project_root = _candidate
        break
else:
    project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))

from aqsp.core.time import now_shanghai, today_shanghai  # noqa: E402
from aqsp.data.source_health import (  # noqa: E402
    DataSourceMonitor,
    HealthCheckResult,
    source_health_path,
)
from aqsp.data.registry import list_registry_entries  # noqa: E402
from aqsp.data.source_health_alerts import build_source_health_alert  # noqa: E402

_logger = logging.getLogger(__name__)
_should_stop = False


def signal_handler(signum: int, frame: Any) -> None:
    """处理停止信号"""
    global _should_stop
    _logger.info(f"收到信号 {signum},准备停止监控")
    _should_stop = True


def get_config() -> dict[str, Any]:
    """从环境变量读取配置"""
    return {
        "enabled": os.getenv("AQSP_ENABLE_SOURCE_MONITOR", "false").lower() in {"true", "1", "yes"},
        "check_interval_seconds": int(os.getenv("AQSP_MONITOR_CHECK_INTERVAL_SECONDS", "300")),
        "failure_threshold": int(os.getenv("AQSP_MONITOR_FAILURE_THRESHOLD", "3")),
        "check_timeout_seconds": float(os.getenv("AQSP_MONITOR_CHECK_TIMEOUT_SECONDS", "10.0")),
        "daemon": os.getenv("AQSP_MONITOR_DAEMON", "false").lower() in {"true", "1", "yes"},
        "health_path": os.getenv("AQSP_SOURCE_HEALTH", ""),
    }


def check_data_source_basic(source_id: str) -> bool:
    """基础数据源健康检查"""
    try:
        from aqsp.data import build_data_source

        source = build_data_source(source_id)

        if hasattr(source, "get_available_symbols"):
            symbols = source.get_available_symbols()
            return len(symbols) > 0

        test_symbol = "000001.SZ"
        end = today_shanghai()
        start = end - timedelta(days=5)

        result = source.fetch_daily(
            symbols=[test_symbol],
            start=start,
            end=end,
            adjust="",
        )

        return test_symbol in result and not result[test_symbol].empty

    except Exception as exc:
        _logger.debug(f"数据源 {source_id} 健康检查失败: {exc}")
        return False


def perform_health_checks(monitor: DataSourceMonitor) -> list[HealthCheckResult]:
    """对所有运行时可用的数据源执行健康检查"""
    results: list[HealthCheckResult] = []

    registry_entries = list_registry_entries()
    ready_sources = [entry for entry in registry_entries if entry.runtime_ready]

    _logger.info(f"开始检查 {len(ready_sources)} 个数据源")

    for entry in ready_sources:
        source_id = entry.id
        _logger.debug(f"检查数据源: {source_id}")

        result = monitor.check_source_health(
            source_id=source_id,
            check_func=lambda sid=source_id: check_data_source_basic(sid),
        )

        results.append(result)
        monitor.record_check_result(result)

        status = "成功" if result.success else f"失败 ({result.error_message[:50]})"
        _logger.info(
            f"数据源 {source_id}: {status}, "
            f"响应时间 {result.response_time_ms:.0f}ms"
        )

    return results


def send_health_alerts(
    monitor: DataSourceMonitor,
    failure_threshold: int,
) -> bool:
    """发送数据源健康告警"""
    unhealthy = monitor.get_unhealthy_sources()

    if not unhealthy:
        return False

    critical_sources = [
        s for s in unhealthy
        if s.consecutive_failures >= failure_threshold
    ]

    if not critical_sources:
        return False

    alert_message = build_source_health_alert(
        critical_sources,
        failure_threshold=failure_threshold,
    )

    try:
        from aqsp.config import load_runtime_config
        from aqsp.notifier import notify_markdown_via_config, print_notify_results

        config = load_runtime_config()
        results = notify_markdown_via_config(alert_message, mode=config.notify_mode)
        print_notify_results(results, prefix="source health alert")

        return any(r.ok for r in results)
    except Exception as exc:
        _logger.error(f"发送健康告警失败: {exc}", exc_info=True)
        _logger.warning(f"未发送的告警内容:\n{alert_message}")
        return False


def run_monitoring_cycle(config: dict[str, Any]) -> None:
    """执行一次完整的监控周期"""
    _logger.info("=" * 60)
    _logger.info(f"开始监控周期 - {now_shanghai().strftime('%Y-%m-%d %H:%M:%S')}")

    monitor = DataSourceMonitor(
        health_path=config["health_path"] or None,
        check_timeout_seconds=config["check_timeout_seconds"],
        failure_threshold=config["failure_threshold"],
    )

    check_results = perform_health_checks(monitor)

    success_count = sum(1 for r in check_results if r.success)
    failure_count = len(check_results) - success_count

    _logger.info(
        f"健康检查完成: {success_count} 成功, {failure_count} 失败"
    )

    unhealthy = monitor.get_unhealthy_sources()

    if unhealthy:
        _logger.warning(f"发现 {len(unhealthy)} 个不健康的数据源")
        try:
            alert_sent = send_health_alerts(monitor, config["failure_threshold"])
            if alert_sent:
                _logger.info("健康告警已发送")
            else:
                _logger.info("不健康的数据源未达到告警阈值")
        except Exception as exc:
            _logger.error(f"发送告警失败: {exc}", exc_info=True)
    else:
        _logger.info("所有数据源健康,无需告警")

    report = monitor.generate_health_report()
    report_path = source_health_path(config["health_path"]).parent / "source_health_report.json"

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _logger.info(f"健康报告已保存到: {report_path}")
    except Exception as exc:
        _logger.error(f"保存健康报告失败: {exc}")

    _logger.info("监控周期完成")


def run_daemon(config: dict[str, Any]) -> None:
    """以守护进程模式持续运行监控"""
    interval = config["check_interval_seconds"]
    _logger.info(f"启动守护进程模式,检查间隔: {interval} 秒")
    _logger.info(f"健康文件路径: {source_health_path(config['health_path'])}")
    _logger.info(f"失败阈值: {config['failure_threshold']} 次")

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    cycle_count = 0

    while not _should_stop:
        cycle_count += 1
        try:
            _logger.info(f"\n{'=' * 60}")
            _logger.info(f"执行第 {cycle_count} 次监控周期")
            run_monitoring_cycle(config)
        except Exception as exc:
            _logger.error(f"监控周期执行失败: {exc}", exc_info=True)

        if _should_stop:
            break

        _logger.info(f"等待 {interval} 秒后进行下一次检查...")
        for _ in range(interval):
            if _should_stop:
                break
            time.sleep(1)

    _logger.info("监控服务已停止")


def main() -> int:
    """主入口"""
    parser = argparse.ArgumentParser(
        description="数据源健康监控服务",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="以守护进程模式运行(持续监控)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help="检查间隔(秒),默认300",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        metavar="COUNT",
        help="失败阈值(次),默认3",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="输出详细日志",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = get_config()

    if args.daemon:
        config["daemon"] = True
    if args.interval:
        config["check_interval_seconds"] = args.interval
    if args.threshold:
        config["failure_threshold"] = args.threshold

    if not config["enabled"] and not (args.daemon or args.interval or args.threshold):
        _logger.warning(
            "数据源监控未启用。设置环境变量 AQSP_ENABLE_SOURCE_MONITOR=true 或使用命令行参数启用。"
        )
        _logger.info("运行一次性检查...")

    try:
        if config["daemon"]:
            run_daemon(config)
        else:
            run_monitoring_cycle(config)
        return 0
    except KeyboardInterrupt:
        _logger.info("用户中断")
        return 130
    except Exception as exc:
        _logger.error(f"监控服务异常退出: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
