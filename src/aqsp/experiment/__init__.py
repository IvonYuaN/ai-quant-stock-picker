"""实验框架：策略参数优化和A/B测试

本模块提供参数网格搜索、A/B测试对比等实验功能。
遵循防前视偏差规则，复用现有的 walk-forward 测试框架。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Callable

import pandas as pd

from aqsp.backtest.walk_forward import WalkForwardTester, WalkForwardResult
from aqsp.core.time import now_shanghai
from aqsp.strategies.composite import CompositeStrategy

_LOGGER = logging.getLogger("aqsp.experiment")


@dataclass(frozen=True)
class ExperimentConfig:
    """实验配置"""
    experiment_id: str
    experiment_type: str  # "grid_search" or "ab_test"
    strategy_name: str
    start_date: str
    end_date: str
    params: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: now_shanghai().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentConfig":
        return cls(**data)


@dataclass(frozen=True)
class ExperimentResult:
    """单次实验结果"""
    experiment_id: str
    variant_id: str
    params: Dict[str, Any]
    metrics: Dict[str, float]
    walk_forward_result: Dict[str, Any]  # WalkForwardResult 序列化
    completed_at: str = field(default_factory=lambda: now_shanghai().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentResult":
        return cls(**data)


class GridSearchRunner:
    """参数网格搜索

    遍历参数空间，为每个参数组合运行 walk-forward 测试。
    """

    def __init__(
        self,
        strategy_factory: Callable[[Dict[str, Any]], CompositeStrategy],
        base_config: Dict[str, Any],
        output_dir: str = "experiments",
    ):
        """
        Args:
            strategy_factory: 根据参数创建策略的工厂函数
            base_config: 基础配置（thresholds 等）
            output_dir: 实验结果输出目录
        """
        self.strategy_factory = strategy_factory
        self.base_config = base_config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        param_grid: Dict[str, Sequence[Any]],
        data: Dict[str, pd.DataFrame],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        experiment_name: Optional[str] = None,
        **walkforward_kwargs,
    ) -> List[ExperimentResult]:
        """运行网格搜索

        Args:
            param_grid: 参数网格，例如 {"volume_ratio_min": [1.2, 1.35, 1.5]}
            data: 回测数据
            start_date: 起始日期
            end_date: 结束日期
            experiment_name: 实验名称
            **walkforward_kwargs: 传递给 WalkForwardTester 的参数

        Returns:
            实验结果列表
        """
        # 生成所有参数组合
        param_keys = list(param_grid.keys())
        param_values = [param_grid[k] for k in param_keys]
        param_combinations = list(itertools.product(*param_values))

        n_combinations = len(param_combinations)
        _LOGGER.info(f"Starting grid search with {n_combinations} parameter combinations")

        # 生成实验 ID
        experiment_id = self._generate_experiment_id(
            experiment_name or "grid_search",
            param_grid,
            start_date,
            end_date,
        )

        # 保存实验配置
        config = ExperimentConfig(
            experiment_id=experiment_id,
            experiment_type="grid_search",
            strategy_name=experiment_name or "unknown",
            start_date=start_date or "auto",
            end_date=end_date or "auto",
            params=param_grid,
            metadata={
                "n_combinations": n_combinations,
                "base_config": self.base_config,
                "walkforward_kwargs": walkforward_kwargs,
            },
        )
        self._save_config(config)

        results: List[ExperimentResult] = []

        for idx, param_values_tuple in enumerate(param_combinations, 1):
            params = dict(zip(param_keys, param_values_tuple))
            variant_id = f"variant_{idx:03d}"

            _LOGGER.info(
                f"Running combination {idx}/{n_combinations} ({variant_id}): {params}"
            )

            try:
                # 创建策略实例
                strategy = self.strategy_factory(params)

                # 运行 walk-forward 测试
                tester = WalkForwardTester(
                    strategy=strategy,
                    **walkforward_kwargs,
                )
                wf_result = tester.run(data, start_date, end_date)

                # 提取关键指标
                metrics = self._extract_metrics(wf_result)

                # 创建实验结果
                result = ExperimentResult(
                    experiment_id=experiment_id,
                    variant_id=variant_id,
                    params=params,
                    metrics=metrics,
                    walk_forward_result=self._serialize_wf_result(wf_result),
                )

                results.append(result)

                # 增量保存结果
                self._append_result(result)

                _LOGGER.info(
                    f"  Completed: Sharpe={metrics.get('sharpe_ratio', 0):.2f}, "
                    f"DSR={metrics.get('deflated_sharpe', 0):.2f}, "
                    f"Return={metrics.get('total_return', 0):.2%}"
                )

            except Exception as e:
                _LOGGER.error(f"Failed to run combination {idx}: {e}", exc_info=True)
                continue

        _LOGGER.info(f"Grid search completed: {len(results)}/{n_combinations} successful")

        return results

    def _generate_experiment_id(
        self,
        name: str,
        param_grid: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> str:
        """生成实验 ID"""
        content = f"{name}_{param_grid}_{start_date}_{end_date}_{now_shanghai()}"
        hash_hex = hashlib.sha256(content.encode()).hexdigest()[:12]
        timestamp = now_shanghai().strftime("%Y%m%d_%H%M%S")
        return f"{name}_{timestamp}_{hash_hex}"

    def _extract_metrics(self, wf_result: WalkForwardResult) -> Dict[str, float]:
        """提取关键指标"""
        overall = wf_result.overall
        return {
            "total_return": overall.total_return,
            "annual_return": overall.annual_return,
            "max_drawdown": overall.max_drawdown,
            "sharpe_ratio": overall.sharpe_ratio,
            "win_rate": overall.win_rate,
            "profit_factor": overall.profit_factor,
            "trades": float(overall.trades),
            "deflated_sharpe": wf_result.deflated_sharpe,
            "pbo": wf_result.pbo if wf_result.pbo is not None else -1.0,
            "robustness_score": wf_result.robustness_score,
            "parameter_std": wf_result.parameter_std,
        }

    def _serialize_wf_result(self, wf_result: WalkForwardResult) -> Dict[str, Any]:
        """序列化 WalkForwardResult"""
        return {
            "overall": asdict(wf_result.overall),
            "deflated_sharpe": wf_result.deflated_sharpe,
            "pbo": wf_result.pbo,
            "robustness_score": wf_result.robustness_score,
            "parameter_std": wf_result.parameter_std,
            "regime_winrates": wf_result.regime_winrates or {},
            "n_periods": len(wf_result.periods),
        }

    def _save_config(self, config: ExperimentConfig) -> None:
        """保存实验配置"""
        config_path = self.output_dir / f"{config.experiment_id}_config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    def _append_result(self, result: ExperimentResult) -> None:
        """增量追加实验结果到 JSONL 文件"""
        jsonl_path = self.output_dir / f"{result.experiment_id}_results.jsonl"
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), ensure_ascii=False, default=str) + "\n")


class ABTestRunner:
    """A/B 测试对比

    对比两个配置变体的性能。
    """

    def __init__(
        self,
        strategy_factory: Callable[[Dict[str, Any]], CompositeStrategy],
        output_dir: str = "experiments",
    ):
        """
        Args:
            strategy_factory: 根据配置创建策略的工厂函数
            output_dir: 实验结果输出目录
        """
        self.strategy_factory = strategy_factory
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        variant_a_config: Dict[str, Any],
        variant_b_config: Dict[str, Any],
        data: Dict[str, pd.DataFrame],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        experiment_name: str = "ab_test",
        **walkforward_kwargs,
    ) -> tuple[ExperimentResult, ExperimentResult]:
        """运行 A/B 测试

        Args:
            variant_a_config: 变体 A 的配置
            variant_b_config: 变体 B 的配置
            data: 回测数据
            start_date: 起始日期
            end_date: 结束日期
            experiment_name: 实验名称
            **walkforward_kwargs: 传递给 WalkForwardTester 的参数

        Returns:
            (变体 A 结果, 变体 B 结果)
        """
        experiment_id = self._generate_experiment_id(
            experiment_name,
            start_date,
            end_date,
        )

        _LOGGER.info(f"Starting A/B test: {experiment_id}")

        # 保存实验配置
        config = ExperimentConfig(
            experiment_id=experiment_id,
            experiment_type="ab_test",
            strategy_name=experiment_name,
            start_date=start_date or "auto",
            end_date=end_date or "auto",
            params={
                "variant_a": variant_a_config,
                "variant_b": variant_b_config,
            },
            metadata={"walkforward_kwargs": walkforward_kwargs},
        )
        self._save_config(config)

        # 运行变体 A
        _LOGGER.info("Running variant A...")
        result_a = self._run_variant(
            experiment_id=experiment_id,
            variant_id="variant_a",
            config=variant_a_config,
            data=data,
            start_date=start_date,
            end_date=end_date,
            **walkforward_kwargs,
        )

        # 运行变体 B
        _LOGGER.info("Running variant B...")
        result_b = self._run_variant(
            experiment_id=experiment_id,
            variant_id="variant_b",
            config=variant_b_config,
            data=data,
            start_date=start_date,
            end_date=end_date,
            **walkforward_kwargs,
        )

        # 保存对比结果
        self._save_comparison(experiment_id, result_a, result_b)

        _LOGGER.info("A/B test completed")

        return result_a, result_b

    def _run_variant(
        self,
        experiment_id: str,
        variant_id: str,
        config: Dict[str, Any],
        data: Dict[str, pd.DataFrame],
        start_date: Optional[str],
        end_date: Optional[str],
        **walkforward_kwargs,
    ) -> ExperimentResult:
        """运行单个变体"""
        strategy = self.strategy_factory(config)

        tester = WalkForwardTester(
            strategy=strategy,
            **walkforward_kwargs,
        )
        wf_result = tester.run(data, start_date, end_date)

        metrics = self._extract_metrics(wf_result)

        result = ExperimentResult(
            experiment_id=experiment_id,
            variant_id=variant_id,
            params=config,
            metrics=metrics,
            walk_forward_result=self._serialize_wf_result(wf_result),
        )

        # 保存结果
        self._save_result(result)

        _LOGGER.info(
            f"  {variant_id}: Sharpe={metrics.get('sharpe_ratio', 0):.2f}, "
            f"DSR={metrics.get('deflated_sharpe', 0):.2f}, "
            f"Return={metrics.get('total_return', 0):.2%}"
        )

        return result

    def _generate_experiment_id(
        self,
        name: str,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> str:
        """生成实验 ID"""
        timestamp = now_shanghai().strftime("%Y%m%d_%H%M%S")
        return f"{name}_{timestamp}"

    def _extract_metrics(self, wf_result: WalkForwardResult) -> Dict[str, float]:
        """提取关键指标"""
        overall = wf_result.overall
        return {
            "total_return": overall.total_return,
            "annual_return": overall.annual_return,
            "max_drawdown": overall.max_drawdown,
            "sharpe_ratio": overall.sharpe_ratio,
            "win_rate": overall.win_rate,
            "profit_factor": overall.profit_factor,
            "trades": float(overall.trades),
            "deflated_sharpe": wf_result.deflated_sharpe,
            "pbo": wf_result.pbo if wf_result.pbo is not None else -1.0,
            "robustness_score": wf_result.robustness_score,
        }

    def _serialize_wf_result(self, wf_result: WalkForwardResult) -> Dict[str, Any]:
        """序列化 WalkForwardResult"""
        return {
            "overall": asdict(wf_result.overall),
            "deflated_sharpe": wf_result.deflated_sharpe,
            "pbo": wf_result.pbo,
            "robustness_score": wf_result.robustness_score,
            "regime_winrates": wf_result.regime_winrates or {},
            "n_periods": len(wf_result.periods),
        }

    def _save_config(self, config: ExperimentConfig) -> None:
        """保存实验配置"""
        config_path = self.output_dir / f"{config.experiment_id}_config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    def _save_result(self, result: ExperimentResult) -> None:
        """保存实验结果"""
        result_path = self.output_dir / f"{result.experiment_id}_{result.variant_id}.json"
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    def _save_comparison(
        self,
        experiment_id: str,
        result_a: ExperimentResult,
        result_b: ExperimentResult,
    ) -> None:
        """保存对比结果"""
        comparison = {
            "experiment_id": experiment_id,
            "variant_a": result_a.to_dict(),
            "variant_b": result_b.to_dict(),
            "comparison": self._compute_comparison(result_a, result_b),
            "completed_at": now_shanghai().isoformat(),
        }

        comparison_path = self.output_dir / f"{experiment_id}_comparison.json"
        with open(comparison_path, "w", encoding="utf-8") as f:
            json.dump(comparison, f, ensure_ascii=False, indent=2, default=str)

    def _compute_comparison(
        self,
        result_a: ExperimentResult,
        result_b: ExperimentResult,
    ) -> Dict[str, Any]:
        """计算对比指标"""
        metrics_a = result_a.metrics
        metrics_b = result_b.metrics

        comparison = {}
        for key in metrics_a.keys():
            if key in metrics_b:
                val_a = metrics_a[key]
                val_b = metrics_b[key]
                diff = val_b - val_a
                pct_change = (diff / abs(val_a) * 100) if val_a != 0 else 0.0

                comparison[key] = {
                    "variant_a": val_a,
                    "variant_b": val_b,
                    "diff": diff,
                    "pct_change": pct_change,
                    "winner": "variant_b" if val_b > val_a else "variant_a" if val_b < val_a else "tie",
                }

        return comparison


def load_experiment_results(experiment_id: str, output_dir: str = "experiments") -> List[ExperimentResult]:
    """加载实验结果

    Args:
        experiment_id: 实验 ID
        output_dir: 实验结果目录

    Returns:
        实验结果列表
    """
    results = []
    jsonl_path = Path(output_dir) / f"{experiment_id}_results.jsonl"

    if not jsonl_path.exists():
        return results

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                results.append(ExperimentResult.from_dict(data))
            except Exception as e:
                _LOGGER.warning(f"Failed to parse result line: {e}")
                continue

    return results


__all__ = [
    "ExperimentConfig",
    "ExperimentResult",
    "GridSearchRunner",
    "ABTestRunner",
    "load_experiment_results",
]
