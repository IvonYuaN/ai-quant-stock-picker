"""东财概念板块（bkzj dataapi）—— 概念分类 + 资金流向数据源。

原走 push2 `clist/get`，2026-10-11 实测该端点在全部出口（prod/Mac/runner/海外代理）
均被请求级阻断（issue #333），且 `push2delay` 降级路径同样失效。
现换用同源页面在用端点 `data.eastmoney.com/dataapi/bkzj/getbkzj`
（prod 直连实测 200，单请求返回全部 506 个概念板块，字段口径同 clist 的 f 码）。
保持与 `cls_news` / `longhubang` / `lockup` 一致的 fail-soft 模式。
"""

from __future__ import annotations

import os
from aqsp.core.runtime import runtime_data_root
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError

# 东财板块资金流 dataapi（issue #333：clist/get 端点级被切后的替代，2026-10-11 实测）
# m:90+t:3 = 概念板块；key 逗号分隔多字段：
# f3 = 涨跌幅（×100），f62 = 主力净流入（元），f184 = 主力净占比（×100），
# f104/f105 = 上涨/下跌家数
EM_BKZJ_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
EM_BKZJ_PARAMS = {
    "key": "f3,f62,f184,f104,f105",
    "code": "m:90+t:3",
}
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/bkzj/gn.html",
}


@dataclass(frozen=True)
class ConceptBoardItem:
    """单个概念板块。"""

    board_code: str
    board_name: str
    up_count: int  # 上涨家数（f104）
    down_count: int  # 下跌家数（f105）
    change_pct: float  # 当日涨跌幅 %（f3）
    main_net_inflow: float  # 主力净流入（万元，f62 元 → 万元）
    main_net_ratio: float  # 主力净占比 %（f184）


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_int(v: object) -> int:
    try:
        if v in (None, ""):
            return 0
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _parse_items(payload: object) -> list[ConceptBoardItem]:
    """东财 bkzj dataapi 响应：{"data": {"diff": [...]}}。单条漂移跳过。

    数值口径（2026-10-11 实测）：f3 / f184 为 ×100（f3=470 → 4.70%），
    f62 为元（→ 万元）。
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    diff = data.get("diff") if isinstance(data, dict) else None
    if not isinstance(diff, list):
        return []
    out: list[ConceptBoardItem] = []
    for raw in diff:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                ConceptBoardItem(
                    board_code=str(raw.get("f12") or "").strip(),
                    board_name=str(raw.get("f14") or "").strip(),
                    up_count=_to_int(raw.get("f104")),
                    down_count=_to_int(raw.get("f105")),
                    change_pct=_to_float(raw.get("f3")) / 100.0,
                    main_net_inflow=_to_float(raw.get("f62")) / 1e4,
                    main_net_ratio=_to_float(raw.get("f184")) / 100.0,
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class ConceptBoardSource:
    """概念板块源：取数 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[ConceptBoardItem] = []

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "concept_board.csv")

    def from_items(self, items: list[ConceptBoardItem]) -> "ConceptBoardSource":
        self._items = list(items)
        return self

    def _fetch(self) -> list[ConceptBoardItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"concept_board: 缺少依赖 requests（{e}）") from e
        # dataapi bkzj 单请求返回全部概念板块（无分页），排序由 key 决定，
        # 展示侧（info_stream）自行按涨跌幅重排
        try:
            r = requests.get(
                EM_BKZJ_URL,
                params=EM_BKZJ_PARAMS,
                headers=_EM_HEADERS,
                timeout=60,
            )
            r.raise_for_status()
            return _parse_items(r.json())
        except Exception as e:  # noqa: BLE001 - fail-soft：统一抛 DataError
            raise DataError(f"concept_board: 东财概念板块抓取失败（{e}）") from e

    def load(self, force: bool = False) -> list[ConceptBoardItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                df = pd.read_csv(path)
                self._items = [ConceptBoardItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch()
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(self, autoload: bool = False) -> list[ConceptBoardItem]:
        if not self._items and autoload:
            self.load()
        return list(self._items)
