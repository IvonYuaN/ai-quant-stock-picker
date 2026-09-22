"""事件日历（点内安全）—— 已知事件的「前瞻预警」与「回溯佐证」层。

用途
----
把**已知事件**组织成可查询的日历，供策略 / 看板做**确定性标注**：

- **前瞻预警**：限售解禁等*事先公告*的事件，提前 N 天提示风险；
- **回溯佐证**：龙虎榜等*已发生*的事件，确认「资金提前埋伏」这类推断。

设计边界（`docs/CONSTITUTION.md` / `AGENTS.md` §4 审查清单）
----------------------------------------------------------
1. **本模块不做网络取数。** 构造只接受已预取的内存记录（`LockupItem` /
   `LongHubangItem`）；唯一的取数入口是 `EventCalendar.from_sources()`，
   且默认 `autoload=False`（只读缓存）。生产由 `scripts/fetch_*.py` 预加载缓存，
   与 `features/pit_enrichment.py` 同一约定。
2. **点内安全（no look-ahead）。** 所有查询都以 `as_of` 过滤：
   - `upcoming_unlocks` 只返回 `event_date >= as_of` 且 `<= as_of + horizon_days`；
   - `recent_longhubang` 只返回 `event_date <= as_of` 且 `>= as_of - lookback_days`。
   **任何查询都不会返回 `as_of` 之后的「已发生」事件。**
3. **只做标注，不改打分。** 本模块产出的严重度 / 计数仅作证据文本，
   **不进入任何基础打分向量**，也不写入 `ledger` 权重。`calculate_score`
   这类纯计算函数不得调用本模块。

⚠️ 已知口径限制（随用随记，别当它是严格 PIT）
-------------------------------------------
东财 `RPT_LIFT_STAGE` 只给**解禁日**（`FREE_DATE`），**不给公告日**。
解禁计划通常在招股书 / 公告中提前数月披露，缺少公告日就无法严格证明
「在 `as_of` 当天市场已知该计划」。因此当前用法限定为
**当日 / 近期预警**（`as_of` ≈ 最新交易日），**不用于历史回测复现**。
要做严格 PIT 回测，需先补「公告日」字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Sequence

from aqsp.data.lockup import LockupItem, LockupSource
from aqsp.data.longhubang import LongHubangItem, LongHubangSource

# ---------------------------------------------------------------
# 严重度分档锚点：解禁股数占总股本比例（`LockupItem.ratio` 是小数，0.0264 = 2.64%）
# 命名常量而非散落字面量；将来若要做成可配置，从这里注入 thresholds。
# ---------------------------------------------------------------
UNLOCK_RATIO_HIGH = 0.10  # ≥10% 总股本解禁 → 高冲击
UNLOCK_RATIO_MEDIUM = 0.03  # ≥3% → 中等冲击
UNLOCK_RATIO_LOW = 0.01  # ≥1% → 轻微冲击

# 龙虎榜净买入（万元）显著额锚点，仅用于证据文案强弱，不参与打分
LHB_NET_AMOUNT_STRONG = 5000.0  # ≥5000 万元净买入


def _norm_symbol(value: object) -> str:
    """把股票代码归一化成 6 位数字串（无法解析时返回空串）。

    ⚠️ 这一步必须做，不是洁癖：`LockupSource` / `LongHubangSource` 的 `load()`
    用的是裸 `pd.read_csv`，pandas 会把 `symbol` 列整列推断成 int，
    `"000001"` 变成 `1` —— **前导零丢失**（上游既有缺陷，已单独立项）。
    若不归一化，日历按 `"1"` 建索引，则全市场所有 `00xxxx` 代码永远查不到，
    且**悄无声息**。在本地层做一次归一化，保证本层不受上游 dtype 影响。
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6) if len(digits) <= 6 else digits


