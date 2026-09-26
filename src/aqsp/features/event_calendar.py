"""事件日历（点内安全）—— 已知事件的「前瞻预警」与「回溯佐证」层。

用途
----
把**已知事件**组织成可查询的日历，供策略 / 看板做**确定性标注**：

- **前瞻预警**：限售解禁等*事先公告*的事件，提前 N 天提示风险；
- **回溯佐证**：龙虎榜等*已发生*的事件，确认「资金提前埋伏」这类推断。

设计边界（`docs/CONSTITUTION.md` / `AGENTS.md` §4 审查清单）
----------------------------------------------------------
1. **本模块不做网络取数。** 构造只接受已预取的内存记录（`LockupItem` /
   `LongHubangItem`）。打分链路的**唯一**入口是 `EventCalendar.from_cache()`
   （只读 `pit_cache/*.csv`，无任何联网路径）。`from_sources()` 仅供**预加载脚本**
   使用，且其 `autoload` 语义**与直觉相反**，务必先读该方法的 docstring。
   设计意图是由 `scripts/fetch_*.py` 预加载 `pit_cache/*.csv` 后注入，
   与 `features/pit_enrichment.py` 同一约定 ——
   ⚠️ **但截至 2026-09-22，这些脚本没有任何调度方、缓存也没有产出方**
   （后端审计结论 B），所以本模块在生产环境下**拿不到数据**。
   这是链路缺口，不是本模块的缺陷；同样地，本模块也**尚无生产调用方**。
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

import logging
import os
import re
from aqsp.core.runtime import runtime_data_root as resolve_runtime_data_root
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional, Sequence

# ⚠️ 只在类型标注下导入 `aqsp.data.*`：本模块被 `strategies/event_driven.py` 导入，
# 若在此做运行时导入会把整个 `aqsp.data` 拉进**打分链路的 import 图**
# （实测 0.245s → 0.327s）。两个 Item 类仅用于类型标注，故用 TYPE_CHECKING 解耦。
if TYPE_CHECKING:
    from aqsp.data.lockup import LockupItem, LockupSource
    from aqsp.data.longhubang import LongHubangItem, LongHubangSource

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# 严重度分档锚点：解禁股数占总股本比例（`LockupItem.ratio` 是小数，0.0264 = 2.64%）
# 命名常量而非散落字面量；将来若要做成可配置，从这里注入 thresholds。
# ---------------------------------------------------------------
UNLOCK_RATIO_HIGH = 0.10  # ≥10% 总股本解禁 → 高冲击
UNLOCK_RATIO_MEDIUM = 0.03  # ≥3% → 中等冲击
UNLOCK_RATIO_LOW = 0.01  # ≥1% → 轻微冲击

# 龙虎榜净买入（万元）显著额锚点，仅用于证据文案强弱，不参与打分
LHB_NET_AMOUNT_STRONG = 5000.0  # ≥5000 万元净买入

# pit_cache 约定（与 `aqsp.data.lockup` / `aqsp.data.longhubang` 的写侧路径一致）
PIT_CACHE_DIRNAME = "pit_cache"
LOCKUP_CACHE_FILENAME = "lockup.csv"
LONGHUBANG_CACHE_FILENAME = "longhubang.csv"

# 非交易日补偿：前瞻窗口的起点常落在周末/节假日，那些日子**不存在任何数据行**，
# 若严格要求「数据最早日 <= 窗口起点」，正常取数也会被误判成「覆盖不足」。
# 给 2 个自然日容差（够覆盖一个常规周末）。**故意给小值**：长假仍会保守地报
# 「未核实」—— 方向是「宁可不核实，也不假装核实过」。
_COVERAGE_SLACK_DAYS = 2


def _pit_cache_path(filename: str, runtime_data_root: Optional[str] = None) -> str:
    """拼出 ``<root>/pit_cache/<filename>``；``root`` 缺省走环境变量 / 系统临时目录。

    与 `lockup.py:101-108` 的 `_default_cache_path()` 同一规则，
    保证读写双方指向同一个文件。
    """
    root = runtime_data_root or str(resolve_runtime_data_root())
    return os.path.join(root, PIT_CACHE_DIRNAME, filename)


def _is_missing(value: object) -> bool:
    """``None`` / NaN / NaT 判定（NaN 与 NaT 都满足「自己不等于自己」）。"""
    if value is None:
        return True
    try:
        return bool(value != value)  # type: ignore[operator]
    except (TypeError, ValueError):
        return False


# 整数型浮点的文本形态：`1.0` / `600519.00`（`pd.read_csv` 列升格成 float64 后常见）
_INT_FLOAT_RE = re.compile(r"^(\d+)\.0+$")
# 非整数小数：`1.5` —— 无法可靠还原成 6 位代码（注意 `000001.SZ` 里的 `.S` 不匹配）
_FRACTIONAL_RE = re.compile(r"\.\d")


def _norm_symbol(value: object) -> str:
    """把股票代码归一化成 6 位数字串；**无法可靠解析时返回空串**（宁丢弃不误配）。

    ⚠️ 这一步必须做，不是洁癖：`LockupSource` / `LongHubangSource` 的 `load()`
    用的是裸 `pd.read_csv`，`symbol` 列会被整列升格 ——
    - 全是 `"600000"` 这类 → int，`"000001"` 变成 `1`（**前导零丢失**）；
    - **只要任意一行 symbol 为空**（`_parse_items` 会产出 `symbol=""`），pandas 会
      把整列升格成 **float64** ⇒ 得到 `1.0` / `600519.0`。

    若不做归一化，日历会按错误键建索引，后果**不是查不到而是查错**：
    `str(1.0)` = `"1.0"` → 数字位 `"10"` → 键 `"000010"`，
    于是查询 `"000001"` 永远为空，而查询 `"000010"` **会拿到 000001 的解禁记录** ——
    证据挂到另一只股票上，且全程无声。

    因此这里对 float 形态显式退化，并且**数字位超过 6 位一律判为无法解析**
    （例如 `600519.0` 若被误当月字符串会得到 7 位 `"6005190"`，绝不能拿去当键）。
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    matched = _INT_FLOAT_RE.match(text)
    if matched:
        text = matched.group(1)  # 1.0 -> "1"；600519.0 -> "600519"
    elif _FRACTIONAL_RE.search(text):
        return ""  # 形如 "1.5" 的非整数小数：无法可靠还原，宁可丢弃也不误配
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits or len(digits) > 6:
        return ""
    return digits.zfill(6)


