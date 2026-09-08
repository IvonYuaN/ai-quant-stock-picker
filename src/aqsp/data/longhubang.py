"""龙虎榜（东财）—— 风险/事件信号源。

移植自 `simonlin1212/TradingAgents-astock` v0.5.17（东财 datacenter 取龙虎榜明细）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
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
    """龙虎榜源：取数 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[LongHubangItem] = []

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

    def _fetch(self, trade_date: str = "") -> list[LongHubangItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"longhubang: 缺少依赖 requests（{e}）") from e
        base_params = {
            "reportName": EM_LHB_REPORT,
            "columns": "ALL",
            "pageSize": "500",
            "pageNumber": "1",
        }
        try:
            # 未指定日期时先取最新有数据的交易日（按 TRADE_DATE 倒序第 1 行）
            date = trade_date.strip()
            if not date:
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
                date = str(rows[0].get("TRADE_DATE") or "")[:10]
                if not date:
                    return []
            # TRADE_DATE 东财存全串 datetime，等值 filter 恒 0 行 → 改用当日区间
            params = {
                **base_params,
                "filter": f"(TRADE_DATE>='{date} 00:00:00')(TRADE_DATE<='{date} 23:59:59')",
                "sortColumns": "BILLBOARD_NET_AMT",
                "sortTypes": "-1",
            }
            r = requests.get(EM_LHB_URL, params=params, headers=_EM_HEADERS, timeout=60)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise DataError(f"longhubang: 东财龙虎榜抓取失败（{e}）") from e
        return _parse_items(payload)

    def load(self, force: bool = False, trade_date: str = "") -> list[LongHubangItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                df = pd.read_csv(path)
                self._items = [LongHubangItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(trade_date=trade_date)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(
        self, autoload: bool = False, trade_date: str = ""
    ) -> list[LongHubangItem]:
        if not self._items and autoload:
            self.load(trade_date=trade_date)
        return list(self._items)
