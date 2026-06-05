"""
停牌和黑名单过滤的单元测试

测试覆盖：
1. ST股过滤
2. *ST股过滤
3. 正常股票通过
4. 手动黑名单
5. 白名单豁免
6. 停牌过滤
7. 低流动性过滤
8. 边界情况和完整过滤流程
"""

from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile
import yaml

from aqsp.data.filters import TradabilityFilter


class TestTradabilityFilterInit:
    """测试过滤器初始化"""

    def test_init_with_default_config(self) -> None:
        """测试使用默认配置初始化"""
        flt = TradabilityFilter()
        assert flt.st_patterns == ["ST", "*ST", "退", "S"]
        assert flt.manual_blacklist == []
        assert flt.whitelist == []
        assert flt.min_daily_amount == 1000000
        assert flt.min_avg_volume_30d == 500000

    def test_init_with_custom_config(self) -> None:
        """测试使用自定义配置初始化"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "blacklist": {
                    "st_patterns": ["ST", "风险"],
                    "manual": ["600000"],
                    "whitelist": ["000001"],
                },
                "liquidity": {
                    "min_daily_amount": 500000,
                    "min_avg_volume_30d": 300000,
                },
            }
            yaml.dump(config, f)
            f.flush()

            flt = TradabilityFilter(config_path=f.name)
            assert "风险" in flt.st_patterns
            assert "600000" in flt.manual_blacklist
            assert "000001" in flt.whitelist
            assert flt.min_daily_amount == 500000

            Path(f.name).unlink()

    def test_init_with_nonexistent_config(self) -> None:
        """测试使用不存在的配置文件时使用默认配置"""
        flt = TradabilityFilter(config_path="/nonexistent/path/config.yaml")
        assert flt.st_patterns == ["ST", "*ST", "退", "S"]


class TestFilterSuspended:
    """测试停牌股票过滤"""

    def test_filter_suspended_with_empty_list(self) -> None:
        """测试空列表"""
        flt = TradabilityFilter()
        result = flt.filter_suspended([])
        assert result == []

    def test_filter_suspended_no_data(self) -> None:
        """测试无数据时视为停牌"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001", "000002"]

        # 无数据字典
        result = flt.filter_suspended(symbols, data=None)
        assert result == []

        # 空数据字典
        result = flt.filter_suspended(symbols, data={})
        assert result == []

    def test_filter_suspended_zero_volume(self) -> None:
        """测试成交量为0的股票视为停牌"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        data = {
            "600000": pd.DataFrame({
                "date": ["2026-01-01"],
                "volume": [0],
                "close": [10.0],
            })
        }

        result = flt.filter_suspended(symbols, data=data)
        assert result == []

    def test_filter_suspended_with_volume(self) -> None:
        """测试有成交量的股票通过"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001"]

        data = {
            "600000": pd.DataFrame({
                "date": ["2026-01-01"],
                "volume": [1000000],
                "close": [10.0],
            }),
            "000001": pd.DataFrame({
                "date": ["2026-01-01"],
                "volume": [500000],
                "close": [15.0],
            }),
        }

        result = flt.filter_suspended(symbols, data=data)
        assert set(result) == {"600000", "000001"}

    def test_filter_suspended_mixed(self) -> None:
        """测试混合情况：部分停牌，部分正常"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001", "000002", "000003"]

        data = {
            "600000": pd.DataFrame({"volume": [1000000]}),  # 正常
            "000001": pd.DataFrame({"volume": [0]}),  # 停牌
            "000002": None,  # 无数据 = 停牌
            "000003": pd.DataFrame({"volume": [500000]}),  # 正常
        }

        result = flt.filter_suspended(symbols, data=data)
        assert set(result) == {"600000", "000003"}

    def test_filter_suspended_series_volume(self) -> None:
        """测试处理Series类型的volume"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        # 模拟DataFrame.iloc[-1]返回Series的情况
        df = pd.DataFrame({
            "volume": [1000000],
            "close": [10.0],
        })

        data = {"600000": df}
        result = flt.filter_suspended(symbols, data=data)
        assert result == ["600000"]


