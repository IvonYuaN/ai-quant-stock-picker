"""股东户数（东财）—— 排雷层「股东户数」数据源（生产 pit_cache/holder_count.csv）。

移植自 backend/astock.py `holder_num_change`（东财数据中心报表）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错 + **分页取全**。

报表名（2026-09-27 生产机实测复核）：
- `RPT_HOLDERNUM_DET` **可用且含完整历史**（每票×每期一行，~44 万行；
  `filter=(END_DATE='<季度末>')` 返回该季全量：2026-06-30 → 5408 条、
  2026-03-31 → 5343 条，远超 LATEST；字段含 SECURITY_CODE / SECURITY_NAME_ABBR /
  HOLDER_NUM / HOLD_NOTICE_DATE / END_DATE，与 LATEST 解析兼容）；
- `RPT_HOLDERNUMLATEST` 也可用但**结构性只含每票最新一期**（旧季度仅剩停更票 ~19 行）
  ⇒ 曾致 holder_count.csv 只落 1 个季度、筹码集中度 QoQ 信号无法计算（09-27 根治）；
- `RPT_HOLDERNUMCHANGE` / `RPT_HOLDERNUM_HIST` 不存在（code 9501），勿用。

落盘列：symbol, name, quarter, holder_count, notice_date
（HolderCountFilter 读 symbol/quarter/holder_count，quarter=END_DATE 截 10 位）。

默认取「最近 4 个已完成披露窗口的季度」：季度数据在季末后 ~30–62 天内才基本披露完，
45 天窗判定季度「已完成」（见 _recent_quarter_ends）。切到 DET 后每季均为全量，
4 个季度历史每次跑批都完整落盘（QoQ 需 ≥2 连续季度）。
"""

from __future__ import annotations

import logging
import os
import time
from aqsp.core.runtime import runtime_data_root
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai

# 东财数据中心通用接口（与 lockup/dividend 同源 host）
EM_HOLDER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# DET = 每票×每期完整历史表（按 END_DATE 过滤即得该季全量）；LATEST 只含每票最新一期。
# 2026-09-27 生产机实测切换，详见模块 docstring。
EM_HOLDER_REPORT = "RPT_HOLDERNUM_DET"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
_PAGE_SIZE = 500
_MAX_PAGES_PER_QUARTER = 40  # 40×500=20000 行/季守卫：A 股全市场远小于此
_DEFAULT_QUARTERS = 4
_DISCLOSURE_WINDOW_DAYS = 45  # 季末后该天数视为「该季数据基本披露完」
# 单页瞬时故障（东财 502 / Response ended prematurely / 超时）重试：
# 09-28 首自动跑批实证 2026-03-31 p3 断流 ⇒ 旧实现整季 raise、后续季度全不抓、
# load() 随后空结果 to_csv 把好数据截断。修复 = 页级重试 + 局部失败降级（#259）。
_PAGE_RETRIES = 3
_PAGE_BACKOFF_SECONDS = (2, 4, 8)
# 合并写封顶最近 8 季：4 季常规 + 4 季缓冲，防缓存无限膨胀（#259）。
_MAX_CACHED_QUARTERS = 8
_logger = logging.getLogger("aqsp.data.holder_num")


@dataclass(frozen=True)
class HolderNumItem:
    """单条股东户数（股票-季度粒度）。"""

    symbol: str
    name: str
    quarter: str  # 季度末 YYYY-MM-DD（END_DATE）
    holder_count: float  # 股东户数
    notice_date: str  # 披露日 YYYY-MM-DD（HOLD_NOTICE_DATE）


def _norm_date(v: object) -> str:
    """东财日期形如 "2026-06-30 00:00:00" → 截断为 YYYY-MM-DD。"""
    if v in (None, ""):
        return ""
    return str(v).strip()[:10]


