"""数据源健康监控系统测试"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from aqsp.data.source_health import (
    DataSourceMonitor,
    HealthCheckResult,
    read_source_health,
    record_source_failure,
    record_source_success,
    prioritize_source_ids,
)


@pytest.fixture
def temp_health_file(tmp_path: Path) -> Path:
    """创建临时健康状态文件"""
    health_file = tmp_path / "source_health.json"
    return health_file


@pytest.fixture
def monitor(temp_health_file: Path) -> DataSourceMonitor:
    """创建测试用的数据源监控器"""
    return DataSourceMonitor(
        health_path=temp_health_file,
        check_timeout_seconds=5.0,
        failure_threshold=3,
    )


def test_health_check_success(monitor: DataSourceMonitor) -> None:
    """测试成功的健康检查"""

    def mock_check() -> bool:
        time.sleep(0.01)  # 模拟检查耗时
        return True

    result = monitor.check_source_health("test_source", mock_check)

    assert result.source_id == "test_source"
    assert result.success is True
    assert result.response_time_ms > 0
    assert result.error_message == ""


def test_health_check_failure(monitor: DataSourceMonitor) -> None:
    """测试失败的健康检查"""

    def mock_check() -> bool:
        raise ValueError("连接失败")

    result = monitor.check_source_health("test_source", mock_check)

    assert result.source_id == "test_source"
    assert result.success is False
    assert result.response_time_ms >= 0
    assert "ValueError" in result.error_message
    assert "连接失败" in result.error_message


def test_record_check_result_success(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试记录成功的检查结果"""
    result = HealthCheckResult(
        source_id="eastmoney",
        success=True,
        response_time_ms=123.45,
    )

    monitor.record_check_result(result)

    health = read_source_health(temp_health_file)
    assert health["sources"]["eastmoney"]["successes"] == 1
    assert health["sources"]["eastmoney"]["failures"] == 0
    assert health["sources"]["eastmoney"]["consecutive_failures"] == 0
    assert health["sources"]["eastmoney"]["avg_response_time_ms"] == 123.45


