"""龙虎榜（东财）—— 风险/事件信号源。

移植自 `simonlin1212/TradingAgents-astock` v0.5.17（东财 datacenter 取龙虎榜明细）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错 + **多日区间取数**。

覆盖区间（重要 —— 2026-09-22 复核发现，踩坑记录勿删）：
原实现的 filter 是**单日区间**，`load()` 又用 `to_csv` **覆盖式**写盘 ⇒
`pit_cache/longhubang.csv` 恒定只含 1 个交易日。而消费方按 `lookback_days=5`
查「近 5 日是否上榜」——**1 天的数据证明不了 5 天的事**；更糟的是它会消解
「必须有龙虎榜/公告确认」这条人工核验项，等于**因为数据不全反而降低了复核门槛**。

现改为按 `[anchor - lookback_days, anchor]` 多日区间取数并合并后写缓存。
注意 `pageSize=500`：5 个交易日的全市场龙虎榜**可能超 500 条**，此时
`_is_truncated` 会置位且 warning **明确写「lookback 覆盖不完整」**，
绝不允许下游误以为 5 天都看全了。排序为 `TRADE_DATE` 降序，因此若真被截断，
丢掉的是最旧的那几天（覆盖区间由消费方按实际数据自行判定）。
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError

# 东财龙虎榜明细（2026-09-08 生产机实测：RPT_DAILYBILLBOARD_DETAILSNEW，
# 每股票-日一行，含机构解读 EXPLAIN；金额单位为元）
EM_LHB_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_LHB_REPORT = "RPT_DAILYBILLBOARD_DETAILSNEW"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
_PAGE_SIZE = 500
DEFAULT_LOOKBACK_DAYS = 5
_logger = logging.getLogger("aqsp.data.longhubang")


@dataclass(frozen=True)
class LongHubangItem:
    """单条龙虎榜上榜记录（股票-交易日粒度）。"""

    trade_date: str  # 交易日期 YYYY-MM-DD
    symbol: str
    name: str
    close_price: float  # 收盘价（元）
    change_rate: float  # 涨跌幅（%）
    buy_amount: float  # 龙虎榜买入额（万元）
    sell_amount: float  # 龙虎榜卖出额（万元）
    net_amount: float  # 龙虎榜净买入额（万元）
    interpretation: str  # 机构解读（东财 EXPLAIN）


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _is_truncated(parsed_count: int, page_size: int) -> bool:
    """页满即可能被 pageSize 截断 —— 用于避免「静默少拿数据」。"""
    return parsed_count >= page_size


def _parse_items(payload: object) -> list[LongHubangItem]:
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
    out: list[LongHubangItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                LongHubangItem(
                    trade_date=str(raw.get("TRADE_DATE") or "").strip(),
                    symbol=str(
                        raw.get("SECURITY_CODE") or raw.get("code") or ""
                    ).strip(),
                    name=str(
                        raw.get("SECURITY_NAME_ABBR") or raw.get("name") or ""
                    ).strip(),
                    close_price=_to_float(raw.get("CLOSE_PRICE")),
                    change_rate=_to_float(raw.get("CHANGE_RATE")),
                    buy_amount=_to_float(raw.get("BILLBOARD_BUY_AMT")) / 1e4,
                    sell_amount=_to_float(raw.get("BILLBOARD_SELL_AMT")) / 1e4,
                    net_amount=_to_float(raw.get("BILLBOARD_NET_AMT")) / 1e4,
                    interpretation=str(
                        raw.get("EXPLAIN") or raw.get("interpretation") or ""
                    ).strip(),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class LongHubangSource:
    """龙虎榜源：取数 + 本地缓存。默认取「锚点交易日前 5 个自然日」区间。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[LongHubangItem] = []
        # 上一次 _fetch 的结果是否疑似被 pageSize 截断（真截断时已 warning）
        self.truncated = False

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "longhubang.csv")

    def from_items(self, items: list[LongHubangItem]) -> "LongHubangSource":
        self._items = list(items)
        return self

    def _fetch(
        self, trade_date: str = "", lookback_days: int = DEFAULT_LOOKBACK_DAYS
    ) -> list[LongHubangItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"longhubang: 缺少依赖 requests（{e}）") from e
        self.truncated = False
        base_params = {
            "reportName": EM_LHB_REPORT,
            "columns": "ALL",
            "pageSize": str(_PAGE_SIZE),
            "pageNumber": "1",
        }
        try:
            anchor = trade_date.strip()[:10]
            if anchor:
                try:
                    anchor = date.fromisoformat(anchor).isoformat()
                except ValueError as e:
                    raise DataError(
                        f"longhubang: trade_date 非法（{trade_date}）"
                    ) from e
            else:
                # 未指定日期时先取最新有数据的交易日（按 TRADE_DATE 倒序第 1 行）
                probe = requests.get(
                    EM_LHB_URL,
                    params={
                        **base_params,
                        "pageSize": "1",
                        "sortColumns": "TRADE_DATE",
                        "sortTypes": "-1",
                    },
                    headers=_EM_HEADERS,
                    timeout=60,
                )
                probe.raise_for_status()
                rows = ((probe.json().get("result") or {}).get("data")) or []
                if not rows:
                    return []
                anchor = str(rows[0].get("TRADE_DATE") or "")[:10]
                if not anchor:
                    return []
            # 多日区间（自然日）：单日 filter 会让缓存恒定只含 1 天
            start = (
                date.fromisoformat(anchor) - timedelta(days=max(int(lookback_days), 0))
            ).isoformat()
            # TRADE_DATE 东财存全串 datetime，等值 filter 恒 0 行 → 用区间
            params = {
                **base_params,
                "filter": (
                    f"(TRADE_DATE>='{start} 00:00:00')(TRADE_DATE<='{anchor} 23:59:59')"
                ),
                "sortColumns": "TRADE_DATE,BILLBOARD_NET_AMT",
                "sortTypes": "-1,-1",
            }
            r = requests.get(EM_LHB_URL, params=params, headers=_EM_HEADERS, timeout=60)
            r.raise_for_status()
            payload = r.json()
        except DataError:
            raise
        except Exception as e:
            raise DataError(f"longhubang: 东财龙虎榜抓取失败（{e}）") from e
        items = _parse_items(payload)
        self.truncated = _is_truncated(len(items), _PAGE_SIZE)
        if self.truncated:
            _logger.warning(
                "longhubang: 结果可能被 pageSize=%d 截断（实得 %d 条），"
                "lookback 覆盖不完整（%s ~ %s 未取全），"
                "请降低 lookback_days 或加分页",
                _PAGE_SIZE,
                len(items),
                start,
                anchor,
            )
        return items

    def load(
        self,
        force: bool = False,
        trade_date: str = "",
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> list[LongHubangItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                # 同 `lockup.load()`：显式锁死 `symbol` 为字符串，避免
                # `"000001"` 被推断成 `1` 而**静默丢失前导零**。
                df = pd.read_csv(path, dtype={"symbol": str})
                self._items = [LongHubangItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(trade_date=trade_date, lookback_days=lookback_days)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(
        self,
        autoload: bool = False,
        trade_date: str = "",
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> list[LongHubangItem]:
        if not self._items and autoload:
            self.load(trade_date=trade_date, lookback_days=lookback_days)
        return list(self._items)
