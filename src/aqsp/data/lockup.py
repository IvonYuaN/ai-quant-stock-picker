"""限售解禁（东财）—— 风险日历数据源。

移植自 `simonlin1212/TradingAgents-astock` v0.5.17（东财 datacenter 取解禁计划）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错 + **有界取数窗口**。

取数窗口（重要 —— 2026-09-22 实测，踩坑记录勿删）：
东财该报表按 `FREE_DATE` 排序，而解禁日可以远到 10 年后。**只加 `filter` 不改排序
是无效补丁**。四组对照实测（pageSize=500 / pageNumber=1，只比 plan_date 区间）：

| 组合 | filter | sortTypes | rows | pages | min | max |
| --- | --- | --- | --- | --- | --- | --- |
| 原实现 | 无 | -1 降序 | 500 | 64 | 2028-10-17 | 2035-10-29 |
| 仅加 from | (FREE_DATE>=今天) | -1 降序 | 500 | 6 | 2028-10-17 | 2035-10-29 |
| 升序 + from | (FREE_DATE>=今天) | 1 升序 | 500 | 6 | 2026-09-22 | 2026-12-29 |
| **有界窗口** | (>=今天)(<=今天+90d) | 1 升序 | 449 | 1 | 2026-09-22 | 2026-12-21 |

根因：`filter` 只缩小**全集**（pages 64→6），并不改变「降序后第 1 页取到哪 500 条」
—— 降序的头永远是最远未来那批。所以原实现缓存里最早一条都在 **2028-10-17**，
导致消费方的「解禁前瞻预警」（查 `[today, today+30d]`）在真实数据下**恒空**，
不是降级而是完全报不出东西。

本模块因此改为**有界窗口**：默认取 `今天 → 今天 + DEFAULT_HORIZON_DAYS`。解禁只有
「未来一段」是有用的，取全集既被 pageSize 截断、又拿不到近期。窗口内实测 449 条
< 500，单页可一次取全；`_is_truncated` 只作兜底守卫。
"""

from __future__ import annotations

import logging
import os
from aqsp.core.runtime import runtime_data_root
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai

# 东财解禁（2026-09-08 生产机实测：RPT_LIFT_STAGE，FREE_DATE 倒序；
# RPT_LIFTING_DATA 不存在。FREE_SHARES 为东财原值未换算）
EM_LOCKUP_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_LOCKUP_REPORT = "RPT_LIFT_STAGE"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
_PAGE_SIZE = 500
DEFAULT_HORIZON_DAYS = 90
_logger = logging.getLogger("aqsp.data.lockup")


@dataclass(frozen=True)
class LockupItem:
    """单条解禁计划。"""

    symbol: str
    name: str
    plan_date: str  # 解禁日 YYYY-MM-DD
    lockup_shares: float  # 解禁股数（万股）
    ratio: float  # 占总股本比例
    lockup_type: str  # 首发/定增/股权激励等


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm_day(v: str) -> str:
    """校验并规整为 YYYY-MM-DD；空/非法一律返回空串。"""
    s = (v or "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return ""


def _is_truncated(parsed_count: int, page_size: int) -> bool:
    """页满即可能被 pageSize 截断 —— 用于避免「静默少拿数据」。"""
    return parsed_count >= page_size


def _parse_items(payload: object) -> list[LockupItem]:
    """东财 datacenter 响应：{"result": {"data": [...]}}。单条漂移跳过。"""
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, dict):
        data = result.get("data")
    elif isinstance(result, list):
        data = result
    else:
        data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    out: list[LockupItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                LockupItem(
                    symbol=str(
                        raw.get("SECURITY_CODE") or raw.get("code") or ""
                    ).strip(),
                    name=str(
                        raw.get("SECURITY_NAME_ABBR") or raw.get("name") or ""
                    ).strip(),
                    plan_date=str(
                        raw.get("FREE_DATE") or raw.get("plan_date") or ""
                    ).strip(),
                    lockup_shares=_to_float(
                        raw.get("FREE_SHARES") or raw.get("shares")
                    ),
                    ratio=_to_float(raw.get("FREE_RATIO") or raw.get("ratio")),
                    lockup_type=str(
                        raw.get("FREE_SHARES_TYPE") or raw.get("type") or ""
                    ).strip(),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class LockupSource:
    """限售解禁源：取数 + 本地缓存。默认取「今天 → 今天+90 天」有界窗口。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[LockupItem] = []
        # 上一次 _fetch 的结果是否疑似被 pageSize 截断（真截断时已 warning）
        self.truncated = False

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "lockup.csv")

    def from_items(self, items: list[LockupItem]) -> "LockupSource":
        self._items = list(items)
        return self

    def _resolve_window(self, from_date: str, to_date: str) -> tuple[str, str]:
        """解析成**始终有界**的 (from, to)，杜绝「无上界 ⇒ 只拿到最远未来」。"""
        start = _norm_day(from_date) or today_shanghai().isoformat()
        end = (
            _norm_day(to_date)
            or (
                date.fromisoformat(start) + timedelta(days=DEFAULT_HORIZON_DAYS)
            ).isoformat()
        )
        return start, end

    def _fetch(self, from_date: str = "", to_date: str = "") -> list[LockupItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"lockup: 缺少依赖 requests（{e}）") from e
        start, end = self._resolve_window(from_date, to_date)
        params: dict[str, str] = {
            "reportName": EM_LOCKUP_REPORT,
            "columns": "ALL",
            "pageSize": str(_PAGE_SIZE),
            "pageNumber": "1",
            # 升序：窗口被调宽导致页满时，截断掉的是「最远」而非「最近」的解禁
            "sortColumns": "FREE_DATE",
            "sortTypes": "1",
            "filter": f"(FREE_DATE>='{start}')(FREE_DATE<='{end}')",
        }
        try:
            r = requests.get(
                EM_LOCKUP_URL, params=params, headers=_EM_HEADERS, timeout=60
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise DataError(f"lockup: 东财解禁抓取失败（{e}）") from e
        items = _parse_items(payload)
        self.truncated = _is_truncated(len(items), _PAGE_SIZE)
        if self.truncated:
            _logger.warning(
                "lockup: 结果可能被 pageSize=%d 截断（实得 %d 条），请收窄窗口"
                "（from_date/to_date）或加分页；被截断的是最远期的解禁",
                _PAGE_SIZE,
                len(items),
            )
        return items

    def load(
        self, force: bool = False, from_date: str = "", to_date: str = ""
    ) -> list[LockupItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                # dtype 必须显式指定：`symbol` 列若被推断成 int/float，
                # `"000001"` 会变成 `1` / `1.0` —— **前导零丢失**，且是静默的。
                # 消费方（如 `features/event_calendar.py`）会因此把解禁记录挂到
                # 另一只股票上，或让全市场 `00xxxx` 代码恒查不到。
                df = pd.read_csv(path, dtype={"symbol": str})
                self._items = [LockupItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(from_date=from_date, to_date=to_date)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(
        self, autoload: bool = False, from_date: str = "", to_date: str = ""
    ) -> list[LockupItem]:
        if not self._items and autoload:
            self.load(from_date=from_date, to_date=to_date)
        return list(self._items)