def test_record_check_result_failure(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试记录失败的检查结果"""
    result = HealthCheckResult(
        source_id="sina",
        success=False,
        response_time_ms=50.0,
        error_message="网络超时",
    )

    monitor.record_check_result(result)

    health = read_source_health(temp_health_file)
    assert health["sources"]["sina"]["successes"] == 0
    assert health["sources"]["sina"]["failures"] == 1
    assert health["sources"]["sina"]["consecutive_failures"] == 1
    assert health["sources"]["sina"]["last_error"] == "网络超时"


def test_consecutive_failures(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试连续失败计数"""
    source_id = "test_source"

    # 连续失败3次
    for i in range(3):
        result = HealthCheckResult(
            source_id=source_id,
            success=False,
            response_time_ms=10.0,
            error_message=f"失败 #{i+1}",
        )
        monitor.record_check_result(result)

    health = read_source_health(temp_health_file)
    assert health["sources"][source_id]["consecutive_failures"] == 3
    assert health["sources"][source_id]["failures"] == 3

    # 一次成功后重置连续失败计数
    success_result = HealthCheckResult(
        source_id=source_id,
        success=True,
        response_time_ms=100.0,
    )
    monitor.record_check_result(success_result)

    health = read_source_health(temp_health_file)
    assert health["sources"][source_id]["consecutive_failures"] == 0
    assert health["sources"][source_id]["failures"] == 3  # 总失败次数不变
    assert health["sources"][source_id]["successes"] == 1


def test_get_source_status(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试获取数据源状态"""
    source_id = "eastmoney"

    # 记录一些历史数据
    for _ in range(7):
        record_source_success(source_id, source_id, path=temp_health_file, response_time_ms=100.0)

    for _ in range(3):
        record_source_failure(source_id, "测试错误", path=temp_health_file)

    status = monitor.get_source_status(source_id)

    assert status.source_id == source_id
    assert status.total_checks == 10
    assert status.success_rate == 0.7
    assert status.consecutive_failures == 3
    assert not status.is_healthy  # 连续失败3次，达到阈值


def test_should_trigger_failover(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试故障切换触发条件"""
    source_id = "test_source"

    # 初始状态不应触发切换
    assert not monitor.should_trigger_failover(source_id)

    # 连续失败2次，未达阈值
    for _ in range(2):
        record_source_failure(source_id, "错误", path=temp_health_file)

    assert not monitor.should_trigger_failover(source_id)

    # 第3次失败，达到阈值
    record_source_failure(source_id, "错误", path=temp_health_file)
    assert monitor.should_trigger_failover(source_id)


def test_get_unhealthy_sources(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试获取不健康的数据源列表"""
    # 健康的源
    record_source_success("healthy_source", "healthy_source", path=temp_health_file)

    # 不健康的源（连续失败3次）
    for _ in range(3):
        record_source_failure("unhealthy_source", "错误", path=temp_health_file)

    # 部分失败但未达阈值
    record_source_success("partial_source", "partial_source", path=temp_health_file)
    record_source_failure("partial_source", "错误", path=temp_health_file)

    unhealthy = monitor.get_unhealthy_sources()

    assert len(unhealthy) == 1
    assert unhealthy[0].source_id == "unhealthy_source"
    assert unhealthy[0].consecutive_failures == 3


def test_generate_health_report(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试生成健康报告"""
    # 准备测试数据
    record_source_success("source1", "source1", path=temp_health_file, response_time_ms=50.0)
    record_source_success("source2", "source2", path=temp_health_file, response_time_ms=150.0)

    for _ in range(3):
        record_source_failure("source3", "持续失败", path=temp_health_file)

    report = monitor.generate_health_report()

    assert "generated_at" in report
    assert report["total_sources"] == 3
    assert report["healthy_sources"] == 2
    assert report["unhealthy_sources"] == 1

    # 检查各个源的状态
    assert report["sources"]["source1"]["is_healthy"] is True
    assert report["sources"]["source1"]["success_rate"] == 1.0

    assert report["sources"]["source2"]["is_healthy"] is True
    assert report["sources"]["source2"]["avg_response_time_ms"] == 150.0

    assert report["sources"]["source3"]["is_healthy"] is False
    assert report["sources"]["source3"]["consecutive_failures"] == 3


def test_response_time_tracking(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试响应时间跟踪"""
    source_id = "test_source"
    response_times = [100.0, 150.0, 200.0, 250.0, 300.0]

    for rt in response_times:
        record_source_success(source_id, source_id, path=temp_health_file, response_time_ms=rt)

    status = monitor.get_source_status(source_id)
    expected_avg = sum(response_times) / len(response_times)

    assert abs(status.avg_response_time_ms - expected_avg) < 0.01


def test_response_time_limit(temp_health_file: Path) -> None:
    """测试响应时间历史记录限制（最多100条）"""
    source_id = "test_source"

    # 记录150次成功
    for i in range(150):
        record_source_success(
            source_id,
            source_id,
            path=temp_health_file,
            response_time_ms=float(i),
        )

    health = read_source_health(temp_health_file)
    response_times = health["sources"][source_id]["response_times"]

    # 应该只保留最后100条
    assert len(response_times) == 100
    assert response_times[0] == 50.0  # 第51条记录
    assert response_times[-1] == 149.0  # 第150条记录


def test_prioritize_source_ids_by_health(temp_health_file: Path) -> None:
    """测试根据健康状态优先排序数据源"""
    # 准备测试数据
    # source1: 最健康（有成功记录，无失败）
    record_source_success("source1", "source1", path=temp_health_file)

    # source2: 中等（有成功和失败）
    record_source_success("source2", "source2", path=temp_health_file)
    record_source_failure("source2", "偶尔失败", path=temp_health_file)

    # source3: 不健康（连续失败）
    for _ in range(3):
        record_source_failure("source3", "持续失败", path=temp_health_file)

    # source4: 无历史记录（冷启动）
    # 不记录任何数据

    original_order = ["source4", "source3", "source2", "source1"]
    prioritized = prioritize_source_ids(original_order, path=temp_health_file)

    # source1 应该排在最前面（健康）
    # source2 其次（部分失败）
    # source4 和 source3 在后面（无记录或持续失败）
    assert prioritized.index("source1") < prioritized.index("source2")
    assert prioritized.index("source2") < prioritized.index("source3")


def test_should_alert(monitor: DataSourceMonitor, temp_health_file: Path) -> None:
    """测试告警触发条件"""
    source_id = "test_source"

    # 记录2次失败，未达阈值
    for _ in range(2):
        record_source_failure(source_id, "错误", path=temp_health_file)

    status = monitor.get_source_status(source_id)
    assert not status.should_alert(failure_threshold=3)

    # 第3次失败，达到阈值
    record_source_failure(source_id, "错误", path=temp_health_file)

    status = monitor.get_source_status(source_id)
    assert status.should_alert(failure_threshold=3)


def test_health_file_creation(tmp_path: Path) -> None:
    """测试健康文件自动创建"""
    health_file = tmp_path / "data" / "source_health.json"
    assert not health_file.exists()

    # 记录数据应该自动创建目录和文件
    record_source_success("test", "test", path=health_file)

    assert health_file.exists()
    assert health_file.parent.exists()


def test_empty_health_file(temp_health_file: Path) -> None:
    """测试空健康文件的处理"""
    # 不存在的文件
    health = read_source_health(temp_health_file)
    assert health["sources"] == {}
    assert health["consecutive_failures"] == 0

    monitor = DataSourceMonitor(health_path=temp_health_file)
    status = monitor.get_source_status("nonexistent")

    assert status.total_checks == 0
    assert status.success_rate == 0.0
    assert status.is_healthy is True  # 无数据视为健康


def test_corrupted_health_file(temp_health_file: Path) -> None:
    """测试损坏的健康文件处理"""
    # 写入无效的JSON
    temp_health_file.write_text("invalid json content")

    # 应该返回空健康状态而不是崩溃
    health = read_source_health(temp_health_file)
    assert health["sources"] == {}
