from __future__ import annotations

import json
from typing import Any

import pandas as pd
import requests


class SinaFundamentalSource:
    name: str = "sina_fundamental"

    def fetch_realtime_fundamentals(
        self, symbols: list[str]
    ) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        all_data = self._fetch_all_stocks()
        for symbol in symbols:
            row = all_data.get(symbol)
            if row:
                out[symbol] = {
                    "pe": row.get("per", 0.0),
                    "pb": row.get("pb", 0.0),
                    "market_cap": row.get("mktcap", 0.0),
                    "circulating_market_cap": row.get("nmc", 0.0),
                    "turnover_ratio": row.get("turnoverratio", 0.0),
                }
        return out

    def _fetch_all_stocks(self) -> dict[str, dict[str, Any]]:
        url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        all_data: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            params = {
                "page": page,
                "num": 80,
                "sort": "symbol",
                "asc": 1,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "init",
            }
            try:
                r = requests.get(url, params=params, timeout=30)
                data = json.loads(r.text)
                if not data:
                    break
                for row in data:
                    code = row.get("code", "")
                    if code:
                        all_data[code] = row
                if len(data) < 80:
                    break
                page += 1
            except Exception:
                break
        return all_data

    def merge_fundamentals_into_ohlcv(
        self,
        ohlcv_data: dict[str, pd.DataFrame],
        fundamentals: dict[str, dict[str, float]],
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for symbol, df in ohlcv_data.items():
            df = df.copy()
            fund = fundamentals.get(symbol, {})
            df["pe"] = fund.get("pe", 0.0)
            df["pb"] = fund.get("pb", 0.0)
            df["market_cap"] = fund.get("market_cap", 0.0)
            result[symbol] = df
        return result