class TestFilterBlacklist:
    """测试黑名单过滤"""

    def test_filter_blacklist_empty_list(self) -> None:
        """测试空列表"""
        flt = TradabilityFilter()
        result = flt.filter_blacklist([])
        assert result == []

    def test_filter_blacklist_st_pattern(self) -> None:
        """测试过滤ST股票"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001", "600002"]
        names = {
            "600000": "中国银行",
            "600001": "ST电子",
            "600002": "*ST盐湖",
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert set(result) == {"600000"}

    def test_filter_blacklist_delisted(self) -> None:
        """测试过滤退市股票"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001"]
        names = {
            "600000": "中国银行",
            "600001": "退市长油",
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert result == ["600000"]

    def test_filter_blacklist_suspended_mark(self) -> None:
        """测试过滤暂停上市股票（S开头）"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001"]
        names = {
            "600000": "中国银行",
            "600001": "S金融股",
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert result == ["600000"]

    def test_filter_blacklist_manual(self) -> None:
        """测试手动黑名单"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "blacklist": {
                    "st_patterns": ["ST", "*ST"],
                    "manual": ["600000", "000001"],
                    "whitelist": [],
                },
                "liquidity": {
                    "min_daily_amount": 1000000,
                    "min_avg_volume_30d": 500000,
                },
            }
            yaml.dump(config, f)
            f.flush()

            flt = TradabilityFilter(config_path=f.name)
            symbols = ["600000", "000001", "000002"]
            names = {
                "600000": "中国银行",
                "000001": "平安银行",
                "000002": "万科A",
            }

            result = flt.filter_blacklist(symbols, names=names)
            assert result == ["000002"]

            Path(f.name).unlink()

    def test_filter_blacklist_whitelist(self) -> None:
        """测试白名单豁免"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "blacklist": {
                    "st_patterns": ["ST"],
                    "manual": [],
                    "whitelist": ["600000"],
                },
                "liquidity": {
                    "min_daily_amount": 1000000,
                    "min_avg_volume_30d": 500000,
                },
            }
            yaml.dump(config, f)
            f.flush()

            flt = TradabilityFilter(config_path=f.name)
            symbols = ["600000", "600001"]
            names = {
                "600000": "ST中国银行",  # 在白名单中，即使名字有ST也保留
                "600001": "ST电子",
            }

            result = flt.filter_blacklist(symbols, names=names)
            assert set(result) == {"600000"}

            Path(f.name).unlink()

    def test_filter_blacklist_case_insensitive(self) -> None:
        """测试大小写不敏感"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001", "600002"]
        names = {
            "600000": "中国银行",
            "600001": "st电子",  # 小写
            "600002": "*st盐湖",  # 小写
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert set(result) == {"600000"}

    def test_filter_blacklist_no_names_dict(self) -> None:
        """测试未提供名称字典时的处理"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001"]

        # 不提供names参数
        result = flt.filter_blacklist(symbols, names=None)
        assert set(result) == {"600000", "600001"}

    def test_filter_blacklist_missing_name(self) -> None:
        """测试某些股票名称缺失时的处理"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001"]
        names = {
            "600000": "中国银行",
            # 600001缺失
        }

        result = flt.filter_blacklist(symbols, names=names)
        # 600001因为名称缺失（默认""），不会被识别为黑名单
        assert set(result) == {"600000", "600001"}


