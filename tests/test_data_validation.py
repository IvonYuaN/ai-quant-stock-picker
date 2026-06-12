"""数据质量验证模块的单元测试。

测试覆盖：
- 基本完整性验证
- 价格合理性验证
- 成交量合理性验证
- 数据连续性验证
- 异常检测
- 边界情况处理
"""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.data.validation import DataValidator, ValidationResult


@pytest.fixture
def validator() -> DataValidator:
    """创建验证器实例"""
    return DataValidator()


@pytest.fixture
def normal_data() -> pd.DataFrame:
    """创建正常的OHLCV数据"""
    return pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0, 11.5, 12.0],
            "high": [10.8, 11.0, 11.5, 12.0, 12.5],
            "low": [9.8, 10.2, 10.8, 11.2, 11.8],
            "close": [10.5, 10.8, 11.2, 11.8, 12.2],
            "volume": [1000000, 1200000, 1100000, 1300000, 1250000],
        }
    )


class TestValidationResultDataclass:
    """测试ValidationResult数据类"""

    def test_validation_result_creation(self) -> None:
        """测试创建ValidationResult"""
        result = ValidationResult(is_valid=True)
        assert result.is_valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_validation_result_with_errors(self) -> None:
        """测试包含错误的ValidationResult"""
        result = ValidationResult(is_valid=False, errors=["Error 1", "Error 2"])
        assert result.is_valid is False
        assert len(result.errors) == 2


class TestNormalDataValidation:
    """测试正常数据通过验证"""

    def test_normal_data_passes_ohlc_validation(
        self, validator: DataValidator, normal_data: pd.DataFrame
    ) -> None:
        """测试正常数据通过OHLC验证"""
        result = validator.validate_ohlc(normal_data)
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_normal_data_passes_price_validation(
        self, validator: DataValidator, normal_data: pd.DataFrame
    ) -> None:
        """测试正常数据通过价格验证"""
        result = validator.validate_price_range(normal_data)
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_normal_data_passes_volume_validation(
        self, validator: DataValidator, normal_data: pd.DataFrame
    ) -> None:
        """测试正常数据通过成交量验证"""
        result = validator.validate_volume(normal_data)
        assert result.is_valid is True
        assert len(result.errors) == 0


