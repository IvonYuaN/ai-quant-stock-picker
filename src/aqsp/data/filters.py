"""可交易性过滤。

过滤顺序保持简单明确：
1. 名称黑名单
2. 停牌/零成交
3. 最低流动性（平均成交量 + 平均成交额）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml


class TradabilityFilter:
    """可交易性过滤器。"""

    def __init__(self, config_path: str | None = None) -> None:
        self.config: dict[str, Any] = self._load_config(config_path)
        self.st_patterns: list[str] = self.config.get("blacklist", {}).get(
            "st_patterns", []
        )
        self.manual_blacklist: list[str] = self.config.get("blacklist", {}).get(
            "manual", []
        )
        self.whitelist: list[str] = self.config.get("blacklist", {}).get(
            "whitelist", []
        )
        self.min_daily_amount: float = self.config.get("liquidity", {}).get(
            "min_daily_amount", 1000000
        )
        self.min_avg_volume_30d: float = self.config.get("liquidity", {}).get(
            "min_avg_volume_30d", 500000
        )

    def _load_config(self, config_path: str | None) -> dict[str, Any]:
        if config_path is None:
            return {
                "blacklist": {
                    "st_patterns": ["ST", "*ST", "退", "S"],
                    "manual": [],
                    "whitelist": [],
                },
                "liquidity": {
                    "min_daily_amount": 1000000,
                    "min_avg_volume_30d": 500000,
                },
            }

        try:
            config_file = Path(config_path)
            if not config_file.exists():
                return self._load_config(None)
            return yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return self._load_config(None)

    @staticmethod
    def _to_float(value: object) -> float:
        if isinstance(value, pd.Series):
            value = value.values[0] if len(value) > 0 else 0.0
        return float(value)

    def filter_suspended(
        self,
        symbols: list[str],
        data: dict[str, pd.DataFrame] | None = None,
    ) -> list[str]:
        if not symbols:
            return []

        if data is None:
            data = {}

        result: list[str] = []
        for symbol in symbols:
            df = data.get(symbol)
            if df is None or df.empty:
                continue

            latest_row = df.iloc[-1]
            volume = self._to_float(latest_row.get("volume", 0))
            if pd.isna(volume) or volume > 0:
                result.append(symbol)

        return result

    def filter_blacklist(
        self,
        symbols: list[str],
        names: dict[str, str] | None = None,
    ) -> list[str]:
        if not symbols:
            return []

        names = names or {}
        result: list[str] = []

        for symbol in symbols:
            # 检查白名单（白名单中的股票即使有黑名单关键字也保留）
            if symbol in self.whitelist:
                result.append(symbol)
                continue

            # 检查手动黑名单
            if symbol in self.manual_blacklist:
                continue

            name = names.get(symbol, "")
            name_upper = name.upper()
            is_blacklisted = False

            for pattern in self.st_patterns:
                if pattern == "S":
                    if pattern in name:
                        is_blacklisted = True
                        break
                    continue
                if pattern.upper() in name_upper:
                    is_blacklisted = True
                    break

            if not is_blacklisted:
                result.append(symbol)

        return result

    def filter_low_liquidity(
        self,
        symbols: list[str],
        data: dict[str, pd.DataFrame] | None = None,
        min_avg_volume: float | None = None,
        min_avg_amount: float | None = None,
        min_volume: float | None = None,
        lookback_days: int = 30,
    ) -> list[str]:
        if not symbols:
            return []

        if data is None:
            data = {}

        volume_floor = (
            min_avg_volume
            if min_avg_volume is not None
            else (min_volume if min_volume is not None else self.min_avg_volume_30d)
        )
        amount_floor = (
            min_avg_amount if min_avg_amount is not None else self.min_daily_amount
        )
        result: list[str] = []

        for symbol in symbols:
            df = data.get(symbol)
            if df is None or df.empty:
                continue

            recent = df.tail(lookback_days)
            if recent.empty or len(recent) < lookback_days:
                continue

            if "volume" not in recent.columns:
                continue
            avg_volume = float(pd.to_numeric(recent["volume"], errors="coerce").mean())
            if pd.isna(avg_volume) or avg_volume < volume_floor:
                continue

            amount_series: pd.Series | None = None
            if "amount" in recent.columns:
                amount_series = pd.to_numeric(recent["amount"], errors="coerce")
            elif {"close", "volume"}.issubset(recent.columns):
                amount_series = pd.to_numeric(
                    recent["close"], errors="coerce"
                ) * pd.to_numeric(recent["volume"], errors="coerce")
            if amount_series is None:
                continue

            avg_amount = float(amount_series.mean())
            if pd.isna(avg_amount) or avg_amount < amount_floor:
                continue

            result.append(symbol)

        return result

    def filter_all(
        self,
        symbols: list[str],
        data: dict[str, pd.DataFrame] | None = None,
        names: dict[str, str] | None = None,
        min_avg_volume: float | None = None,
        min_avg_amount: float | None = None,
        min_volume: float | None = None,
    ) -> list[str]:
        result = self.filter_blacklist(symbols, names)
        result = self.filter_suspended(result, data)
        return self.filter_low_liquidity(
            result,
            data,
            min_avg_volume=min_avg_volume,
            min_avg_amount=min_avg_amount,
            min_volume=min_volume,
        )