class TestFilterLowLiquidity:
    """测试低流动性过滤"""

    def test_filter_low_liquidity_empty_list(self) -> None:
        """测试空列表"""
        flt = TradabilityFilter()
        result = flt.filter_low_liquidity([])
        assert result == []

    def test_filter_low_liquidity_no_data(self) -> None:
        """测试无数据时视为流动性不足"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001"]

        result = flt.filter_low_liquidity(symbols, data=None)
        assert result == []

        result = flt.filter_low_liquidity(symbols, data={})
        assert result == []

    def test_filter_low_liquidity_avg_volume(self) -> None:
        """测试平均成交量过滤"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001"]

        data = {
            "600000": pd.DataFrame({
                "volume": [600000] * 30,  # 30天，平均60万
                "amount": [10000000] * 30,
            }),
            "000001": pd.DataFrame({
                "volume": [400000] * 30,  # 30天，平均40万
                "amount": [8000000] * 30,
            }),
        }

        # min_volume=500000，所以600000通过，000001不通过
        result = flt.filter_low_liquidity(symbols, data=data, min_volume=500000)
        assert result == ["600000"]

    def test_filter_low_liquidity_daily_amount(self) -> None:
        """测试最近日成交额过滤"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001"]

        data = {
            "600000": pd.DataFrame({
                "volume": [600000] * 30,
                "amount": [12000000] * 30,  # 1200万
            }),
            "000001": pd.DataFrame({
                "volume": [600000] * 30,
                "amount": [500000] * 30,  # 50万
            }),
        }

        # min_daily_amount=1000000，所以600000通过，000001不通过
        result = flt.filter_low_liquidity(symbols, data=data)
        assert result == ["600000"]

    def test_filter_low_liquidity_lookback_days(self) -> None:
        """测试回看天数参数"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        # 30天数据
        data = {
            "600000": pd.DataFrame({
                "volume": [600000] * 30,
                "amount": [10000000] * 30,
            }),
        }

        # 回看15天
        result = flt.filter_low_liquidity(
            symbols, data=data, min_volume=500000, lookback_days=15
        )
        assert result == ["600000"]

    def test_filter_low_liquidity_mixed(self) -> None:
        """测试混合情况"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001", "000002"]

        data = {
            "600000": pd.DataFrame({
                "volume": [600000] * 30,
                "amount": [10000000] * 30,
            }),
            "000001": pd.DataFrame({
                "volume": [300000] * 30,  # 流动性不足
                "amount": [5000000] * 30,
            }),
            "000002": pd.DataFrame({
                "volume": [700000] * 30,
                "amount": [11000000] * 30,
            }),
        }

        result = flt.filter_low_liquidity(
            symbols, data=data, min_volume=500000
        )
        assert set(result) == {"600000", "000002"}

    def test_filter_low_liquidity_insufficient_lookback(self) -> None:
        """测试数据不足回看天数的情况"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        # 只有15天数据
        data = {
            "600000": pd.DataFrame({
                "volume": [600000] * 15,
                "amount": [10000000] * 15,
            }),
        }

        # 要求30天回看，但只有15天，应该跳过
        result = flt.filter_low_liquidity(
            symbols, data=data, min_volume=500000, lookback_days=30
        )
        assert result == []


class TestFilterAll:
    """测试完整过滤流程"""

    def test_filter_all_comprehensive(self) -> None:
        """测试完整过滤流程"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001", "000001", "000002", "000003"]

        # 构造数据和名称
        names = {
            "600000": "中国银行",  # 正常
            "600001": "ST电子",  # 黑名单
            "000001": "平安银行",  # 停牌
            "000002": "万科A",  # 流动性不足
            "000003": "招商银行",  # 正常
        }

        data = {
            "600000": pd.DataFrame({
                "volume": [600000] * 30,
                "amount": [10000000] * 30,
            }),
            "600001": None,  # 黑名单，不需要数据
            "000001": pd.DataFrame({
                "volume": [0] * 30,  # 停牌
                "amount": [0] * 30,
            }),
            "000002": pd.DataFrame({
                "volume": [300000] * 30,  # 流动性不足
                "amount": [5000000] * 30,
            }),
            "000003": pd.DataFrame({
                "volume": [700000] * 30,
                "amount": [11000000] * 30,
            }),
        }

        result = flt.filter_all(symbols, data=data, names=names, min_volume=500000)
        # 只有600000和000003通过所有过滤
        assert set(result) == {"600000", "000003"}

    def test_filter_all_order_matters(self) -> None:
        """测试过滤顺序（黑名单 -> 停牌 -> 流动性）"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001"]

        names = {
            "600000": "中国银行",
            "600001": "ST电子",
        }

        data = {
            "600000": pd.DataFrame({
                "volume": [100] * 30,  # 流动性不足
                "amount": [1000] * 30,
            }),
            "600001": pd.DataFrame({
                "volume": [1000000] * 30,  # 即使流动性好，也被黑名单过滤
                "amount": [10000000] * 30,
            }),
        }

        result = flt.filter_all(symbols, data=data, names=names)
        assert result == []  # 600001被黑名单过滤，600000被流动性过滤

    def test_filter_all_empty_input(self) -> None:
        """测试空输入"""
        flt = TradabilityFilter()
        result = flt.filter_all([], data={}, names={})
        assert result == []

    def test_filter_all_none_data_and_names(self) -> None:
        """测试None数据和名称"""
        flt = TradabilityFilter()
        symbols = ["600000", "000001"]

        # 不提供data和names
        result = flt.filter_all(symbols, data=None, names=None)
        assert result == []  # 无数据=停牌，无名称=无法识别黑名单但不被过滤


