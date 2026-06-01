from __future__ import annotations

from datetime import date
from typing import List, Tuple
import json
from pathlib import Path

from aqsp.core.time import today_shanghai
from aqsp.data.index_constituents import load_optional_index_constituents

DEFAULT_POOLS = {
    "sh300": ("沪深300", ["000300"]),
    "zz500": ("中证500", ["000905"]),
    "zz1000": ("中证1000", ["000852"]),
    "cyb": ("创业板指", ["399006"]),
    "zxb": ("中小板指", ["399005"]),
}


class UniversePool:
    def __init__(self, name: str, description: str, index_codes: List[str]):
        self.name = name
        self.description = description
        self.index_codes = index_codes

    @classmethod
    def from_default(cls, pool_name: str) -> "UniversePool":
        if pool_name not in DEFAULT_POOLS:
            raise ValueError(
                f"Unknown pool: {pool_name}. Available: {list(DEFAULT_POOLS.keys())}"
            )
        description, index_codes = DEFAULT_POOLS[pool_name]
        return cls(pool_name, description, index_codes)

    @classmethod
    def from_file(cls, filepath: str) -> "UniversePool":
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Pool file not found: {filepath}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls(
            name=data.get("name", "custom"),
            description=data.get("description", ""),
            index_codes=data.get("index_codes", []),
        )

    @staticmethod
    def list_default_pools() -> List[Tuple[str, str]]:
        return [(name, desc) for name, (desc, _) in DEFAULT_POOLS.items()]

    def get_symbols(self, as_of: date | None = None) -> List[str]:
        target_day = as_of or today_shanghai()
        symbols: list[str] = []
        for index_code in self.index_codes:
            ts_index_code = _to_tushare_index_code(index_code)
            symbols.extend(load_optional_index_constituents(ts_index_code, target_day))
        deduped = list(dict.fromkeys(symbols))
        if deduped:
            return deduped
        raise ValueError(
            f"Pool {self.name} requires TUSHARE_TOKEN or explicit --symbols for point-in-time constituents"
        )


class StockUniverse:
    def __init__(self, symbols: List[str], names: List[str] = None):
        self.symbols = symbols
        self.names = names or ["" for _ in symbols]
        self._symbol_to_name = dict(zip(symbols, self.names))

    def __len__(self):
        return len(self.symbols)

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.symbols

    def get_name(self, symbol: str) -> str:
        return self._symbol_to_name.get(symbol, "")

    def filter(self, symbols: List[str]) -> "StockUniverse":
        filtered = [
            (s, self._symbol_to_name.get(s, ""))
            for s in symbols
            if s in self._symbol_to_name
        ]
        return StockUniverse([s for s, _ in filtered], [n for _, n in filtered])

    def union(self, other: "StockUniverse") -> "StockUniverse":
        combined = {}
        for s, n in zip(self.symbols, self.names):
            combined[s] = n
        for s, n in zip(other.symbols, other.names):
            if s not in combined:
                combined[s] = n
        return StockUniverse(list(combined.keys()), list(combined.values()))

    def intersection(self, other: "StockUniverse") -> "StockUniverse":
        common = {}
        for s, n in zip(self.symbols, self.names):
            if s in other:
                common[s] = n
        return StockUniverse(list(common.keys()), list(common.values()))


def _to_tushare_index_code(index_code: str) -> str:
    if "." in index_code:
        return index_code
    if index_code.startswith("399"):
        return f"{index_code}.SZ"
    return f"{index_code}.SH"
