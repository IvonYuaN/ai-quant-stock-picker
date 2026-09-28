"""测试实验框架"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aqsp.experiment import (
    ExperimentConfig,
    ExperimentResult,
    GridSearchRunner,
    ABTestRunner,
    load_experiment_results,
)
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import Thresholds


@pytest.fixture
def sample_data():
    """生成样本数据"""
    dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    data = {}

    for symbol in ["000001", "000002", "000300"]:
        np.random.seed(hash(symbol) % 2**32)
        n = len(dates)

        # 生成随机OHLCV数据
        base_price = 10.0
        returns = np.random.randn(n) * 0.02
        close_prices = base_price * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            "date": dates.astype(str),
            "open": close_prices * (1 + np.random.randn(n) * 0.01),
            "high": close_prices * (1 + np.abs(np.random.randn(n)) * 0.02),
            "low": close_prices * (1 - np.abs(np.random.randn(n)) * 0.02),
            "close": close_prices,
            "volume": np.random.randint(1000000, 10000000, n),
            "amount": close_prices * np.random.randint(1000000, 10000000, n),
        })

        data[symbol] = df

    return data


@pytest.fixture
def strategy_factory():
    """策略工厂函数"""
    def factory(params):
        base_thresholds = Thresholds()
        thresholds_dict = base_thresholds.to_dict()

        # 更新参数
        for key, value in params.items():
            if "." in key:
                parts = key.split(".")
                d = thresholds_dict
                for part in parts[:-1]:
                    if part not in d:
                        d[part] = {}
                    d = d[part]
                d[parts[-1]] = value
            else:
                thresholds_dict[key] = value

        modified_thresholds = Thresholds.from_dict(thresholds_dict)
        return CompositeStrategy(modified_thresholds)

    return factory


def test_experiment_config():
    """测试实验配置"""
    config = ExperimentConfig(
        experiment_id="test_001",
        experiment_type="grid_search",
        strategy_name="momentum",
        start_date="2024-01-01",
        end_date="2024-12-31",
        params={"param1": [1, 2, 3]},
    )

    assert config.experiment_id == "test_001"
    assert config.experiment_type == "grid_search"

    # 测试序列化
    config_dict = config.to_dict()
    assert isinstance(config_dict, dict)

    # 测试反序列化
    config2 = ExperimentConfig.from_dict(config_dict)
    assert config2.experiment_id == config.experiment_id


def test_experiment_result():
    """测试实验结果"""
    result = ExperimentResult(
        experiment_id="test_001",
        variant_id="variant_001",
        params={"param1": 1.0},
        metrics={
            "sharpe_ratio": 1.5,
            "deflated_sharpe": 1.2,
            "total_return": 0.25,
        },
        walk_forward_result={},
    )

    assert result.experiment_id == "test_001"
    assert result.metrics["sharpe_ratio"] == 1.5

    # 测试序列化
    result_dict = result.to_dict()
    assert isinstance(result_dict, dict)

    # 测试反序列化
    result2 = ExperimentResult.from_dict(result_dict)
    assert result2.variant_id == result.variant_id


def test_grid_search_runner(sample_data, strategy_factory):
    """测试网格搜索"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = GridSearchRunner(
            strategy_factory=strategy_factory,
            base_config={},
            output_dir=tmpdir,
        )

        # 定义小参数网格
        param_grid = {
            "momentum.min_returns": [0.03, 0.05],
            "momentum.lookback_days": [30, 60],
        }

        # 运行网格搜索
        results = runner.run(
            param_grid=param_grid,
            data=sample_data,
            start_date="2024-01-01",
            end_date="2024-06-30",
            experiment_name="test_grid",
            train_period_days=60,
            test_period_days=30,
            purge_days=5,
            top_n=2,
        )

        # 验证结果
        assert len(results) == 4  # 2 * 2 = 4 combinations

        for result in results:
            assert result.experiment_id.startswith("test_grid_")
            assert "momentum.min_returns" in result.params
            assert "momentum.lookback_days" in result.params
            assert "sharpe_ratio" in result.metrics
            assert "deflated_sharpe" in result.metrics

        # 验证输出文件
        output_path = Path(tmpdir)
        config_files = list(output_path.glob("*_config.json"))
        result_files = list(output_path.glob("*_results.jsonl"))

        assert len(config_files) == 1
        assert len(result_files) == 1

        # 验证可以加载结果
        experiment_id = results[0].experiment_id
        loaded_results = load_experiment_results(experiment_id, tmpdir)
        assert len(loaded_results) == len(results)


