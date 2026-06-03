from __future__ import annotations

from pathlib import Path

import pandas as pd

from aqsp.filters_lethal.base import FilterResult, LethalFilter


class LockupReleaseFilter(LethalFilter):
    name = "lockup_release"
    hypothesis = "限售股解禁后30天内，大股东减持压力导致股价承压"

    def __init__(
        self, data_path: str = "data/lockup_schedule.csv", lookback_days: int = 30
    ):
        self.data_path = data_path
        self.lookback_days = lookback_days

    def _load_lockup_data(self) -> pd.DataFrame | None:
        path = Path(self.data_path)
        if not path.exists():
            return None
        return pd.read_csv(path, dtype={"symbol": str})

    def check(self, symbol: str, df: pd.DataFrame, **kwargs: object) -> FilterResult:
        lockup_data = kwargs.get("lockup_data")
        if lockup_data is None:
            lockup_data = self._load_lockup_data()
        if lockup_data is None or lockup_data.empty:
            return FilterResult(
                symbol=symbol,
                passed=True,
                reason="无解禁数据，跳过",
                filter_name=self.name,
            )

        from aqsp.core.time import today_shanghai

        today = today_shanghai()
        symbol_rows = lockup_data[lockup_data["symbol"] == symbol]
        if symbol_rows.empty:
            return FilterResult(
                symbol=symbol,
                passed=True,
                reason="无该股解禁记录",
                filter_name=self.name,
            )

        for _, row in symbol_rows.iterrows():
            # 单行解禁日期脏数据（空/格式错/NaN）不应让整个排雷崩溃，
            # 跳过坏行继续检查其余记录（T2 原则：错杀<漏放，但崩溃会导致漏放）。
            try:
                release_date = pd.Timestamp(row["release_date"]).date()
            except (ValueError, TypeError, KeyError):
                continue
            days_until = (release_date - today).days
            if 0 <= days_until <= self.lookback_days:
                return FilterResult(
                    symbol=symbol,
                    passed=False,
                    reason=f"距解禁日{days_until}天（{release_date.isoformat()}），在{self.lookback_days}天窗口内",
                    filter_name=self.name,
                )

        return FilterResult(
            symbol=symbol,
            passed=True,
            reason="无近期解禁",
            filter_name=self.name,
        )
