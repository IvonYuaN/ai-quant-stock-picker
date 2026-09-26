"""近期公告标题（东财 np-anotice-stock）—— 排雷层「公告关键词」数据源。

产出 ``pit_cache/announcements.csv``（列名 ``symbol`` / ``text`` / ``notice_date``，
与 AnnouncementKeywordFilter 的读取契约**写读同源**：它按 ``text`` 列做关键词扫描）。

接口（2026-09-25 本机实测）：
- ``https://np-anotice-stock.eastmoney.com/api/security/ann``，
  参数 ``ann_type=A&begin_time&end_time&page_index&page_size``；
- **pageSize 上限 100**（传 500/200 均被截到 100，实测 2026-09-24 单页 got=100）；
- **keyword 参数无效**（传「退市」返回的全是无关公告，实测 2026-09-25），
  且响应无 total ⇒ 只能日期窗全量拉取 + **本地关键词匹配**（恰好与
  AnnouncementKeywordFilter 的标题扫描语义一致）；
- 全市场 A 股公告量级约 300 条/日（5 天窗口 ≥1500 条，实测）。

分页终止：页 < 100 条或触达 ``_MAX_PAGES`` 守卫；触达守卫时置 ``truncated``
并 warning（避免静默少拿数据）。

默认窗口：今天 - ``AQSP_ANN_WINDOW_DAYS``（默认 10 天）→ 今天。窗口内标题
即排雷扫描文本；过期窗口靠 daily 预加载持续滚动，缓存覆盖由 meta.json 标记。
"""

from __future__ import annotations

import logging
import os
from aqsp.core.runtime import runtime_data_root
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai

EM_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
# 接口实测 pageSize 上限 100（传更大值被截到 100）。
_PAGE_SIZE = 100
# 300 条/日 × 10 天 ≈ 3000 条 → 30 页；守卫放宽到 100 页（1 万条）防极端放量。
_MAX_PAGES = 100
_DEFAULT_WINDOW_DAYS = 10
_logger = logging.getLogger("aqsp.data.announcement")


@dataclass(frozen=True)
class AnnouncementItem:
    """单条公告（标题粒度，排雷层只需标题文本）。

    ``text`` 列即排雷扫描文本；列名与 AnnouncementKeywordFilter 契约写读同源。
    """

    symbol: str
    name: str
    notice_date: str  # YYYY-MM-DD
    text: str  # 公告标题（= 扫描文本；落盘列名 text）


def _default_window_days() -> int:
    raw = os.environ.get("AQSP_ANN_WINDOW_DAYS", "")
    try:
        n = int(raw)
        return n if 1 <= n <= 30 else _DEFAULT_WINDOW_DAYS
    except (ValueError, TypeError):
        return _DEFAULT_WINDOW_DAYS


def _parse_page(payload: object) -> list[AnnouncementItem]:
    """np-anotice-stock 响应：{"data": {"list": [...]}}。一条公告可挂多只股票。"""
    out: list[AnnouncementItem] = []
    if not isinstance(payload, dict):
        return out
    data = payload.get("data")
    rows = data.get("list") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return out
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        notice_date = str(raw.get("notice_date") or "")[:10]
        text = str(raw.get("title") or raw.get("title_ch") or "").strip()
        if not text:
            continue
        codes = raw.get("codes") or []
        if isinstance(codes, list) and codes:
            for code in codes:
                if not isinstance(code, dict):
                    continue
                symbol = str(code.get("stock_code") or "").strip()
                name = str(code.get("short_name") or "").strip()
                if symbol:
                    out.append(
                        AnnouncementItem(
                            symbol=symbol,
                            name=name,
                            notice_date=notice_date,
                            text=text,
                        )
                    )
        else:
            # 无 codes（理论不应出现）：降级用顶层 stock_code 字段（若有）
            symbol = str(raw.get("stock_code") or "").strip()
            if symbol:
                out.append(
                    AnnouncementItem(
                        symbol=symbol,
                        name=str(raw.get("name") or "").strip(),
                        notice_date=notice_date,
                        text=text,
                    )
                )
    return out


class AnnouncementSource:
    """近期公告标题源：日期窗分页全量 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[AnnouncementItem] = []
        # 上一次 _fetch 是否触达分页上限（真截断时已 warning）
        self.truncated = False

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目统一 runtime data root 约定（PR #232：恒返回发布根/项目根，绝不落 /tmp）
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "announcements.csv")

    def from_items(
        self, items: list[AnnouncementItem]
    ) -> "AnnouncementSource":
        self._items = list(items)
        return self

    def _fetch(self, begin_time: str, end_time: str) -> list[AnnouncementItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"announcement: 缺少依赖 requests（{e}）") from e
        out: list[AnnouncementItem] = []
        page = 1
        while page <= _MAX_PAGES:
            params: dict[str, str] = {
                "ann_type": "A",
                "client_source": "web",
                "page_index": str(page),
                "page_size": str(_PAGE_SIZE),
                "begin_time": begin_time,
                "end_time": end_time,
            }
            try:
                r = requests.get(
                    EM_ANN_URL, params=params, headers=_EM_HEADERS, timeout=60
                )
                r.raise_for_status()
                payload = r.json()
            except Exception as e:
                raise DataError(
                    f"announcement: 东财公告抓取失败 p{page}（{e}）"
                ) from e
            page_items = _parse_page(payload)
            out.extend(page_items)
            if len(page_items) < _PAGE_SIZE:
                break
            page += 1
        else:
            self.truncated = True
            _logger.warning(
                "announcement: 触达分页上限 %d 页，窗口 %s~%s 可能被截断",
                _MAX_PAGES,
                begin_time,
                end_time,
            )
        return out

    def load(
        self, force: bool = False, begin_time: str = "", end_time: str = ""
    ) -> list[AnnouncementItem]:
        today = today_shanghai()
        if not end_time:
            end_time = today.isoformat()
        if not begin_time:
            begin_time = (today - timedelta(days=_default_window_days())).isoformat()
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                df = pd.read_csv(path, dtype={"symbol": str})
                self._items = [
                    AnnouncementItem(
                        symbol=str(row["symbol"]).zfill(6),
                        name=str(row.get("name") or ""),
                        notice_date=str(row.get("notice_date") or ""),
                        text=str(row.get("text") or row.get("title") or ""),
                    )
                    for _, row in df.iterrows()
                ]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(begin_time, end_time)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(
        self,
        autoload: bool = False,
        begin_time: str = "",
        end_time: str = "",
    ) -> list[AnnouncementItem]:
        if not self._items and autoload:
            self.load(begin_time=begin_time, end_time=end_time)
        return list(self._items)
