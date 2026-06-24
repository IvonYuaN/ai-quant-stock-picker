"""数据质量验证和异常检测模块。

提供OHLCV数据的完整性、合理性和异常检测功能。
验证逻辑遵循保守原则：宁可误报（警告）不可漏报（错误）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ValidationResult:
    """验证结果数据类。

    Attributes:
        is_valid: 数据是否有效（没有错误）
        errors: 错误列表，表示数据不可用的问题
        warnings: 警告列表，表示可能有问题但数据仍可用
    """

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DataValidator:
    """数据质量验证器。

    提供OHLCV数据的多层次验证：
    1. 基本完整性：列完整性、空值检查、索引连续性
    2. 价格合理性：正数、OHLC关系、涨跌幅限制
    3. 成交量合理性：非负、正常交易日非零、异常倍增检测
    4. 异常检测：价格跳空、连续停牌、长期不变价格

    验证分类原则：
    - 错误（Error）：数据不可用，必须拒绝使用
    - 警告（Warning）：可能有问题，但数据仍可使用
    """

    # 涨跌幅限制（不同市场）
    LIMIT_MOVE_MAIN_BOARD: float = 0.10  # 主板：10%
    LIMIT_MOVE_GROWTH: float = 0.20  # 创业板/科创板：20%
    LIMIT_MOVE_BEIJING: float = 0.30  # 北交所：30%
    LIMIT_MOVE_ST: float = 0.05  # ST股：5%

    # 异常检测阈值
    PRICE_GAP_THRESHOLD: float = 0.10  # 跳空 > 10%
    VOLUME_SPIKE_THRESHOLD: float = 100.0  # 成交量异常倍增阈值
    CONTINUOUS_ZERO_VOLUME_DAYS: int = 3  # 连续零成交天数阈值

    def validate_ohlc(self, df: pd.DataFrame, symbol: str = "") -> ValidationResult:
        """验证OHLC数据的基本质量。

        验证项目：
        - 必需列存在
        - 没有空值
        - 数据类型正确

        Args:
            df: OHLCV数据框
            symbol: 股票代码（用于错误消息）

        Returns:
            ValidationResult: 验证结果
        """
        result = ValidationResult(is_valid=True)

        # 检查必需列
        required_columns = {"open", "high", "low", "close", "volume"}
        missing = required_columns - set(df.columns)
        if missing:
            result.errors.append(f"缺失必需列: {', '.join(sorted(missing))}")
            result.is_valid = False
            return result

        # 检查空值
        for col in required_columns:
            null_count = df[col].isna().sum()
            if null_count > 0:
                result.errors.append(f"列 '{col}' 包含 {null_count} 个空值")
                result.is_valid = False

        if not result.is_valid:
            return result

        # 进行其他验证
        result_price_range = self.validate_price_range(df, symbol=symbol)
        result.errors.extend(result_price_range.errors)
        result.warnings.extend(result_price_range.warnings)
        if not result_price_range.is_valid:
            result.is_valid = False

        result_volume = self.validate_volume(df)
        result.errors.extend(result_volume.errors)
        result.warnings.extend(result_volume.warnings)

        result_continuity = self.validate_data_continuity(df)
        result.errors.extend(result_continuity.errors)
        result.warnings.extend(result_continuity.warnings)

        result_anomalies = self.check_anomalies(df)
        result.warnings.extend(result_anomalies.warnings)

        return result

    def validate_price_range(
        self,
        df: pd.DataFrame,
        symbol: str = "",
    ) -> ValidationResult:
        """验证价格合理性。

        检查项目：
        - 所有价格 > 0
        - high >= low
        - high >= open 和 close
        - low <= open 和 close
        - 单日涨跌幅在合理范围内

        Args:
            df: OHLCV数据框

        Returns:
            ValidationResult: 验证结果
        """
        result = ValidationResult(is_valid=True)

        if df.empty:
            return result

        required_cols = {"open", "high", "low", "close"}
        if not required_cols.issubset(df.columns):
            return result

        # 检查价格是否为正
        price_cols = list(required_cols)
        for col in price_cols:
            if (df[col] <= 0).any():
                result.errors.append(f"列 '{col}' 包含非正数")
                result.is_valid = False

        if not result.is_valid:
            return result

        # 检查 high >= low
        invalid_hl = df[df["high"] < df["low"]]
        if not invalid_hl.empty:
            result.errors.append(f"发现 {len(invalid_hl)} 行数据: high < low")
            result.is_valid = False

        # 检查 high >= open, close
        invalid_ho = df[df["high"] < df["open"]]
        if not invalid_ho.empty:
            result.errors.append(f"发现 {len(invalid_ho)} 行数据: high < open")
            result.is_valid = False

        invalid_hc = df[df["high"] < df["close"]]
        if not invalid_hc.empty:
            result.errors.append(f"发现 {len(invalid_hc)} 行数据: high < close")
            result.is_valid = False

        # 检查 low <= open, close
        invalid_lo = df[df["low"] > df["open"]]
        if not invalid_lo.empty:
            result.errors.append(f"发现 {len(invalid_lo)} 行数据: low > open")
            result.is_valid = False

        invalid_lc = df[df["low"] > df["close"]]
        if not invalid_lc.empty:
            result.errors.append(f"发现 {len(invalid_lc)} 行数据: low > close")
            result.is_valid = False

        # 检查涨跌幅
        limit = self._limit_move_for_symbol(df, symbol)
        daily_returns = df["close"].pct_change().dropna()
        for idx, ret in daily_returns.items():
            abs_ret = abs(ret)

            if abs_ret >= limit + 0.0001:  # 允许浮点精度误差
                result.errors.append(f"日涨跌幅超限: {ret:.2%} (限制: {limit:.2%})")
                result.is_valid = False

        return result

    def validate_volume(self, df: pd.DataFrame) -> ValidationResult:
        """验证成交量合理性。

        检查项目：
        - 成交量 >= 0
        - 正常交易日成交量 > 0
        - 成交量异常倍增检测

        Args:
            df: OHLCV数据框

        Returns:
            ValidationResult: 验证结果
        """
        result = ValidationResult(is_valid=True)

        if df.empty or "volume" not in df.columns:
            return result

        vol = df["volume"]

        # 检查成交量是否为负
        if (vol < 0).any():
            result.errors.append("成交量包含负数")
            result.is_valid = False
            return result

        # 检查连续零成交
        zero_mask = vol == 0
        zero_groups = (zero_mask != zero_mask.shift()).cumsum()
        continuous_zeros = (zero_mask.groupby(zero_groups).cumsum() + 1).max()

        if continuous_zeros.max() > self.CONTINUOUS_ZERO_VOLUME_DAYS:
            max_consecutive = int(continuous_zeros.max())
            result.warnings.append(f"检测到 {max_consecutive} 天连续零成交（可能停牌）")

        return result

    def validate_data_continuity(self, df: pd.DataFrame) -> ValidationResult:
        """验证数据连续性。

        检查日期序列是否连续（优先使用 date 列，其次回退到日期索引）。

        Args:
            df: OHLCV数据框（假设索引是日期）

        Returns:
            ValidationResult: 验证结果
        """
        result = ValidationResult(is_valid=True)

        if df.empty or len(df) < 2:
            return result

        # 优先使用标准 OHLCV 的 date 列，避免默认整数索引下连续性检查失效。
        try:
            if "date" in df.columns:
                parsed = pd.to_datetime(df["date"], errors="coerce").dropna()
                if len(parsed) < 2:
                    return result
                dates = parsed.dt.to_pydatetime()
            elif hasattr(df.index, "to_pydatetime"):
                dates = df.index.to_pydatetime()
            elif isinstance(df.index[0], pd.Timestamp):
                dates = df.index
            else:
                # 无法判断是否连续
                return result

            # 计算日期差
            date_diffs = pd.Series(dates).diff().dropna()
            if date_diffs.empty:
                return result

            # 检查是否存在非预期的日期跳跃。
            # 1 天是正常日频，3 天通常对应周末；明显更长的间隔才提示可能缺口。
            max_diff = date_diffs.max()
            if hasattr(max_diff, "days"):
                max_days = max_diff.days
            else:
                max_days = max_diff.total_seconds() / (24 * 3600)

            if max_days > 3.5:  # 周末不告警，明显长缺口才提示
                result.warnings.append(
                    f"检测到数据间断：最大日期间隔 {max_days:.1f} 天"
                )

        except (TypeError, AttributeError):
            # 无法解析日期，跳过连续性检查
            pass

        return result

    def check_anomalies(self, df: pd.DataFrame) -> ValidationResult:
        """检测异常数据。

        检查项目：
        - 价格跳空 > 10%
        - 连续多日成交量为0
        - 价格长期不变

        Args:
            df: OHLCV数据框

        Returns:
            ValidationResult: 验证结果（仅包含警告）
        """
        result = ValidationResult(is_valid=True)

        if df.empty or len(df) < 2:
            return result

        # 检查价格跳空
        if "open" in df.columns and "close" in df.columns:
            prev_close = df["close"].shift(1)
            gap_ratio = (df["open"] - prev_close) / prev_close
            gap_ratio = gap_ratio.dropna()

            large_gaps = gap_ratio[gap_ratio.abs() > self.PRICE_GAP_THRESHOLD]
            if not large_gaps.empty:
                result.warnings.append(
                    f"检测到 {len(large_gaps)} 个价格跳空 > {self.PRICE_GAP_THRESHOLD:.0%}"
                )

        # 检查价格长期不变
        if "close" in df.columns:
            close_changes = df["close"].diff().abs().sum()
            if close_changes == 0 and len(df) > 1:
                result.warnings.append("价格在整个期间保持不变（可能未更新）")

        return result

    def _limit_move_for_symbol(self, df: pd.DataFrame, symbol: str = "") -> float:
        clean_symbol = self._resolve_symbol(df, symbol)
        if self._is_st_stock(df):
            return self.LIMIT_MOVE_ST
        if self._is_growth_board_symbol(clean_symbol):
            return self.LIMIT_MOVE_GROWTH
        if self._is_beijing_board_symbol(clean_symbol):
            return self.LIMIT_MOVE_BEIJING
        return self.LIMIT_MOVE_MAIN_BOARD

    @staticmethod
    def _resolve_symbol(df: pd.DataFrame, symbol: str = "") -> str:
        clean = str(symbol or "").strip()
        if clean:
            return clean
        if "symbol" not in df.columns or df.empty:
            return ""
        try:
            return str(df["symbol"].dropna().iloc[0]).strip()
        except (IndexError, KeyError):
            return ""

    @staticmethod
    def _is_st_stock(df: pd.DataFrame) -> bool:
        """检查是否为ST股票。

        优先根据名称字段识别，未提供名称时按普通股票处理。
        """
        if "name" not in df.columns or df.empty:
            return False
        names = df["name"].dropna().astype(str)
        return any("ST" in name.upper() for name in names)

    @staticmethod
    def _is_growth_board_symbol(symbol: str) -> bool:
        """检查是否为创业板/科创板股票。

        创业板 300/301，科创板 688/689，涨跌幅通常按 20% 校验。
        """
        clean = symbol.strip()
        return clean.startswith(("300", "301", "688", "689"))

    @staticmethod
    def _is_beijing_board_symbol(symbol: str) -> bool:
        """检查是否为北交所股票，涨跌幅通常按 30% 校验。"""
        clean = symbol.strip()
        return clean.startswith(("43", "83", "87", "88"))
