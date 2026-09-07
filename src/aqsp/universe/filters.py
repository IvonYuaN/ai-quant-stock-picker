from __future__ import annotations

from datetime import date, datetime
from typing import List, Dict, Optional
import pandas as pd

from aqsp.core.time import today_shanghai


class STFilter:
    def __init__(self):
        # 名称关键字通道：精确包含匹配（ST/*ST 为风险警示，退为退市预警）。
        # 顺序无关：因 "*ST" 已含 "ST" 子串，二者命中其一即可。
        self.st_keywords = ["ST", "*ST", "退"]

    def is_st(
        self,
        symbol: str,
        name: str = "",
        st_symbols: Optional[set[str]] = None,
    ) -> bool:
        # 通道1：名称关键字（展示名缺失时此通道不可用）。
        # strip 后判空：None / "" / 全空白均视为缺失。
        clean_name = (name or "").strip()
        name_upper = clean_name.upper()
        for keyword in self.st_keywords:
            if keyword in name_upper:
                return True
        # 通道2：显式 ST 符号集（独立于名称，名称缺失时仍可判定）。
        # 健康报告 #R4：原仅依赖名称单通道，名称缺失即判定为非 ST，可能漏掉
        # 真实 ST 标的；增加符号集第二通道弥补。
        if st_symbols and symbol in st_symbols:
            return True
        # name 缺失保守处理：名称通道无法确认「非 ST」，且无第二通道佐证时，
        # 保守排除（选股宁可漏不可误纳入风险警示标的）。健康报告 #R4。
        if not clean_name:
            return True
        return False

    def filter(
        self,
        universe: List[str],
        names: Optional[Dict[str, str]] = None,
        st_symbols: Optional[set[str]] = None,
        **kwargs,
    ) -> List[str]:
        names = names or {}
        return [
            s
            for s in universe
            if not self.is_st(s, names.get(s, ""), st_symbols=st_symbols)
        ]


class SuspendedFilter:
    def __init__(self, threshold_volume: float = 100):
        self.threshold_volume = threshold_volume

    def is_suspended(self, symbol: str, data: pd.DataFrame) -> bool:
        if data is None or data.empty:
            return True

        latest = data.iloc[-1]
        volume = latest.get("volume", 0)
        if isinstance(volume, pd.Series):
            volume = volume.values[0] if len(volume) > 0 else 0

        return float(volume) < self.threshold_volume

    def filter(
        self, universe: List[str], data: Dict[str, pd.DataFrame] = None, **kwargs
    ) -> List[str]:
        result = []
        for symbol in universe:
            df = data.get(symbol) if data else None
            if not self.is_suspended(symbol, df):
                result.append(symbol)
        return result


class NewStockFilter:
    def __init__(self, min_days_listed: int = 90):
        self.min_days_listed = min_days_listed

    def is_new_stock(
        self, listing_date: Optional[str], as_of: Optional[date] = None
    ) -> bool:
        if listing_date is None:
            return True

        try:
            listed = datetime.strptime(listing_date, "%Y-%m-%d").date()
        except ValueError:
            return True

        # 上市天数必须相对评估基准日计算，沿用 pool.get_symbols/runtime 的 as_of
        # 约定；否则回测与复算会用当前挂钟时间判定历史新股，等于把"未来已知
        # 该股已上市满 N 天"的信息带进过去（PIT 泄漏），且结果随运行日漂移。
        reference_day = as_of or today_shanghai()
        return (reference_day - listed).days < self.min_days_listed

    def filter(
        self,
        universe: List[str],
        listing_dates: Dict[str, str] = None,
        as_of: Optional[date] = None,
        **kwargs,
    ) -> List[str]:
        result = []
        for symbol in universe:
            listing_date = listing_dates.get(symbol) if listing_dates else None
            if not self.is_new_stock(listing_date, as_of=as_of):
                result.append(symbol)
        return result


class DelistedFilter:
    def __init__(self):
        self.delisted_keywords = ["退市", "退A", "退B"]

    def is_delisted(self, name: str) -> bool:
        if not name:
            return False
        name_upper = name.upper()
        for keyword in self.delisted_keywords:
            if keyword in name_upper:
                return True
        return False

    def filter(
        self, universe: List[str], names: Optional[Dict[str, str]] = None, **kwargs
    ) -> List[str]:
        names = names or {}
        return [s for s in universe if not self.is_delisted(names.get(s, ""))]


class LiquidityFilter:
    def __init__(self, min_avg_volume: float = 1000000, lookback_days: int = 20):
        self.min_avg_volume = min_avg_volume
        self.lookback_days = lookback_days

    def has_enough_liquidity(self, data: pd.DataFrame) -> bool:
        if data is None or data.empty:
            return False

        recent = data.tail(self.lookback_days)
        if recent.empty:
            return False

        avg_volume = recent["volume"].mean()
        return float(avg_volume) >= self.min_avg_volume

    def filter(
        self, universe: List[str], data: Dict[str, pd.DataFrame] = None, **kwargs
    ) -> List[str]:
        result = []
        for symbol in universe:
            df = data.get(symbol) if data else None
            if self.has_enough_liquidity(df):
                result.append(symbol)
        return result


class PriceFilter:
    def __init__(self, min_price: float = 1.0, max_price: float = 1000.0):
        self.min_price = min_price
        self.max_price = max_price

    def is_price_valid(self, data: pd.DataFrame) -> bool:
        if data is None or data.empty:
            return False

        latest = data.iloc[-1]
        close = float(latest.get("close", 0))
        return self.min_price <= close <= self.max_price

    def filter(
        self, universe: List[str], data: Dict[str, pd.DataFrame] = None, **kwargs
    ) -> List[str]:
        result = []
        for symbol in universe:
            df = data.get(symbol) if data else None
            if self.is_price_valid(df):
                result.append(symbol)
        return result


class FilterPipeline:
    def __init__(self):
        self._filters = []

    def add_filter(self, filter_name: str, filter_instance):
        self._filters.append((filter_name, filter_instance))

    def apply(self, universe: List[str], **kwargs) -> List[str]:
        result = universe.copy()
        for filter_name, filter_instance in self._filters:
            method = getattr(filter_instance, "filter", None)
            if method:
                result = method(result, **kwargs)
        return result

    def apply_with_stats(self, universe: List[str], **kwargs) -> Dict:
        stats = {
            "initial_count": len(universe),
            "filter_stats": {},
            "final_count": 0,
            "remaining": [],
        }

        result = universe.copy()
        for filter_name, filter_instance in self._filters:
            before = len(result)
            method = getattr(filter_instance, "filter", None)
            if method:
                result = method(result, **kwargs)
            after = len(result)
            stats["filter_stats"][filter_name] = {
                "before": before,
                "after": after,
                "removed": before - after,
            }

        stats["final_count"] = len(result)
        stats["remaining"] = result
        return stats