class TestMissingColumnsDetection:
    """测试缺失列检测"""

    def test_missing_open_column(self, validator: DataValidator) -> None:
        """测试缺失open列"""
        df = pd.DataFrame(
            {
                "high": [10.0],
                "low": [9.0],
                "close": [9.5],
                "volume": [1000],
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is False
        assert any("open" in err for err in result.errors)

    def test_missing_volume_column(self, validator: DataValidator) -> None:
        """测试缺失volume列"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is False
        assert any("volume" in err for err in result.errors)

    def test_missing_multiple_columns(self, validator: DataValidator) -> None:
        """测试缺失多列"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "close": [10.0],
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is False
        assert len(result.errors) >= 1


class TestNaNValueDetection:
    """测试空值检测"""

    def test_nan_in_open(
        self, validator: DataValidator, normal_data: pd.DataFrame
    ) -> None:
        """测试open列中的NaN"""
        df = normal_data.copy()
        df.loc[2, "open"] = float("nan")
        result = validator.validate_ohlc(df)
        assert result.is_valid is False
        assert any(
            "open" in err and "NaN" in err or "空值" in err for err in result.errors
        )

    def test_nan_in_close(
        self, validator: DataValidator, normal_data: pd.DataFrame
    ) -> None:
        """测试close列中的NaN"""
        df = normal_data.copy()
        df.loc[1, "close"] = float("nan")
        result = validator.validate_ohlc(df)
        assert result.is_valid is False
        assert any("close" in err for err in result.errors)

    def test_multiple_nan_values(
        self, validator: DataValidator, normal_data: pd.DataFrame
    ) -> None:
        """测试多个NaN值"""
        df = normal_data.copy()
        df.loc[0, "volume"] = float("nan")
        df.loc[2, "volume"] = float("nan")
        result = validator.validate_ohlc(df)
        assert result.is_valid is False
        assert any("volume" in err for err in result.errors)


class TestNegativePriceDetection:
    """测试负价格检测"""

    def test_negative_open_price(self, validator: DataValidator) -> None:
        """测试负的open价格"""
        df = pd.DataFrame(
            {
                "open": [-10.0],
                "high": [10.0],
                "low": [9.0],
                "close": [10.0],
                "volume": [1000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert any("open" in err and "非正数" in err for err in result.errors)

    def test_zero_price(self, validator: DataValidator) -> None:
        """测试零价格"""
        df = pd.DataFrame(
            {
                "open": [0.0],
                "high": [10.0],
                "low": [9.0],
                "close": [10.0],
                "volume": [1000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert any("open" in err for err in result.errors)

    def test_all_prices_non_positive(self, validator: DataValidator) -> None:
        """测试所有价格都非正数"""
        df = pd.DataFrame(
            {
                "open": [-5.0],
                "high": [0.0],
                "low": [-1.0],
                "close": [0.0],
                "volume": [1000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert len(result.errors) >= 3


class TestHighLowRelationship:
    """测试high/low关系验证"""

    def test_high_less_than_low(self, validator: DataValidator) -> None:
        """测试high < low的错误数据"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [9.5],
                "low": [9.8],
                "close": [10.0],
                "volume": [1000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert any("high < low" in err for err in result.errors)

    def test_high_less_than_open(self, validator: DataValidator) -> None:
        """测试high < open的错误数据"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [9.5],
                "low": [9.0],
                "close": [10.0],
                "volume": [1000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert any("high < open" in err for err in result.errors)

    def test_high_less_than_close(self, validator: DataValidator) -> None:
        """测试high < close的错误数据"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.2],
                "low": [9.5],
                "close": [10.5],
                "volume": [1000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert any("high < close" in err for err in result.errors)


class TestZeroVolumeDetection:
    """测试零成交量检测"""

    def test_single_zero_volume(self, validator: DataValidator) -> None:
        """测试单个零成交量"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.5],
                "high": [10.5, 11.0],
                "low": [9.5, 10.0],
                "close": [10.0, 10.5],
                "volume": [0, 1000000],
            }
        )
        result = validator.validate_volume(df)
        # 单个零成交量不应触发警告（可能停牌一天）
        assert result.is_valid is True

    def test_continuous_zero_volume_threshold(self, validator: DataValidator) -> None:
        """测试连续零成交量超过阈值"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.0, 10.0, 10.0],
                "high": [10.5, 10.5, 10.5, 10.5],
                "low": [9.5, 9.5, 9.5, 9.5],
                "close": [10.0, 10.0, 10.0, 10.0],
                "volume": [0, 0, 0, 0],
            }
        )
        result = validator.validate_volume(df)
        assert result.is_valid is True
        assert any("连续零成交" in warn for warn in result.warnings)

    def test_negative_volume(self, validator: DataValidator) -> None:
        """测试负成交量"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "volume": [-1000],
            }
        )
        result = validator.validate_volume(df)
        assert result.is_valid is False
        assert any("负数" in err for err in result.errors)


class TestPriceJumpDetection:
    """测试价格跳空检测"""

    def test_large_gap_up(self, validator: DataValidator) -> None:
        """测试向上跳空"""
        df = pd.DataFrame(
            {
                "open": [11.5],  # 比前日close高15%
                "high": [12.0],
                "low": [11.0],
                "close": [11.5],
                "volume": [1000000],
            },
            index=pd.DatetimeIndex(["2026-06-05"]),
        )

        # 前置数据
        prev_df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "volume": [1000000],
            },
            index=pd.DatetimeIndex(["2026-06-04"]),
        )

        combined_df = pd.concat([prev_df, df])
        result = validator.check_anomalies(combined_df)
        assert any("跳空" in warn for warn in result.warnings)

    def test_small_gap_ignored(self, validator: DataValidator) -> None:
        """测试小跳空被忽略"""
        df = pd.DataFrame(
            {
                "open": [10.3],  # 比前日close高3%
                "high": [10.8],
                "low": [10.0],
                "close": [10.5],
                "volume": [1000000],
            },
            index=pd.DatetimeIndex(["2026-06-05"]),
        )

        prev_df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "volume": [1000000],
            },
            index=pd.DatetimeIndex(["2026-06-04"]),
        )

        combined_df = pd.concat([prev_df, df])
        result = validator.check_anomalies(combined_df)
        assert not any("跳空" in warn for warn in result.warnings)


class TestPriceUnchangedDetection:
    """测试价格长期不变检测"""

    def test_price_unchanged(self, validator: DataValidator) -> None:
        """测试价格长期不变"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.0, 10.0],
                "high": [10.5, 10.5, 10.5],
                "low": [9.5, 9.5, 9.5],
                "close": [10.0, 10.0, 10.0],
                "volume": [1000000, 1000000, 1000000],
            }
        )
        result = validator.check_anomalies(df)
        assert any("保持不变" in warn for warn in result.warnings)

    def test_price_changed(self, validator: DataValidator) -> None:
        """测试价格有变化"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.5],
                "high": [10.5, 11.0],
                "low": [9.5, 10.0],
                "close": [10.0, 10.8],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.check_anomalies(df)
        assert not any("保持不变" in warn for warn in result.warnings)


class TestDailyLimitMove:
    """测试涨跌幅限制"""

    def test_limit_up_detected(self, validator: DataValidator) -> None:
        """测试涨停（超过限制）"""
        df = pd.DataFrame(
            {
                "open": [10.0, 11.1],  # 涨11%
                "high": [10.5, 11.5],
                "low": [9.5, 10.8],
                "close": [10.0, 11.1],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert any("涨跌幅超限" in err for err in result.errors)

    def test_limit_down_detected(self, validator: DataValidator) -> None:
        """测试跌停（超过限制）"""
        df = pd.DataFrame(
            {
                "open": [10.0, 8.8],  # 跌12%
                "high": [10.5, 9.0],
                "low": [9.5, 8.5],
                "close": [10.0, 8.8],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False
        assert any("涨跌幅超限" in err for err in result.errors)

    def test_within_limit_accepted(self, validator: DataValidator) -> None:
        """测试涨跌幅在限制内"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.8],  # 涨8%
                "high": [10.5, 11.0],
                "low": [9.5, 10.5],
                "close": [10.0, 10.8],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is True

    def test_chinext_symbol_uses_20pct_limit(
        self,
        validator: DataValidator,
    ) -> None:
        """创业板按20%校验，避免把10%+正常波动误判为脏数据。"""
        df = pd.DataFrame(
            {
                "symbol": ["300750", "300750"],
                "open": [10.0, 11.03],
                "high": [10.5, 11.2],
                "low": [9.5, 10.8],
                "close": [10.0, 11.037],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_ohlc(df, symbol="300750")
        assert result.is_valid is True
        assert not any("涨跌幅超限" in err for err in result.errors)

    def test_main_board_still_uses_10pct_limit(
        self,
        validator: DataValidator,
    ) -> None:
        """主板仍按10%校验，避免放松真实异常。"""
        df = pd.DataFrame(
            {
                "symbol": ["600000", "600000"],
                "open": [10.0, 11.03],
                "high": [10.5, 11.2],
                "low": [9.5, 10.8],
                "close": [10.0, 11.037],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_ohlc(df, symbol="600000")
        assert result.is_valid is False
        assert any("限制: 10.00%" in err for err in result.errors)


class TestEmptyDataHandling:
    """测试空数据处理"""

    def test_empty_dataframe(self, validator: DataValidator) -> None:
        """测试空DataFrame"""
        df = pd.DataFrame()
        result = validator.validate_ohlc(df)
        # 应该检测到缺失列
        assert result.is_valid is False

    def test_single_row_dataframe(self, validator: DataValidator) -> None:
        """测试单行DataFrame"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "volume": [1000000],
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is True

    def test_price_continuity_empty_df(self, validator: DataValidator) -> None:
        """测试空DataFrame的连续性检查"""
        df = pd.DataFrame()
        result = validator.validate_data_continuity(df)
        assert result.is_valid is True


class TestWarningVsErrorClassification:
    """测试警告vs错误分类"""

    def test_price_gap_is_warning_not_error(self, validator: DataValidator) -> None:
        """测试价格跳空是警告而非错误"""
        df = pd.DataFrame(
            {
                "open": [11.5],
                "high": [12.0],
                "low": [11.0],
                "close": [11.5],
                "volume": [1000000],
            },
            index=pd.DatetimeIndex(["2026-06-05"]),
        )

        prev_df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "volume": [1000000],
            },
            index=pd.DatetimeIndex(["2026-06-04"]),
        )

        combined_df = pd.concat([prev_df, df])
        result = validator.check_anomalies(combined_df)
        assert result.is_valid is True  # 不是错误
        assert len(result.warnings) > 0  # 但有警告

    def test_negative_price_is_error_not_warning(
        self, validator: DataValidator
    ) -> None:
        """测试负价格是错误而非警告"""
        df = pd.DataFrame(
            {
                "open": [-10.0],
                "high": [10.0],
                "low": [9.0],
                "close": [10.0],
                "volume": [1000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False  # 是错误
        assert len(result.errors) > 0
        assert len(result.warnings) == 0  # 不是警告


class TestMultipleIssuesAccumulation:
    """测试多个问题累积"""

    def test_multiple_errors_accumulated(self, validator: DataValidator) -> None:
        """测试多个错误累积"""
        df = pd.DataFrame(
            {
                "open": [-10.0, -9.5],
                "high": [9.5, 9.8],  # high < low
                "low": [9.8, 10.0],
                "close": [10.0, 10.5],
                "volume": [0, -100],
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is False
        assert len(result.errors) >= 2  # 负价格和负成交量

    def test_errors_and_warnings_combined(self, validator: DataValidator) -> None:
        """测试错误和警告混合"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.0, 10.0, 10.0],  # 价格不变
                "high": [10.5, 10.5, 10.5, 10.5],
                "low": [9.5, 9.5, 9.5, 9.5],
                "close": [10.0, 10.0, 10.0, 10.0],
                "volume": [1000000, 0, 0, 0],  # 3天连续零成交
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is True  # 没有错误
        assert len(result.warnings) >= 2  # 至少有2个警告（价格不变 + 连续零成交）


class TestBoundaryConditions:
    """测试边界情况"""

    def test_exactly_at_limit_threshold(self, validator: DataValidator) -> None:
        """测试恰好在限制边界"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.99],  # 涨9.9%（不到10%限制）
                "high": [10.5, 11.5],
                "low": [9.5, 10.5],
                "close": [10.0, 10.99],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is True  # 应该被接受（在限制以内）

    def test_just_above_limit_threshold(self, validator: DataValidator) -> None:
        """测试略高于限制边界"""
        df = pd.DataFrame(
            {
                "open": [10.0, 11.05],  # 从10.0到11.05 = 10.5%（超过10%限制）
                "high": [10.5, 11.5],
                "low": [9.5, 10.5],
                "close": [10.0, 11.05],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_price_range(df)
        assert result.is_valid is False

    def test_very_small_positive_volume(self, validator: DataValidator) -> None:
        """测试极小的正成交量"""
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "volume": [1],  # 最小的正成交量
            }
        )
        result = validator.validate_volume(df)
        assert result.is_valid is True
        assert len(result.errors) == 0


class TestLargeDatasets:
    """测试大数据集性能"""

    def test_large_normal_dataset(self, validator: DataValidator) -> None:
        """测试大型正常数据集"""
        n = 10000
        df = pd.DataFrame(
            {
                "open": [10.0 + i * 0.001 for i in range(n)],
                "high": [10.5 + i * 0.001 for i in range(n)],
                "low": [9.5 + i * 0.001 for i in range(n)],
                "close": [10.2 + i * 0.001 for i in range(n)],
                "volume": [1000000] * n,
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is True
        # 应该能在合理时间内完成

    def test_large_dataset_with_errors(self, validator: DataValidator) -> None:
        """测试大型数据集包含错误"""
        n = 1000
        df = pd.DataFrame(
            {
                "open": [10.0] * n,
                "high": [9.5] * n,  # 错误：high < open
                "low": [9.0] * n,
                "close": [10.0] * n,
                "volume": [1000000] * n,
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is False


class TestSpecialCases:
    """测试特殊情况"""

    def test_very_high_volume_spike(self, validator: DataValidator) -> None:
        """测试极端的成交量尖峰"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.5],
                "high": [10.5, 11.0],
                "low": [9.5, 10.0],
                "close": [10.0, 10.8],
                "volume": [1000000, 200000000],  # 200倍放大
            }
        )
        result = validator.validate_volume(df)
        assert result.is_valid is True
        # 成交量本身没有错误，但应该被check_anomalies检测

    def test_fractional_prices(self, validator: DataValidator) -> None:
        """测试分数价格"""
        df = pd.DataFrame(
            {
                "open": [10.123],
                "high": [10.456],
                "low": [9.789],
                "close": [10.234],
                "volume": [1000000],
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is True

    def test_all_prices_equal(self, validator: DataValidator) -> None:
        """测试所有价格相等"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.0],
                "high": [10.0, 10.0],
                "low": [10.0, 10.0],
                "close": [10.0, 10.0],
                "volume": [1000000, 1000000],
            }
        )
        result = validator.validate_ohlc(df)
        assert result.is_valid is True
        assert any("保持不变" in warn for warn in result.warnings)
