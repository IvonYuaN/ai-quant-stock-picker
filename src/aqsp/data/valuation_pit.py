"""估值 PIT 源：按 ``as_of`` 查询历史估值快照（PE/PB/PS/PCF/总市值）。

设计规则（沿用 :mod:`aqsp.features.pit_enrichment` 与
:mod:`aqsp.data.macro_pit` 的既有约定，architecture §5/§9 无前视红线）：

- **每个查询都是 ``as_of <= signal_date``** —— 绝不取未来期快照。
  本模块的数据是**定期快照**（每 14 天一期），因此 ``as_of`` 语义是
  "不晚于该日可见的最近一期快照"，与 ``industry_pit`` 的 reclassification
  时点语义一致。
- **全部 fail-soft**：缓存缺失/损坏 ⇒ 查询返回 ``None``，调用方回退原行为，
  管线在无数据时**行为不变**。
- **cache-only**：``load_cached()`` 只读本地缓存、**绝不联网**；缓存由
  ``scripts/data/fetch_pit_data.py`` 预载（同 ``macro_pit``/``industry_pit``）。

数据来源：``valuation_history.csv``（东财 push2 快照，字段 SECURITY_CODE /
TRADE_DATE / PE_TTM / PB_MRQ / PS_TTM / PCF_OCF_TTM / TOTAL_MARKET_CAP）。
"""

from __future__ import annotations

import logging
import os
from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from aqsp.core.runtime import runtime_data_root

_logger = logging.getLogger("aqsp.data.valuation_pit")

# 快照里没有的字段不进 dataclass（保持最小契约）
_VAL_COLUMNS = ("pe_ttm", "pb_mrq", "ps_ttm", "pcf_ocf_ttm", "total_market_cap")


@dataclass(frozen=True)
class ValuationPoint:
    """某一期的估值快照（一只票 × 一个快照日）。"""

    code: str
    trade_date: str
    pe_ttm: Optional[float] = None
    pb_mrq: Optional[float] = None
    ps_ttm: Optional[float] = None
    pcf_ocf_ttm: Optional[float] = None
    total_market_cap: Optional[float] = None


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return out


def _coerce_code(value: object) -> str:
    """SECURITY_CODE 统一成 6 位（东财导出可能带前导零丢失）。"""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


class ValuationPitSource:
    """估值 PIT 源：本地缓存加载 + 时点查询。"""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir
        self._points: list[ValuationPoint] = []
        # code -> 该 code 的快照日期（升序），配合 bisect 做 asof 查询
        self._index: dict[str, list[str]] = {}

    # ---------------------------------------------------------------- 缓存
    def _default_cache_path(self) -> str:
        if self._cache_dir:
            return os.path.join(self._cache_dir, "valuation_history.csv")
        # 遵循项目统一 runtime data root 约定（#335：release 目录不可变，
        # 缓存落 repo 内等于没有；与 macro_pit/industry_pit 同款）。
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "valuation_history.csv")

    def _read_cache_points(self) -> Optional[list[ValuationPoint]]:
        path = self._default_cache_path()
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_csv(path, dtype={"SECURITY_CODE": str})
        except Exception:
            return None  # 缓存损坏 ⇒ 按缺失处理
        if df.empty or "SECURITY_CODE" not in df.columns or "TRADE_DATE" not in df.columns:
            return None
        out: list[ValuationPoint] = []
        for row in df.to_dict("records"):
            code = _coerce_code(row.get("SECURITY_CODE", ""))
            date = str(row.get("TRADE_DATE", "")).strip()
            if not code or not date:
                continue
            out.append(
                ValuationPoint(
                    code=code,
                    trade_date=date,
                    pe_ttm=_to_float(row.get("PE_TTM")),
                    pb_mrq=_to_float(row.get("PB_MRQ")),
                    ps_ttm=_to_float(row.get("PS_TTM")),
                    pcf_ocf_ttm=_to_float(row.get("PCF_OCF_TTM")),
                    total_market_cap=_to_float(row.get("TOTAL_MARKET_CAP")),
                )
            )
        return out or None

    def load_cached(self) -> bool:
        """仅从本地缓存加载（**绝不联网**）；命中且非空返回 True。

        供消费者（:mod:`aqsp.features.pit_enrichment` 默认单例）做缓存预热；
        缓存缺失/损坏时静默保持空，由调用方走优雅回退。
        """
        points = self._read_cache_points()
        if not points:
            return False
        # 必须按 (code, trade_date) 排序 ⇒ 索引内每个 code 的日期升序（bisect 前提）
        self._points = sorted(points, key=lambda p: (p.code, p.trade_date))
        self._build_index()
        return True

    def from_points(self, points: list[ValuationPoint]) -> "ValuationPitSource":
        """注入快照序列（测试 / 预加载用）。"""
        self._points = sorted(points, key=lambda p: (p.code, p.trade_date))
        self._build_index()
        return self

    def _build_index(self) -> None:
        """按 code 建升序索引（asof 查询用 bisect，O(log n)）。

        索引同时保存**该 code 自己的 points 列表** —— 位置必须与日期一一对应，
        否则会串票（全局 points 下标 ≠ 该 code 内下标）。
        """
        buckets: dict[str, list[ValuationPoint]] = {}
        for point in self._points:
            buckets.setdefault(point.code, []).append(point)
        self._index = {
            code: ([p.trade_date for p in pts], pts) for code, pts in buckets.items()
        }

    # ---------------------------------------------------------------- 查询
    def _as_of_point(self, code: str, as_of: str) -> Optional[ValuationPoint]:
        """取 ``trade_date <= as_of`` 的最近一期（``as_of`` 支持 YYYYMMDD 或 YYYY-MM-DD）。

        按 code 建索引 + bisect ⇒ O(log n)，避免在全量快照上线性扫描
        （生产快照 40 万行、逐票逐日查询必须走索引）。
        """
        if not self._points:
            return None
        norm = str(as_of).replace("-", "")
        # 严格 8 位数字：非法输入（如 "not-a-date" 去掉横线后正好 8 字符）
        # 必须拒掉，否则字符串比较会把未来期排到前面
        if len(norm) != 8 or not norm.isdigit():
            return None
        entry = self._index.get(code)
        if not entry:
            return None
        dates, pts = entry
        pos = bisect_right(dates, norm) - 1
        if pos < 0:
            return None
        return pts[pos]

    def lookup(
        self, code: str, as_of: str, autoload: bool = True
    ) -> Optional[ValuationPoint]:
        """按 ``as_of`` 取该票的估值快照（无则 None）。"""
        if not self._points and autoload:
            self.load_cached()
        return self._as_of_point(_coerce_code(code), as_of)

    def pe(self, code: str, as_of: str, autoload: bool = True) -> Optional[float]:
        point = self.lookup(code, as_of, autoload=autoload)
        return point.pe_ttm if point else None

    def pb(self, code: str, as_of: str, autoload: bool = True) -> Optional[float]:
        point = self.lookup(code, as_of, autoload=autoload)
        return point.pb_mrq if point else None

    def total_market_cap(self, code: str, as_of: str, autoload: bool = True) -> Optional[float]:
        point = self.lookup(code, as_of, autoload=autoload)
        return point.total_market_cap if point else None
