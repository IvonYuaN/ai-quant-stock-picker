"""财联社电报快讯（point-in-time 抓取）—— A 股实时催化信号源。

移植自 `simonlin1212/TradingAgents-astock` v0.5.17 与 `ZhuLinsen/daily_stock_analysis`
共同引用的 `https://www.cls.cn/telegraph` 数据流；按 AQSP 改造：
- 纯 PIT 解析函数不依赖网络，可单测；
- `ClsNewsSource` 负责取数 + 本地 CSV 缓存；网络取数 lazy import，失败抛
  `DataError`；
- 解析容错：单条 JSON 形状漂移跳过该项，绝不让一条坏数据 crash 整批
  （与上游 TradingAgents 的 `_em_get()` 容错思路一致）。

集成点：`news/catalysts` 实时催化 → 失败时优雅回退到现有东财新闻源。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError

# 财联社电报列表（生产机验证；沙箱不直连）
CLS_TELEGRAPH_URL = "https://www.cls.cn/nodeapi/updateTelegraphList"


@dataclass(frozen=True)
class ClsNewsItem:
    """单条财联社快讯。"""

    item_id: str  # 财联社 id
    title: str
    summary: str
    ctime: str  # 发布时间（ISO）
    level: str  # 重要度（A/B/C）
    subjects: tuple[str, ...]  # 关联主题


def _normalize_subjects(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("subject_name") or item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            out.append(name)
    return tuple(out)


def _parse_items(payload: object) -> list[ClsNewsItem]:
    """从 cls.cn telegraph 接口 JSON 解析 item 列表；单条漂移跳过。"""
    if not isinstance(payload, dict):
        return []
    # 真实结构：{"data": {"roll_data": [...]}}；兼容 {"data": [...]} 与 [...]
    data = payload.get("data") if isinstance(payload, dict) else None
    roll: object
    if isinstance(data, dict):
        roll = data.get("roll_data")
    elif isinstance(data, list):
        roll = data
    else:
        roll = payload.get("roll_data") if isinstance(payload, dict) else None
        if roll is None:
            roll = payload if isinstance(payload, list) else []
    if not isinstance(roll, list):
        return []
    out: list[ClsNewsItem] = []
    for raw in roll:
        if not isinstance(raw, dict):
            continue
        try:
            item_id = str(raw.get("id") or raw.get("item_id") or "").strip()
            title = str(raw.get("title") or "").strip()
            summary = str(raw.get("content") or raw.get("brief") or "").strip()
            ctime_raw = raw.get("ctime")
            ctime = (
                pd.Timestamp(ctime_raw).isoformat()
                if ctime_raw not in (None, "")
                else ""
            )
            level = str(raw.get("level") or "").strip()
            out.append(
                ClsNewsItem(
                    item_id=item_id,
                    title=title,
                    summary=summary,
                    ctime=ctime,
                    level=level,
                    subjects=_normalize_subjects(raw.get("subjects")),
                )
            )
        except Exception:  # noqa: BLE001 - 单条漂移跳过，绝不 crash 整批
            continue
    return out


class ClsNewsSource:
    """财联社快讯源：取数 + 本地缓存 + 时点查询。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[ClsNewsItem] = []

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "cls_news.csv")

    def from_items(self, items: list[ClsNewsItem]) -> "ClsNewsSource":
        """注入 item 列表（测试 / 预加载用）。"""
        self._items = list(items)
        return self

    def _fetch(self, limit: int = 50) -> list[ClsNewsItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover - 依赖缺失属环境态
            raise DataError(f"cls_news: 缺少依赖 requests（{e}）") from e
        params = {"app": "CailianpressWeb", "os": "web", "rn": str(limit)}
        try:
            r = requests.get(CLS_TELEGRAPH_URL, params=params, timeout=60)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:  # 网络/SSL/超时/JSON 失败
            raise DataError(f"cls_news: cls.cn 抓取失败（{e}）") from e
        return _parse_items(payload)

    def load(self, force: bool = False, limit: int = 50) -> list[ClsNewsItem]:
        """加载快讯：缓存命中且非强制时读本地，否则抓取并落盘。"""
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                df = pd.read_csv(path)
                self._items = [ClsNewsItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(limit=limit)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(self, autoload: bool = False, limit: int = 50) -> list[ClsNewsItem]:
        if not self._items and autoload:
            self.load(limit=limit)
        return list(self._items)