def _parse_iso(value: object) -> Optional[date]:
    """把东财的日期串解析成 :class:`datetime.date`，失败返回 ``None``。

    兼容 ``"2026-09-15 00:00:00"`` / ``"2026-09-15"`` / ``"2026/09/15"``。
    """
    text = str(value or "").strip()
    if not text:
        return None
    # 截到日期段即可：东财常见形态是 "2026-09-15 00:00:00"，也有纯 "2026-09-15"
    head = text.replace("/", "-")[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        return None


def unlock_severity(ratio: float) -> str:
    """按解禁比例给出严重度分档：``high`` / ``medium`` / ``low`` / ``negligible``。"""
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return "negligible"
    if r >= UNLOCK_RATIO_HIGH:
        return "high"
    if r >= UNLOCK_RATIO_MEDIUM:
        return "medium"
    if r >= UNLOCK_RATIO_LOW:
        return "low"
    return "negligible"


@dataclass(frozen=True)
class UpcomingEvent:
    """未来已知事件（前瞻预警）。"""

    symbol: str
    name: str
    event_type: str  # 目前仅 "lockup_expiry"
    event_date: str  # YYYY-MM-DD
    days_until: int  # 距 as_of 的自然日数（>= 0）
    severity: str  # high / medium / low / negligible
    ratio: float  # 解禁占总股本比例
    detail: str  # 人类可读证据行


@dataclass(frozen=True)
class RecentEvent:
    """近期已确认事件（回溯佐证）。"""

    symbol: str
    name: str
    event_type: str  # 目前仅 "longhubang"
    event_date: str  # YYYY-MM-DD
    days_ago: int  # 距 as_of 的自然日数（>= 0）
    net_amount: float  # 龙虎榜净买入额（万元）
    interpretation: str  # 东财机构解读
    detail: str  # 人类可读证据行


class EventCalendar:
    """点内安全的已知事件日历（纯内存查询，永不触网）。

    Args:
        unlocks: 限售解禁记录（已取数）。
        longhubang: 龙虎榜记录（已取数）。
        unlock_horizon_days: 解禁前瞻窗口默认值（自然日）。
        longhubang_lookback_days: 龙虎榜回溯窗口默认值（自然日）。

    不可变的：内部索引按 symbol 分组并按日期排好序。
    """

    def __init__(
        self,
        *,
        unlocks: Sequence[LockupItem] = (),
        longhubang: Sequence[LongHubangItem] = (),
        unlock_horizon_days: int = 30,
        longhubang_lookback_days: int = 5,
    ) -> None:
        self._unlock_horizon_days = max(0, int(unlock_horizon_days))
        self._lhb_lookback_days = max(0, int(longhubang_lookback_days))

        self._unlocks: dict[str, list[tuple[date, LockupItem]]] = {}
        for item in unlocks:
            day = _parse_iso(item.plan_date)
            symbol = _norm_symbol(item.symbol)
            if day is None or not symbol:
                continue
            self._unlocks.setdefault(symbol, []).append((day, item))
        for bucket in self._unlocks.values():
            bucket.sort(key=lambda pair: pair[0])

        self._lhb: dict[str, list[tuple[date, LongHubangItem]]] = {}
        for item in longhubang:
            day = _parse_iso(item.trade_date)
            symbol = _norm_symbol(item.symbol)
            if day is None or not symbol:
                continue
            self._lhb.setdefault(symbol, []).append((day, item))
        # 倒序：最近的排前面，便于取「最近一次上榜」
        for bucket in self._lhb.values():
            bucket.sort(key=lambda pair: pair[0], reverse=True)

    # -----------------------------------------------------------
    # 构造
    # -----------------------------------------------------------
    @classmethod
    def from_sources(
        cls,
        *,
        lockup_source: Optional[LockupSource] = None,
        longhubang_source: Optional[LongHubangSource] = None,
        autoload: bool = False,
        from_date: str = "",
        trade_date: str = "",
        unlock_horizon_days: int = 30,
        longhubang_lookback_days: int = 5,
    ) -> "EventCalendar":
        """从数据源构建日历。

        **`autoload=False`（默认）只读缓存，不触网** —— 这是打分链路里的唯一
        合法用法。`autoload=True` 会发起网络请求，必须在打分链路之外显式调用
        （预加载脚本 / 盘后任务），否则违反「纯计算函数不访问网络」红线。

        任一数据源抛错都 fail-soft 降级为空，保证调用方行为不因缺数据而改变。
        """
        unlock_items: list[LockupItem] = []
        lhb_items: list[LongHubangItem] = []
        if lockup_source is not None:
            try:
                unlock_items = lockup_source.items(
                    autoload=autoload, from_date=from_date
                )
            except Exception:  # noqa: BLE001 - best-effort enrichment
                unlock_items = []
        if longhubang_source is not None:
            try:
                lhb_items = longhubang_source.items(
                    autoload=autoload, trade_date=trade_date
                )
            except Exception:  # noqa: BLE001 - best-effort enrichment
                lhb_items = []
        return cls(
            unlocks=unlock_items,
            longhubang=lhb_items,
            unlock_horizon_days=unlock_horizon_days,
            longhubang_lookback_days=longhubang_lookback_days,
        )

    # -----------------------------------------------------------
    # 只读属性
    # -----------------------------------------------------------
    @property
    def unlock_horizon_days(self) -> int:
        """解禁前瞻窗口默认值（自然日）。"""
        return self._unlock_horizon_days

    @property
    def longhubang_lookback_days(self) -> int:
        """龙虎榜回溯窗口默认值（自然日）。"""
        return self._lhb_lookback_days

    def is_empty(self) -> bool:
        """两个数据面都没有内容时为 True（调用方据此整体跳过标注）。"""
        return not self._unlocks and not self._lhb

    def has_unlock_data(self) -> bool:
        """是否装载了解禁数据面。

        调用方**只能在本方法为 True 时**才把「查不到解禁」解读成
        「没有解禁」。否则那是「没数据」，不是「没事件」。
        """
        return bool(self._unlocks)

    def has_longhubang_data(self) -> bool:
        """是否装载了龙虎榜数据面（语义同上）。"""
        return bool(self._lhb)

    def tracked_symbols(self) -> int:
        """已索引的股票数（两个数据面去重后的并集大小）。"""
        return len(set(self._unlocks) | set(self._lhb))

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    def upcoming_unlocks(
        self,
        symbol: str,
        as_of: str,
        horizon_days: Optional[int] = None,
    ) -> list[UpcomingEvent]:
        """返回 ``symbol`` 在 ``[as_of, as_of + horizon]`` 内的解禁事件（升序）。

        `as_of` 之前的解禁**不算预警**（已发生），因此被排除。
        `as_of` 无法解析时返回空列表。
        """
        ref = _parse_iso(as_of)
        if ref is None:
            return []
        horizon = (
            self._unlock_horizon_days
            if horizon_days is None
            else max(0, int(horizon_days))
        )
        out: list[UpcomingEvent] = []
        for day, item in self._unlocks.get(_norm_symbol(symbol), []):
            days_until = (day - ref).days
            if days_until < 0 or days_until > horizon:
                continue
            ratio = float(item.ratio)
            kind = item.lockup_type or "限售解禁"
            detail = (
                f"预计 {days_until} 个自然日后（{day.isoformat()}）限售解禁"
                f"：{kind}，{item.lockup_shares:.0f} 万股，占总股本 {ratio:.2%}"
            )
            out.append(
                UpcomingEvent(
                    symbol=_norm_symbol(item.symbol),
                    name=item.name,
                    event_type="lockup_expiry",
                    event_date=day.isoformat(),
                    days_until=days_until,
                    severity=unlock_severity(ratio),
                    ratio=ratio,
                    detail=detail,
                )
            )
        out.sort(key=lambda ev: ev.days_until)
        return out

    def recent_longhubang(
        self,
        symbol: str,
        as_of: str,
        lookback_days: Optional[int] = None,
    ) -> list[RecentEvent]:
        """返回 ``symbol`` 在 ``[as_of - lookback, as_of]`` 内的上榜记录（新的在前）。

        `as_of` 之后才发生的记录**一律排除**（no look-ahead）。
        `as_of` 无法解析时返回空列表。
        """
        ref = _parse_iso(as_of)
        if ref is None:
            return []
        lookback = (
            self._lhb_lookback_days
            if lookback_days is None
            else max(0, int(lookback_days))
        )
        out: list[RecentEvent] = []
        for day, item in self._lhb.get(_norm_symbol(symbol), []):
            days_ago = (ref - day).days
            if days_ago < 0 or days_ago > lookback:
                continue
            strength = (
                "大额净买入"
                if item.net_amount >= LHB_NET_AMOUNT_STRONG
                else "净买入"
                if item.net_amount > 0
                else "净卖出"
            )
            detail = (
                f"{days_ago} 个自然日前（{day.isoformat()}）登上龙虎榜，"
                f"{strength} {abs(item.net_amount):.0f} 万元"
            )
            if item.interpretation:
                detail = f"{detail}（{item.interpretation}）"
            out.append(
                RecentEvent(
                    symbol=_norm_symbol(item.symbol),
                    name=item.name,
                    event_type="longhubang",
                    event_date=day.isoformat(),
                    days_ago=days_ago,
                    net_amount=float(item.net_amount),
                    interpretation=item.interpretation,
                    detail=detail,
                )
            )
        out.sort(key=lambda ev: ev.days_ago)
        return out