def _to_float(value: object) -> float:
    """容错取数：``None`` / 空串 / 非数字 ⇒ ``0.0``。

    与 `aqsp.data.lockup._to_float` 同构（沿仓库既有惯例命名）。
    ⚠️ 本模块需要它是因为对象可能来自 **CSV 回读**：若某列整列为空，
    `pd.read_csv` 会给出 `""`（object dtype）而非数字，直接 `float()` 会抛。
    仓库里同类 helper 已有 12 份，归并列入后续清理 PR（不在本 PR 扩大范围）。
    """
    try:
        if value in (None, ""):
            return 0.0
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


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

        # 去重：同一份记录若被重复喂入（分页重叠、缓存与实时叠加），
        # **不能让同一条预警/佐证报两遍**。键取「业务身份」而非对象相等。
        self._unlocks: dict[str, list[tuple[date, LockupItem]]] = {}
        seen_unlocks: set[tuple[object, ...]] = set()
        for item in unlocks:
            day = _parse_iso(item.plan_date)
            symbol = _norm_symbol(item.symbol)
            if day is None or not symbol:
                continue
            key = (
                symbol,
                day,
                str(item.lockup_type or ""),
                round(_to_float(item.lockup_shares), 4),
                round(_to_float(item.ratio), 8),
            )
            if key in seen_unlocks:
                continue
            seen_unlocks.add(key)
            self._unlocks.setdefault(symbol, []).append((day, item))
        for bucket in self._unlocks.values():
            bucket.sort(key=lambda pair: pair[0])

        self._lhb: dict[str, list[tuple[date, LongHubangItem]]] = {}
        seen_lhb: set[tuple[object, ...]] = set()
        for item in longhubang:
            day = _parse_iso(item.trade_date)
            symbol = _norm_symbol(item.symbol)
            if day is None or not symbol:
                continue
            key = (
                symbol,
                day,
                round(_to_float(item.net_amount), 4),
                str(item.interpretation or ""),
            )
            if key in seen_lhb:
                continue
            seen_lhb.add(key)
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

        ⚠️ `autoload` 的真实语义（**2026-09-22 实测勘误：原 docstring 写反了**）：

        | 取值 | 实际行为 |
        |---|---|
        | `False` | `items()` 直接返回该 source **已注入**的 items。全新 source 上**什么都不读** —— 不读缓存、不联网，得到**空日历**。 |
        | `True` | 触发 `load()`：**缓存优先**（`lockup.py:141`），缓存缺失或损坏时才 `_fetch()` **联网**。 |

        因此：
        - **「读缓存、绝不联网」的正解是 `EventCalendar.from_cache()`** ——
          它自己读 CSV 且没有联网路径。打分链路**唯一**合法入口就是它。
        - 本方法仅供**预加载脚本**（取数进程）使用。
        - `from_sources(autoload=True)` 在缓存齐备时确实不联网（实测 `_fetch` 调用
          0 次），但**缓存缺失时会联网** ⇒ 只可在打分链路之外调用。
        - `from_sources(autoload=False)` **不会**读缓存，别指望它 ——
          默认保留 `False` 是取「宁可空、不可联网」的失效安全。

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

    @classmethod
    def from_cache(
        cls,
        *,
        runtime_data_root: Optional[str] = None,
        unlock_horizon_days: int = 30,
        longhubang_lookback_days: int = 5,
    ) -> "EventCalendar":
        """从 ``pit_cache/*.csv`` **只读**构建日历 —— **绝不联网**，缺缓存即空日历。

        这是**打分链路唯一**该用的构造入口。为什么不走 `from_sources()`：
        `lockup.py:155-158` 把「读磁盘缓存」门控在 `autoload` 上 ——

        - `autoload=False` 只返回**进程内** `from_items()` 注入的记录、**不读磁盘**
          ⇒ `fetch_*.py`（另一个进程）写下的 CSV 在本进程**永远读不到**
          （实测 `from_items([]) or items()==[]` → True）；
        - `autoload=True` 在缓存缺失时会**直接 `_fetch()` 联网**
          ⇒ 违反「纯计算函数不访问网络」红线。

        即：**现有 Source API 里不存在「读缓存且不触网」的开关**。本方法因此绕开
        Source 自己读 CSV，并保证：

        - `dtype={"symbol": str}` —— **从源头**锁死股票代码为字符串，
          前导零不再依赖 `_norm_symbol` 事后兜底；
        - 缓存缺失 / 损坏 / 单行坏数据 ⇒ 降级（空 / 跳过该行），**从不抛错、从不联网**。

        Args:
            runtime_data_root: pit_cache 根目录；缺省按 ``AQSP_RUNTIME_DATA_ROOT``
                → 系统临时目录 解析（与写侧 `lockup.py:101-108` 同规则）。
        """
        return cls(
            unlocks=cls._read_cache_items(
                _pit_cache_path(LOCKUP_CACHE_FILENAME, runtime_data_root), "lockup"
            ),
            longhubang=cls._read_cache_items(
                _pit_cache_path(LONGHUBANG_CACHE_FILENAME, runtime_data_root),
                "longhubang",
            ),
            unlock_horizon_days=unlock_horizon_days,
            longhubang_lookback_days=longhubang_lookback_days,
        )

    @staticmethod
    def _read_cache_items(path: str, kind: str) -> list:
        """把一个 pit_cache CSV 读成 dataclass 列表；任何问题降级为空 / 跳过该行。

        ``symbol`` 显式按 ``str`` 读：否则 pandas 会把整列推断成 int（全是
        `"600000"` 这类）或 **float**（存在空值时），`"000001"` → `1` / `1.0`，
        **前导零丢失**。在读取处锁死 dtype 比事后归一化更干净、也更难被绕过。
        """
        if not os.path.exists(path):
            _logger.debug("event_calendar: 缓存不存在，按空处理（%s）", path)
            return []
        try:
            import pandas as pd

            frame = pd.read_csv(path, dtype={"symbol": str})
        except Exception:  # noqa: BLE001 - 缓存损坏按缺失处理，绝不向上抛
            _logger.debug("event_calendar: 缓存读取失败（%s）", path, exc_info=True)
            return []

        # 延迟导入：本模块在「不读缓存」的路径上保持 `aqsp.data` 不在 import 图里
        if kind == "lockup":
            from aqsp.data.lockup import LockupItem as _Item
        else:
            from aqsp.data.longhubang import LongHubangItem as _Item

        names = [(f.name, str(f.type)) for f in fields(_Item)]
        out: list = []
        for row in frame.to_dict("records"):
            # 缺失值**按字段类型**分别处理，不能一律填空串：
            # - 文本字段 → `""`（下游 `_parse_iso` / `_norm_symbol` 都容错）；
            # - 数值字段 → `nan`，**必须保住 NaN 语义** —— 若填空串，`_to_float`
            #   会把它变成 `0.0`，于是「净额缺失」被显示成「净卖出 0 万元」，
            #   正好是 P2 想修的方向性错误换个马甲回来。
            payload: dict[str, object] = {}
            for name, type_name in names:
                value = row.get(name)
                if _is_missing(value):
                    value = "" if type_name in ("str", "builtins.str") else float("nan")
                payload[name] = value
            try:
                out.append(_Item(**payload))
            except Exception:  # noqa: BLE001 - 单行坏数据跳过，不牵连整份缓存
                continue
        return out

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

    def longhubang_span(self) -> Optional[tuple[date, date]]:
        """龙虎榜数据面的实际日期覆盖区间 ``(最早, 最晚)``；无数据返回 ``None``。

        ⚠️ 这个覆盖检查是必需的：`fetch_longhubang.py` 不传日期 ⇒
        `longhubang.py:150-155` 用**单日区间** filter ⇒ 缓存通常**只有 1 天**。
        若默认 `lookback=5`，这份数据根本证明不了「近 5 日未上榜」。
        没有覆盖检查，策略就会拿「1 天的数据」下「5 天」的结论，
        并顺手消解 `NEEDS_LHB_CONFIRM` 这条人工核验项 ——
        等于**因为数据不全而降低了复核门槛**，比「没数据」更危险。
        """
        if not self._lhb:
            return None
        days = [day for bucket in self._lhb.values() for day, _ in bucket]
        return (min(days), max(days))

    def longhubang_covers(
        self, as_of: str, lookback_days: Optional[int] = None
    ) -> bool:
        """数据面是否真的覆盖 ``[as_of - lookback_days, as_of]`` 整段。

        只有为 True 时，调用方才可以下**否定结论**（「未上龙虎榜」）。
        肯定结论（真的查到记录）不需要覆盖 —— 一条真实记录本身就是事实。

        容差 2 个自然日（见 `_COVERAGE_SLACK_DAYS`），原因是窗口起点常为非交易日。
        容差**不会**放过原始缺陷：本地缓存只存 1 天时（span 起止同一天 = as_of），
        `as_of <= as_of - 5 + 2` 仍为 False ⇒ 正确判为「未核实」。
        """
        span = self.longhubang_span()
        ref = _parse_iso(as_of)
        if span is None or ref is None:
            return False
        lookback = (
            self._lhb_lookback_days
            if lookback_days is None
            else max(0, int(lookback_days))
        )
        window_start = ref - timedelta(days=lookback)
        return span[0] <= window_start + timedelta(days=_COVERAGE_SLACK_DAYS)

    def unlock_span(self) -> Optional[tuple[date, date]]:
        """解禁数据面的覆盖区间 ``(最早, 最晚)``；无数据返回 ``None``。

        用于暴露「解禁前瞻预警实际能看多远」：`lockup.py:119-126` 按 `FREE_DATE`
        **降序**取 `pageSize=500` 单页且**无 filter** ⇒ 拿到的是时间上**最远**的
        500 条。若默认 `horizon=30` 天，两者交集可能接近空 —— 此时预警
        「没有输出」并不等于「没有解禁」。调用方可据此判断结果是否可信。
        """
        if not self._unlocks:
            return None
        days = [day for bucket in self._unlocks.values() for day, _ in bucket]
        return (min(days), max(days))

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

        ⚠️ **返回 `[]` 不等于「没有解禁」** —— 也可能是数据面压根没覆盖到这段
        窗口。2026-09-22 实测：`lockup.py` 改前按 `FREE_DATE` 降序取单页，
        缓存里最早一条是 **2028-10-17**，于是 horizon=30 时**永远**返回 `[]`。
        调用方若需要区分「没事件」与「没数据」，先查 :meth:`unlock_span`。
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
            ratio = _to_float(item.ratio)
            shares = _to_float(item.lockup_shares)
            kind = item.lockup_type or "限售解禁"
            # NaN 表示「字段缺失」，不能格式化成 "nan 万股 / nan%" 这种假数据
            shares_text = "股数未知" if shares != shares else f"{shares:.0f} 万股"
            ratio_text = "占比未知" if ratio != ratio else f"{ratio:.2%}"
            detail = (
                f"预计 {days_until} 个自然日后（{day.isoformat()}）限售解禁"
                f"：{kind}，{shares_text}，占总股本 {ratio_text}"
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
            net = _to_float(item.net_amount)
            if net != net:  # NaN：净额缺失 ⇒ 方向未知，绝不能误标成「净卖出」
                strength, amount_text = "净额缺失", "净额未披露"
            else:
                strength = (
                    "大额净买入"
                    if net >= LHB_NET_AMOUNT_STRONG
                    else "净买入"
                    if net > 0
                    else "净卖出"
                )
                amount_text = f"{abs(net):.0f} 万元"
            detail = (
                f"{days_ago} 个自然日前（{day.isoformat()}）登上龙虎榜，"
                f"{strength} {amount_text}"
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
                    net_amount=net,
                    interpretation=item.interpretation,
                    detail=detail,
                )
            )
        out.sort(key=lambda ev: ev.days_ago)
        return out