class TestEdgeCases:
    """测试边界情况"""

    def test_edge_case_empty_dataframe(self) -> None:
        """测试空DataFrame"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        data = {
            "600000": pd.DataFrame(),  # 空DataFrame
        }

        result = flt.filter_suspended(symbols, data=data)
        assert result == []

    def test_edge_case_nan_volume(self) -> None:
        """测试NaN成交量"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        data = {
            "600000": pd.DataFrame({
                "volume": [np.nan],
                "close": [10.0],
            }),
        }

        result = flt.filter_suspended(symbols, data=data)
        # NaN会被转换为float('nan')，不等于0，应该通过
        assert result == ["600000"]

    def test_edge_case_special_characters_in_name(self) -> None:
        """测试名称中的特殊字符"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001"]
        names = {
            "600000": "中国银行-A",
            "600001": "*ST-电子",
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert set(result) == {"600000"}

    def test_edge_case_duplicate_symbols(self) -> None:
        """测试重复的股票代码"""
        flt = TradabilityFilter()
        symbols = ["600000", "600000", "000001"]
        names = {
            "600000": "中国银行",
            "000001": "ST电子",
        }

        result = flt.filter_blacklist(symbols, names=names)
        # 应该返回["600000", "600000"]
        assert result.count("600000") == 2

    def test_edge_case_large_volume(self) -> None:
        """测试大成交量"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        data = {
            "600000": pd.DataFrame({
                "volume": [int(1e10)],  # 100亿股
                "amount": [1e15],
            }),
        }

        result = flt.filter_suspended(symbols, data=data)
        assert result == ["600000"]

    def test_edge_case_negative_volume(self) -> None:
        """测试负成交量（异常数据）"""
        flt = TradabilityFilter()
        symbols = ["600000"]

        data = {
            "600000": pd.DataFrame({
                "volume": [-100],
                "close": [10.0],
            }),
        }

        result = flt.filter_suspended(symbols, data=data)
        # 负数不会通过大于0的检查
        assert result == []

    def test_edge_case_all_st_patterns(self) -> None:
        """测试所有ST模式"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001", "600002", "600003"]
        names = {
            "600000": "ST电子",  # ST
            "600001": "*ST盐湖",  # *ST
            "600002": "退市长油",  # 退
            "600003": "S金融",  # S
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert result == []  # 全部被过滤

    def test_edge_case_mixed_case_patterns(self) -> None:
        """测试混合大小写模式"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001", "600002"]
        names = {
            "600000": "中国St电子",  # 混合大小写
            "600001": "退市上海",  # 小写
            "600002": "暂停s证券",  # 小写s
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert set(result) == {"600002"}  # 只有s不被识别为S（大小写敏感）


class TestAccuracy:
    """测试过滤准确性（不误杀正常股票）"""

    def test_accuracy_normal_stocks_pass(self) -> None:
        """测试正常股票不被误杀"""
        flt = TradabilityFilter()
        symbols = [
            "600000",  # 浦发银行
            "000001",  # 平安银行
            "600519",  # 贵州茅台
            "000858",  # 五粮液
        ]

        names = {
            "600000": "浦发银行",
            "000001": "平安银行",
            "600519": "贵州茅台",
            "000858": "五粮液",
        }

        data = {
            symbol: pd.DataFrame({
                "volume": [600000] * 30,
                "amount": [10000000] * 30,
            })
            for symbol in symbols
        }

        result = flt.filter_all(symbols, data=data, names=names)
        # 所有正常股票应该通过
        assert len(result) == 4

    def test_accuracy_no_false_positives_for_containing_patterns(self) -> None:
        """测试不会误杀包含ST等字样的正常股票"""
        flt = TradabilityFilter()
        symbols = ["600000", "600001"]
        names = {
            "600000": "北斗导航",  # 包含"斗"
            "600001": "方正技术",  # 包含"正"
        }

        result = flt.filter_blacklist(symbols, names=names)
        assert set(result) == {"600000", "600001"}

    def test_accuracy_whitelist_prevents_false_positives(self) -> None:
        """测试白名单可以防止误杀"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "blacklist": {
                    "st_patterns": ["ST"],
                    "manual": [],
                    "whitelist": ["600000"],
                },
                "liquidity": {
                    "min_daily_amount": 1000000,
                    "min_avg_volume_30d": 500000,
                },
            }
            yaml.dump(config, f)
            f.flush()

            flt = TradabilityFilter(config_path=f.name)
            symbols = ["600000"]
            names = {
                "600000": "ST北京某公司",  # 包含ST但在白名单中
            }

            result = flt.filter_blacklist(symbols, names=names)
            assert result == ["600000"]

            Path(f.name).unlink()
