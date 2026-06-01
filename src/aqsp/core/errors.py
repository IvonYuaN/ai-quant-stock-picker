from __future__ import annotations


class DataError(Exception):
    pass


class FreshnessError(DataError):
    def __init__(self, symbol: str, days_lag: int, max_allowed: int):
        super().__init__(
            f"数据过期: {symbol} 最新数据滞后 {days_lag} 天,最大允许 {max_allowed} 天"
        )
        self.symbol = symbol
        self.days_lag = days_lag
        self.max_allowed = max_allowed


class NotExecutableError(Exception):
    def __init__(self, symbol: str, reason: str):
        super().__init__(f"信号不可成交: {symbol} - {reason}")
        self.symbol = symbol
        self.reason = reason


class DataInconsistencyError(DataError):
    def __init__(self, symbol: str, source1: str, source2: str, diff_pct: float):
        super().__init__(
            f"数据不一致: {symbol} 在 {source1} 和 {source2} 之间差异 {diff_pct:.2f}%"
        )
        self.symbol = symbol
        self.source1 = source1
        self.source2 = source2
        self.diff_pct = diff_pct


class MissingDataError(DataError):
    def __init__(self, symbol: str, reason: str = ""):
        msg = f"缺失数据: {symbol}"
        if reason:
            msg += f" - {reason}"
        super().__init__(msg)
        self.symbol = symbol
        self.reason = reason
