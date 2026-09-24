from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

from aqsp.filters_lethal.base import FilterResult, LethalFilter


def _default_lockup_cache_path() -> str:
    """与 aqsp.data.lockup.LockupSource._default_cache_path 同一规则（写读同源）。

    生产者把解禁计划写到 ``$AQSP_RUNTIME_DATA_ROOT/pit_cache/lockup.csv``；
    未配置 runtime root 时回落系统临时目录（与生产者一致，避免污染源码树）。
    """

    root = os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()
    return os.path.join(root, "pit_cache", "lockup.csv")


class LockupReleaseFilter(LethalFilter):
    name = "lockup_release"
    hypothesis = "限售股解禁后30天内，大股东减持压力导致股价承压"

    def __init__(self, data_path: str | None = None, lookback_days: int = 30):
        # 缺省读生产者（aqsp.data.lockup）落盘的 pit_cache/lockup.csv。
        # 旧默认 data/lockup_schedule.csv 全仓不存在 ⇒ 解禁排雷在生产一直空转。
        self.data_path = data_path or _default_lockup_cache_path()
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

        # 生产者（aqsp.data.lockup）写的是 plan_date；保留 release_date 兼容旧格式。
        date_col = next(
            (c for c in ("plan_date", "release_date") if c in lockup_data.columns),
            None,
        )
        if date_col is None:
            return FilterResult(
                symbol=symbol,
                passed=True,
                reason="解禁数据缺日期列（plan_date/release_date），跳过",
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

        for release_raw in symbol_rows[date_col].tolist():
            # 单行解禁日期脏数据（空/格式错/NaN）不应让整个排雷崩溃，
            # 跳过坏行继续检查其余记录（T2 原则：错杀<漏放，但崩溃会导致漏放）。
            try:
                release_date = pd.Timestamp(release_raw).date()
            except (ValueError, TypeError):
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