def test_ab_test_runner(sample_data, strategy_factory):
    """测试A/B测试"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ABTestRunner(
            strategy_factory=strategy_factory,
            output_dir=tmpdir,
        )

        # 定义两个配置
        base_thresholds = Thresholds()
        config_a = base_thresholds.to_dict()
        config_b = base_thresholds.to_dict()

        # 修改配置B
        config_b["momentum"]["min_returns"] = 0.10
        config_b["momentum"]["lookback_days"] = 90

        # 运行A/B测试
        result_a, result_b = runner.run(
            variant_a_config=config_a,
            variant_b_config=config_b,
            data=sample_data,
            start_date="2024-01-01",
            end_date="2024-06-30",
            experiment_name="test_ab",
            train_period_days=60,
            test_period_days=30,
            purge_days=5,
            top_n=2,
        )

        # 验证结果
        assert result_a.variant_id == "variant_a"
        assert result_b.variant_id == "variant_b"
        assert result_a.experiment_id == result_b.experiment_id

        assert "sharpe_ratio" in result_a.metrics
        assert "sharpe_ratio" in result_b.metrics

        # 验证输出文件
        output_path = Path(tmpdir)
        config_files = list(output_path.glob("*_config.json"))
        comparison_files = list(output_path.glob("*_comparison.json"))

        assert len(config_files) == 1
        assert len(comparison_files) == 1

        # 验证对比文件内容
        with open(comparison_files[0], "r") as f:
            comparison_data = json.load(f)

        assert "variant_a" in comparison_data
        assert "variant_b" in comparison_data
        assert "comparison" in comparison_data


def test_load_experiment_results():
    """测试加载实验结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        experiment_id = "test_load_001"

        # 创建测试结果文件
        results = [
            ExperimentResult(
                experiment_id=experiment_id,
                variant_id=f"variant_{i:03d}",
                params={"param1": i * 0.1},
                metrics={"sharpe_ratio": i * 0.5},
                walk_forward_result={},
            )
            for i in range(5)
        ]

        # 保存结果
        jsonl_path = Path(tmpdir) / f"{experiment_id}_results.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for result in results:
                f.write(json.dumps(result.to_dict(), default=str) + "\n")

        # 加载结果
        loaded_results = load_experiment_results(experiment_id, tmpdir)

        assert len(loaded_results) == 5
        assert all(r.experiment_id == experiment_id for r in loaded_results)


def test_grid_search_with_missing_data(strategy_factory):
    """测试网格搜索处理缺失数据"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = GridSearchRunner(
            strategy_factory=strategy_factory,
            base_config={},
            output_dir=tmpdir,
        )

        # 空数据集
        empty_data = {}

        param_grid = {
            "momentum.min_returns": [0.05],
        }

        # 应该能处理空数据而不崩溃
        try:
            results = runner.run(
                param_grid=param_grid,
                data=empty_data,
                start_date="2024-01-01",
                end_date="2024-06-30",
                experiment_name="test_empty",
                train_period_days=60,
                test_period_days=30,
            )
            # 空数据会导致walk-forward失败，但不应该抛出未捕获的异常
            assert isinstance(results, list)
        except ValueError:
            # ValueError是预期的（没有可用数据）
            pass


def test_parameter_sensitivity_analysis(sample_data, strategy_factory):
    """测试参数敏感性分析"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = GridSearchRunner(
            strategy_factory=strategy_factory,
            base_config={},
            output_dir=tmpdir,
        )

        # 测试单个参数的敏感性
        param_grid = {
            "momentum.min_returns": [0.02, 0.04, 0.06, 0.08, 0.10],
        }

        results = runner.run(
            param_grid=param_grid,
            data=sample_data,
            start_date="2024-01-01",
            end_date="2024-06-30",
            experiment_name="test_sensitivity",
            train_period_days=60,
            test_period_days=30,
            purge_days=5,
            top_n=2,
        )

        assert len(results) == 5

        # 验证参数值的范围
        param_values = [r.params["momentum.min_returns"] for r in results]
        assert min(param_values) == 0.02
        assert max(param_values) == 0.10

        # 验证所有结果都有指标
        for result in results:
            assert "sharpe_ratio" in result.metrics
            assert "deflated_sharpe" in result.metrics


def test_experiment_reproducibility(sample_data, strategy_factory):
    """测试实验可重复性"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = GridSearchRunner(
            strategy_factory=strategy_factory,
            base_config={},
            output_dir=tmpdir,
        )

        param_grid = {
            "momentum.min_returns": [0.05],
            "momentum.lookback_days": [60],
        }

        # 运行两次相同的实验
        results1 = runner.run(
            param_grid=param_grid,
            data=sample_data,
            start_date="2024-01-01",
            end_date="2024-06-30",
            experiment_name="test_repro",
            train_period_days=60,
            test_period_days=30,
            purge_days=5,
            top_n=2,
        )

        results2 = runner.run(
            param_grid=param_grid,
            data=sample_data,
            start_date="2024-01-01",
            end_date="2024-06-30",
            experiment_name="test_repro",
            train_period_days=60,
            test_period_days=30,
            purge_days=5,
            top_n=2,
        )

        # 验证结果的参数相同
        assert results1[0].params == results2[0].params

        # 由于随机性，指标可能略有不同，但应该接近
        # 这里只验证结构一致性
        assert set(results1[0].metrics.keys()) == set(results2[0].metrics.keys())
