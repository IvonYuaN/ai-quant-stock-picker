from __future__ import annotations

import os
from aqsp.core.runtime import runtime_data_root
from pathlib import Path

import pandas as pd

from aqsp.filters_lethal.base import FilterResult, LethalFilter


def _default_holder_cache_path() -> str:
    """与 aqsp.data.holder_num.HolderNumSource._default_cache_path 同一规则（写读同源）。

    生产者把股东户数写到 ``$AQSP_RUNTIME_DATA_ROOT/pit_cache/holder_count.csv``；
    未配置 runtime root 时回落系统临时目录（与生产者一致，避免污染源码树）。
    旧默认 data/holder_count.csv 全仓无产出方 ⇒ 排雷层一直在空转。
    """

    root = runtime_data_root()
    return os.path.join(root, "pit_cache", "holder_count.csv")


class HolderCountFilter(LethalFilter):
    name = "holder_count"
    hypothesis = "股东户数连续2季度减少超过15%，表明筹码集中但可能有出货风险"

    def __init__(
        self,
        data_path: str | None = None,
        decline_threshold: float = 0.15,
        min_quarters: int = 2,
    ):
        # 缺省读生产者（aqsp.data.holder_num）落盘的 pit_cache/holder_count.csv。
        self.data_path = data_path or _default_holder_cache_path()
        self.decline_threshold = decline_threshold
        self.min_quarters = min_quarters

    def _load_holder_data(self) -> pd.DataFrame | None:
        path = Path(self.data_path)
        if not path.exists():
            return None
        return pd.read_csv(path, dtype={"symbol": str})

    def check(self, symbol: str, df: pd.DataFrame, **kwargs: object) -> FilterResult:
        holder_data = kwargs.get("holder_data")
        if holder_data is None:
            holder_data = self._load_holder_data()
        if holder_data is None or holder_data.empty:
            return FilterResult(
                symbol=symbol,
                passed=True,
                reason="无股东户数数据，跳过",
                filter_name=self.name,
                data_missing=True,
            )

        symbol_rows = holder_data[holder_data["symbol"] == symbol].sort_values(
            "quarter"
        )
        if len(symbol_rows) < self.min_quarters:
            return FilterResult(
                symbol=symbol,
                passed=True,
                reason=f"股东户数不足{self.min_quarters}个季度数据",
                filter_name=self.name,
            )

        recent = symbol_rows.tail(self.min_quarters)
        counts = recent["holder_count"].tolist()
        consecutive_declines = 0
        for i in range(1, len(counts)):
            if counts[i - 1] > 0:
                change = (counts[i - 1] - counts[i]) / counts[i - 1]
                if change >= self.decline_threshold:
                    consecutive_declines += 1

        if consecutive_declines >= self.min_quarters - 1:
            pct = (counts[0] - counts[-1]) / counts[0] * 100 if counts[0] > 0 else 0
            return FilterResult(
                symbol=symbol,
                passed=False,
                reason=f"股东户数连续{consecutive_declines}季度减少超{self.decline_threshold:.0%}（累计{pct:.1f}%）",
                filter_name=self.name,
            )

        return FilterResult(
            symbol=symbol,
            passed=True,
            reason="股东户数趋势正常",
            filter_name=self.name,
        )