def _recent_quarter_ends(today: date, count: int) -> list[str]:
    """最近 count 个「季末 < today - 45 天」的季度末（新→旧）。

    45 天 = 披露完成窗估计：一季报 4/30 前披露（30 天）、中报 8/31 前披露（62 天）、
    三季报 10/31、年报 4/30（跨 146 天）。保守取 45：季末距今天数不足 45 天的季度
    数据可能未披露完，不取（宁可少一季旧数据，不取半季新数据）。
    """
    cutoff = today - timedelta(days=_DISCLOSURE_WINDOW_DAYS)
    ends: set[str] = set()
    for year in (cutoff.year, cutoff.year - 1, cutoff.year - 2):
        for qend in (
            date(year, 3, 31),
            date(year, 6, 30),
            date(year, 9, 30),
            date(year, 12, 31),
        ):
            if qend < cutoff:
                ends.add(qend.isoformat())
    return sorted(ends, reverse=True)[:count]


def _default_quarter_count() -> int:
    raw = os.environ.get("AQSP_HOLDER_NUM_QUARTERS", "")
    try:
        n = int(raw)
        return n if 2 <= n <= 8 else _DEFAULT_QUARTERS
    except (ValueError, TypeError):
        return _DEFAULT_QUARTERS


def _parse_quarter_rows(rows: list[object]) -> list[HolderNumItem]:
    """东财 datacenter 行解析：脏行（无代码）跳过。"""
    out: list[HolderNumItem] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            symbol = str(raw.get("SECURITY_CODE") or "").strip()
            if not symbol:
                continue
            out.append(
                HolderNumItem(
                    symbol=symbol,
                    name=str(
                        raw.get("SECURITY_NAME_ABBR") or raw.get("name") or ""
                    ).strip(),
                    quarter=_norm_date(raw.get("END_DATE")),
                    holder_count=float(raw.get("HOLDER_NUM") or 0),
                    notice_date=_norm_date(raw.get("HOLD_NOTICE_DATE")),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


class HolderNumSource:
    """股东户数源：按季度 filter 分页取全 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[HolderNumItem] = []
        # 上一次 _fetch 是否触达分页上限（真截断时已 warning）
        self.truncated = False

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目统一 runtime data root 约定（PR #232：恒返回发布根/项目根，绝不落 /tmp）
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "holder_count.csv")

    def from_items(self, items: list[HolderNumItem]) -> "HolderNumSource":
        self._items = list(items)
        return self

    def _fetch(
        self, quarters: list[str]
    ) -> list[HolderNumItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"holder_num: 缺少依赖 requests（{e}）") from e

        def _get_page(page: int, quarter: str) -> tuple[list, int]:
            """抓单页：瞬时故障（502 / 断流 / 超时）重试后仍败才抛 DataError。

            返回 (raw_rows, total_count)；末页 raw_rows 可能为空。
            """
            last: Exception | None = None
            for attempt in range(_PAGE_RETRIES):
                if attempt:
                    time.sleep(
                        _PAGE_BACKOFF_SECONDS[
                            min(attempt, len(_PAGE_BACKOFF_SECONDS)) - 1
                        ]
                    )
                params: dict[str, str] = {
                    "reportName": EM_HOLDER_REPORT,
                    "columns": "ALL",
                    "pageSize": str(_PAGE_SIZE),
                    "pageNumber": str(page),
                    # SECURITY_CODE 升序：单季 END_DATE 恒定，按代码排序保证分页稳定
                    "sortColumns": "SECURITY_CODE",
                    "sortTypes": "1",
                    "filter": f"(END_DATE='{quarter}')",
                }
                try:
                    r = requests.get(
                        EM_HOLDER_URL,
                        params=params,
                        headers=_EM_HEADERS,
                        timeout=60,
                    )
                    r.raise_for_status()
                    payload = r.json()
                    result = payload.get("result")
                    rows = result.get("data") if isinstance(result, dict) else None
                    total = 0
                    if isinstance(result, dict):
                        try:
                            total = int(result.get("count") or 0)
                        except (TypeError, ValueError):
                            total = 0
                    return (rows if isinstance(rows, list) else []), total
                except Exception as exc:
                    last = exc
            raise DataError(
                f"holder_num: 东财股东户数抓取失败 {quarter} p{page}（{last}）"
            ) from last

        items: list[HolderNumItem] = []
        for quarter in quarters:
            page = 1
            quarter_items: list[HolderNumItem] = []
            try:
                while True:
                    raw_rows, total = _get_page(page, quarter)
                    if not raw_rows:
                        break
                    page_items = _parse_quarter_rows(raw_rows)
                    quarter_items.extend(page_items)
                    # 取全判定：当季累计达到 count、或本页不满（末页）
                    if (total and len(quarter_items) >= total) or len(
                        page_items
                    ) < _PAGE_SIZE:
                        break
                    page += 1
                    if page > _MAX_PAGES_PER_QUARTER:
                        self.truncated = True
                        _logger.warning(
                            "holder_num: 季度 %s 触达分页上限 %d 页，该季可能被截断",
                            quarter,
                            _MAX_PAGES_PER_QUARTER,
                        )
                        break
            except DataError as e:
                # 单季重试耗尽仍失败 ⇒ 局部降级（保住该季已抓部分 + 继续后续季度），
                # 绝不绑架整批：否则一次瞬断就整表丢/旧（#259）。
                _logger.warning(
                    "holder_num: 季度 %s 抓取降级（%s），已得 %d 行，继续后续季度",
                    quarter,
                    e,
                    len(quarter_items),
                )
                self.truncated = True
            items.extend(quarter_items)
        return items

    def _read_cache(self, path: str) -> list[HolderNumItem]:
        """读旧缓存（坏文件/缺列 ⇒ 空列表，绝不拖垮上层）。"""
        if not os.path.exists(path):
            return []
        try:
            df = pd.read_csv(path, dtype={"symbol": str})
            return [
                HolderNumItem(
                    symbol=str(row["symbol"]).zfill(6),
                    name=str(row.get("name") or ""),
                    quarter=str(row.get("quarter") or ""),
                    holder_count=float(row.get("holder_count") or 0),
                    notice_date=str(row.get("notice_date") or ""),
                )
                for _, row in df.iterrows()
            ]
        except Exception:
            return []

    def load(
        self, force: bool = False, quarters: Optional[list[str]] = None
    ) -> list[HolderNumItem]:
        if quarters is None:
            quarters = _recent_quarter_ends(today_shanghai(), _default_quarter_count())
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            cached = self._read_cache(path)
            if cached:
                self._items = cached
                return self._items
        # force 场景下旧缓存仅作「全灭兜底」，不得替代本次强制重抓（#259）
        prev_items = self._read_cache(path) if force else []
        fetched = self._fetch(quarters)
        # 合并写：按 (symbol, quarter) 键并入旧缓存，fetched 胜出（更新披露），
        # 封顶最近 8 季防膨胀（#259）。降级跑批（某季失败）绝不冲掉已缓存的其余季。
        if fetched:
            merged: dict[tuple[str, str], HolderNumItem] = {
                (i.symbol, i.quarter): i for i in prev_items
            }
            for i in fetched:
                merged[(i.symbol, i.quarter)] = i
            keep_quarters = sorted({i.quarter for i in merged.values()}, reverse=True)[
                :_MAX_CACHED_QUARTERS
            ]
            self._items = [i for i in merged.values() if i.quarter in keep_quarters]
        elif self._items or prev_items:
            # 本次全季抓取失败（网络全灭/断流）⇒ 保全旧缓存、绝不写空表（#259）。
            self._items = self._items or prev_items
            _logger.warning(
                "holder_num: 本次抓取零结果，沿用旧缓存 %d 行（不写空表）",
                len(self._items),
            )
            return self._items
        else:
            self._items = []
        if self._items:
            try:
                pd.DataFrame([i.__dict__ for i in self._items]).to_csv(
                    path, index=False
                )
            except Exception:
                pass
        return self._items

    def items(
        self, autoload: bool = False, quarters: Optional[list[str]] = None
    ) -> list[HolderNumItem]:
        if not self._items and autoload:
            self.load(quarters=quarters)
        return list(self._items)
