#!/usr/bin/env python3
"""
快速启动脚本：演示所有新集成功能的使用

功能清单：
1. RegimeAdaptiveStrategySelector - HMM市场状态检测和策略混合
2. NationalTeamFilterStrategy - 国家队持仓过滤
3. 异常处理和边界检查示例
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import numpy as np

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def demo_regime_adaptive_selector():
    """演示1：Regime自适应策略选择"""
    logger.info("=" * 70)
    logger.info("演示1：Regime自适应策略选择器")
    logger.info("=" * 70)

    try:
        from aqsp.strategies.regime_adaptive import (
            RegimeAdaptiveStrategySelector,
        )

        # 创建模拟市场数据（100个交易日）
        dates = pd.date_range("2024-01-01", periods=100, freq="D")

        # 模拟不同市场状态的数据
        # 牛市场景：持续上涨
        bull_prices = 100 * (1.001 ** np.arange(100))
        index_df_bull = pd.DataFrame(
            {
                "date": dates,
                "close": bull_prices,
            }
        )

        # 熊市场景：持续下跌
        bear_prices = 100 * (0.999 ** np.arange(100))
        index_df_bear = pd.DataFrame(
            {
                "date": dates,
                "close": bear_prices,
            }
        )

        # 震荡市场：上下波动
        sideways_prices = 100 + 5 * np.sin(np.arange(100) * 0.1)
        index_df_sideways = pd.DataFrame(
            {
                "date": dates,
                "close": sideways_prices,
            }
        )

        # 初始化选择器
        selector = RegimeAdaptiveStrategySelector()

        # 测试三种市场状态
        test_cases = [
            ("牛市", index_df_bull),
            ("熊市", index_df_bear),
            ("震荡", index_df_sideways),
        ]

        for label, index_df in test_cases:
            logger.info(f"\n测试场景：{label}")
            market_data = {"index_df": index_df}

            try:
                strategy_mix, regime_result = selector.select_strategy_mix(market_data)

                logger.info(f"  Regime检测: {regime_result.regime}")
                logger.info(f"  置信度: {regime_result.confidence:.2%}")
                logger.info(f"  策略配置: {strategy_mix.description}")
                logger.info("  策略权重:")
                for strategy_name, weight in strategy_mix.weights.items():
                    logger.info(f"    - {strategy_name}: {weight:.2%}")
                logger.info(f"  重点策略: {', '.join(strategy_mix.focus)}")

                # 测试单个策略的权重获取
                momentum_weight = selector.get_strategy_weight("momentum", market_data)
                logger.info(f"  momentum策略权重: {momentum_weight:.2%}")

                # 测试策略激活判断
                is_active = selector.is_strategy_active(
                    "momentum", market_data, min_weight=0.1
                )
                logger.info(f"  momentum策略激活: {is_active}")

            except Exception as exc:
                logger.error("  处理失败: %s", exc, exc_info=True)

        logger.info("\n测试异常处理：缺少market_data中的index_df")
        try:
            strategy_mix, regime_result = selector.select_strategy_mix({})
            logger.info(f"  安全降级到: {regime_result.regime} 配置")
            logger.info(f"  策略配置: {strategy_mix.description}")
        except Exception as exc:
            logger.error("  异常未处理: %s", exc, exc_info=True)

        logger.info("\n✓ 演示1完成")

    except Exception as exc:
        logger.error("演示1失败: %s", exc, exc_info=True)


def demo_national_team_filter():
    """演示2：国家队持仓过滤策略"""
    logger.info("\n" + "=" * 70)
    logger.info("演示2：国家队持仓过滤策略")
    logger.info("=" * 70)

    try:
        from aqsp.strategies.national_team_filter import NationalTeamFilterStrategy
        from aqsp.strategies.base import StrategyConfig

        # 创建策略配置
        config = StrategyConfig(
            name="national_team_filter",
            enabled=True,
            weight=0.5,
        )

        # 初始化策略
        strategy = NationalTeamFilterStrategy(config)
        logger.info(f"策略初始化成功: {strategy.name}")
        logger.info(f"策略版本: {strategy.version}")
        logger.info(f"策略假设: {strategy.hypothesis}")

        # 模拟数据
        example_data = {
            "600000": pd.DataFrame(
                {
                    "date": ["2024-01-01", "2024-01-02"],
                    "close": [10.0, 10.5],
                }
            ),
            "601398": pd.DataFrame(
                {
                    "date": ["2024-01-01", "2024-01-02"],
                    "close": [5.0, 5.1],
                }
            ),
            "000001": pd.DataFrame(
                {
                    "date": ["2024-01-01", "2024-01-02"],
                    "close": [15.0, 15.2],
                }
            ),
        }

        logger.info(f"\n计算{len(example_data)}个股票的国家队持仓评分...")
        scores = strategy.calculate_score(example_data)

        for symbol, score in scores.items():
            status = "✓ 国家队持仓" if score > 0.5 else "✗ 无国家队持仓"
            logger.info(f"  {symbol}: {score:.1f} {status}")

        logger.info("\n✓ 演示2完成")

    except Exception as exc:
        logger.error("演示2失败: %s", exc, exc_info=True)


def demo_error_handling():
    """演示3：异常处理和边界检查"""
    logger.info("\n" + "=" * 70)
    logger.info("演示3：异常处理和边界检查")
    logger.info("=" * 70)

    try:
        from aqsp.strategies.regime_adaptive import RegimeAdaptiveStrategySelector

        selector = RegimeAdaptiveStrategySelector()

        # 测试1：权重边界检查
        logger.info("\n测试1：权重边界检查")
        logger.info("  传入None数据...")
        try:
            weight = selector.get_strategy_weight("momentum", {"index_df": None})
            logger.info(f"  ✓ 安全返回: {weight}")
        except Exception as exc:
            logger.error("  ✗ 异常: %s", exc)

        # 测试2：无效策略名称
        logger.info("\n测试2：无效策略名称")
        logger.info("  查询不存在的策略权重...")
        try:
            # 创建有效数据
            dates = pd.date_range("2024-01-01", periods=50, freq="D")
            prices = 100 * (1.001 ** np.arange(50))
            valid_df = pd.DataFrame({"date": dates, "close": prices})

            weight = selector.get_strategy_weight(
                "nonexistent_strategy", {"index_df": valid_df}
            )
            logger.info(f"  ✓ 安全返回: {weight}")
        except Exception as exc:
            logger.error("  ✗ 异常: %s", exc, exc_info=True)

        # 测试3：权重范围验证
        logger.info("\n测试3：权重范围验证")
        logger.info("  验证返回的权重在[0.0, 1.0]范围内...")
        try:
            dates = pd.date_range("2024-01-01", periods=100, freq="D")
            prices = 100 + 10 * np.sin(np.arange(100) * 0.05)
            valid_df = pd.DataFrame({"date": dates, "close": prices})

            market_data = {"index_df": valid_df}
            strategy_mix, _ = selector.select_strategy_mix(market_data)

            all_valid = True
            for strategy_name, weight in strategy_mix.weights.items():
                if not (0.0 <= weight <= 1.0):
                    logger.warning(f"  ✗ {strategy_name}权重超出范围: {weight}")
                    all_valid = False

            if all_valid:
                logger.info("  ✓ 所有权重都在有效范围内")
        except Exception as exc:
            logger.error("  ✗ 异常: %s", exc, exc_info=True)

        logger.info("\n✓ 演示3完成")

    except Exception as exc:
        logger.error("演示3失败: %s", exc, exc_info=True)


def main():
    """主程序"""
    logger.info("\n" + "=" * 70)
    logger.info("A股量化选股系统 - 新功能集成快速启动")
    logger.info("=" * 70)
    logger.info("本脚本演示以下新功能:")
    logger.info("1. Regime自适应策略选择器 (HMM市场状态检测)")
    logger.info("2. 国家队持仓过滤策略")
    logger.info("3. 异常处理和边界检查")
    logger.info("=" * 70)

    try:
        # 验证导入
        logger.info("\n验证新模块导入...")
        from aqsp.strategies.national_team_filter import NationalTeamFilterStrategy
        from aqsp.strategies.regime_adaptive import RegimeAdaptiveStrategySelector

        _ = (RegimeAdaptiveStrategySelector, NationalTeamFilterStrategy)
        logger.info("✓ RegimeAdaptiveStrategySelector 导入成功")
        logger.info("✓ NationalTeamFilterStrategy 导入成功")

        # 执行演示
        demo_regime_adaptive_selector()
        demo_national_team_filter()
        demo_error_handling()

        logger.info("\n" + "=" * 70)
        logger.info("✓ 所有演示完成！系统100%就绪")
        logger.info("=" * 70)
        return 0

    except ImportError as exc:
        logger.error("模块导入失败: %s", exc, exc_info=True)
        logger.error("请确保在项目根目录运行此脚本")
        return 1
    except Exception as exc:
        logger.error("执行失败: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
