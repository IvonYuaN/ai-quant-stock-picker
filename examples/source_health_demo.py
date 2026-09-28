#!/usr/bin/env python3
"""演示如何使用数据源健康监控 API"""

from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))

from aqsp.data.source_health import (
    DataSourceMonitor,
    read_source_health,
    record_source_success,
    record_source_failure,
    prioritize_source_ids,
)


def example_basic_usage():
    """基本用法示例"""
    print("=" * 60)
    print("示例 1: 基本用法")
    print("=" * 60)

    # 创建监控器
    monitor = DataSourceMonitor(
        health_path="data/source_health_example.json",
        failure_threshold=3,
    )

    # 模拟健康检查
    def check_eastmoney():
        # 实际场景中这里会真正检查数据源
        return True

    result = monitor.check_source_health("eastmoney", check_eastmoney)
    monitor.record_check_result(result)

    print(f"✓ 数据源: {result.source_id}")
    print(f"  成功: {result.success}")
    print(f"  响应时间: {result.response_time_ms:.2f}ms")
    print()


def example_record_events():
    """记录事件示例"""
    print("=" * 60)
    print("示例 2: 记录成功和失败事件")
    print("=" * 60)

    health_path = "data/source_health_example.json"

    # 记录成功
    record_source_success(
        "eastmoney",
        "eastmoney",
        path=health_path,
        response_time_ms=150.5,
    )
    print("✓ 记录成功事件: eastmoney (150.5ms)")

    # 记录失败
    record_source_failure(
        "sina",
        "网络超时",
        path=health_path,
    )
    print("✗ 记录失败事件: sina (网络超时)")
    print()


def example_query_status():
    """查询状态示例"""
    print("=" * 60)
    print("示例 3: 查询数据源状态")
    print("=" * 60)

    monitor = DataSourceMonitor(
        health_path="data/source_health_example.json",
    )

    # 查询单个源
    status = monitor.get_source_status("eastmoney")
    print(f"数据源: {status.source_id}")
    print(f"  健康: {status.is_healthy}")
    print(f"  连续失败: {status.consecutive_failures}")
    print(f"  成功率: {status.success_rate:.1%}")
    print(f"  平均响应时间: {status.avg_response_time_ms:.2f}ms")
    print()

    # 查询所有不健康的源
    unhealthy = monitor.get_unhealthy_sources()
    if unhealthy:
        print(f"不健康的数据源 ({len(unhealthy)}):")
        for s in unhealthy:
            print(f"  - {s.source_id}: 连续失败 {s.consecutive_failures} 次")
    else:
        print("✓ 所有数据源都健康")
    print()


def example_prioritize_sources():
    """优先级排序示例"""
    print("=" * 60)
    print("示例 4: 根据健康状态优化数据源顺序")
    print("=" * 60)

    health_path = "data/source_health_example.json"

    # 模拟一些健康数据
    for _ in range(10):
        record_source_success("eastmoney", "eastmoney", path=health_path)
    for _ in range(5):
        record_source_success("sina", "sina", path=health_path)
    for _ in range(3):
        record_source_failure("tencent", "连接失败", path=health_path)

    # 原始顺序
    original = ["tencent", "sina", "eastmoney"]
    print(f"原始顺序: {original}")

    # 优化后的顺序
    prioritized = prioritize_source_ids(original, path=health_path)
    print(f"优化顺序: {prioritized}")
    print()


def example_health_report():
    """生成健康报告示例"""
    print("=" * 60)
    print("示例 5: 生成健康报告")
    print("=" * 60)

    monitor = DataSourceMonitor(
        health_path="data/source_health_example.json",
    )

    report = monitor.generate_health_report()

    print(f"生成时间: {report['generated_at']}")
    print(f"总数据源: {report['total_sources']}")
    print(f"健康数据源: {report['healthy_sources']}")
    print(f"不健康数据源: {report['unhealthy_sources']}")
    print()

    print("详细状态:")
    for source_id, status in report['sources'].items():
        health_icon = "✓" if status['is_healthy'] else "✗"
        print(f"  {health_icon} {source_id}: 成功率 {status['success_rate']:.1%}, "
              f"连续失败 {status['consecutive_failures']} 次")
    print()


def example_failover_trigger():
    """故障切换触发示例"""
    print("=" * 60)
    print("示例 6: 判断是否应触发故障切换")
    print("=" * 60)

    monitor = DataSourceMonitor(
        health_path="data/source_health_example.json",
        failure_threshold=3,
    )

    # 检查各个源是否需要切换
    sources = ["eastmoney", "sina", "tencent"]
    for source_id in sources:
        should_failover = monitor.should_trigger_failover(source_id)
        status = monitor.get_source_status(source_id)

        if should_failover:
            print(f"⚠️  {source_id}: 需要切换 (连续失败 {status.consecutive_failures} 次)")
        else:
            print(f"✓ {source_id}: 正常")
    print()


def main():
    """运行所有示例"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "数据源健康监控 API 示例" + " " * 15 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    try:
        example_basic_usage()
        example_record_events()
        example_query_status()
        example_prioritize_sources()
        example_health_report()
        example_failover_trigger()

        print("=" * 60)
        print("✓ 所有示例执行完成")
        print("=" * 60)
        print()
        print("提示: 查看生成的健康文件:")
        print("  cat data/source_health_example.json")
        print()

    except Exception as exc:
        print(f"\n✗ 示例执行失败: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
