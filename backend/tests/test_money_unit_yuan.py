"""接口出口的金额单位统一为**元**（`market._sectors` / `info_stream._fetch_concepts`）。

背景（2026-11-25）：这两个源给的金额单位**都不是元**，而前端一律按元算：

- **同花顺行业资金流**（`akshare.stock_fund_flow_industry`）的流入/流出/净额是**亿元**。
  实测 2026-11-25：农产品加工 净额 `-4.71`、养殖业 `-2.77` —— 若当作元那是「-4 块钱」，
  显然不可能。
- **东财概念板块** f62 是元，但 `ConceptBoardItem.main_net_inflow` 存的是**万元**
  （`concept_board.py` 把 f62 除以 1e4；收评 `board_fund` 段按万元读同一份 CSV，
  那条契约不动）。

前端契约写得很明确：

- `frontend/src/lib/api.ts` 的 `SectorFlow.net` 注释 =「主力净额（元）」；
- `chart-options.ts` 是 `row.net / 1e8`，轴标签「净额(亿)」；
- `MarketPage` / `InfoStreamPage` 都用 `formatYi()`（金额（元）→ 亿）；
- `chart-options.test.ts::sectorValuesInYi` 断言 `12.5e8 → 12.5`。

单位混用的后果**不是「数字略偏」，而是整块渲染成假的**：

- 列表：4.71 亿的净流出 → `formatYi(-4.71)` → 「-0.0 亿」，看着像「资金基本持平」；
- 柱状图：`4.71 / 1e8 = 0.00` ⇒ **每根柱子宽度为 0**，而坐标轴还写着「净额(亿)」。

一个看起来合理的错答案比空白更糟。本文件守两件事：**出口一律元**，
且 `net / 1e8` 能还原出源里的亿元值（也就是柱子真的有宽度）。
"""

from __future__ import annotations

import pandas as pd
import pytest

import info_stream
import market
import source_health
from aqsp.data.concept_board import ConceptBoardItem, ConceptBoardSource

COLUMNS = ["行业", "行业-涨跌幅", "净额", "流入资金", "流出资金", "公司家数"]


@pytest.fixture(autouse=True)
def _reset_source_health():
    source_health.reset()
    yield
    source_health.reset()


def _patch_frame(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    frame = pd.DataFrame(rows, columns=COLUMNS)

    class _FakeAk:
        def stock_fund_flow_industry(self, symbol: str = "即时") -> pd.DataFrame:  # noqa: ARG002
            return frame

    monkeypatch.setattr(market.astock, "_akshare", lambda: _FakeAk())


# --------------------------------------------------------------- 板块资金流（亿元）


def test_sector_net_is_yuan_not_yi(monkeypatch: pytest.MonkeyPatch) -> None:
    """源里 -4.71（亿元）⇒ 出口 -4.71e8（元）。"""
    _patch_frame(
        monkeypatch,
        [{"行业": "农产品加工", "行业-涨跌幅": 0.64, "净额": -4.71,
          "流入资金": 18.27, "流出资金": 22.98, "公司家数": 43}],
    )
    sectors = market._sectors()
    assert sectors[0]["net"] == pytest.approx(-4.71e8)
    assert sectors[0]["inflow"] == pytest.approx(18.27e8)
    assert sectors[0]["outflow"] == pytest.approx(22.98e8)


def test_sector_chart_bar_has_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 回归守卫：`net / 1e8` 必须还原出源里的亿元值，否则柱子宽度为 0。

    前端 `chart-options.ts` 就是 `row.net / 1e8` 再 `toFixed(2)`；单位错了这里会得 0.00。
    """
    _patch_frame(
        monkeypatch,
        [{"行业": "农产品加工", "行业-涨跌幅": 0.64, "净额": -4.71,
          "流入资金": 18.27, "流出资金": 22.98, "公司家数": 43}],
    )
    net = market._sectors()[0]["net"]
    assert round(net / 1e8, 2) == -4.71
    # `formatYi` 的口径（元 → 亿，1 位小数）也不该再是「0.0 亿」
    assert f"{net / 1e8:.1f} 亿" == "-4.7 亿"


def test_sector_sort_order_survives_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """换算不改变排序：净额仍是降序（乘以正常数不会翻转大小关系）。"""
    _patch_frame(
        monkeypatch,
        [
            {"行业": "小额", "行业-涨跌幅": 1.0, "净额": 0.95,
             "流入资金": 3.0, "流出资金": 2.05, "公司家数": 10},
            {"行业": "大额", "行业-涨跌幅": 2.0, "净额": 12.3,
             "流入资金": 30.0, "流出资金": 17.7, "公司家数": 20},
            {"行业": "流出", "行业-涨跌幅": -1.0, "净额": -8.4,
             "流入资金": 4.0, "流出资金": 12.4, "公司家数": 30},
        ],
    )
    assert [row["name"] for row in market._sectors()] == ["大额", "小额", "流出"]


def test_sector_unknown_still_none_after_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """换算不能把「未知」洗成 0：NaN 仍走 `None`。"""
    _patch_frame(
        monkeypatch,
        [{"行业": "半导体", "行业-涨跌幅": float("nan"), "净额": float("nan"),
          "流入资金": float("nan"), "流出资金": float("nan"), "公司家数": 10}],
    )
    # 净额不可用 ⇒ 整行不进榜（它是排序键）
    assert market._sectors() == []


# --------------------------------------------------------------- 概念板块（万元）


def _concept_source(items: list[ConceptBoardItem]) -> ConceptBoardSource:
    return ConceptBoardSource().from_items(items)


def _concept_item(inflow_wan: float) -> ConceptBoardItem:
    return ConceptBoardItem(
        board_code="BK0933",
        board_name="退税商店",
        up_count=19,
        down_count=1,
        change_pct=3.57,
        main_net_inflow=inflow_wan,  # 万元（见 concept_board 的 f62 元→万元）
        main_net_ratio=7.75,
    )


def test_concept_main_net_inflow_is_yuan() -> None:
    """`main_net_inflow` 源是万元 ⇒ 出口元（×1e4）。"""
    # 东财 f62 实测 271122208 元 ⇒ 内部 27112.2208 万元
    concepts, _, status = info_stream._fetch_concepts(_concept_source([_concept_item(27112.2208)]))
    assert status == "ok"
    assert len(concepts) == 1
    assert concepts[0]["main_net_inflow"] == pytest.approx(271122208.0)
    assert concepts[0]["main_net_inflow"] / 1e8 == pytest.approx(2.71122208)


def test_concept_chart_scale_is_not_rounded_to_zero() -> None:
    """🔴 回归守卫：`formatYi` 必须给出「2.7 亿」而不是「0.0 亿」。"""
    concepts, _, _ = info_stream._fetch_concepts(_concept_source([_concept_item(27112.2208)]))
    assert f"{concepts[0]['main_net_inflow'] / 1e8:.1f} 亿" == "2.7 亿"


def test_concept_ratio_is_percent_and_not_scaled() -> None:
    """净占比是百分数（7.75 = 7.75%），**不能**跟着金额一起乘 1e4。"""
    concepts, _, _ = info_stream._fetch_concepts(_concept_source([_concept_item(27112.2208)]))
    assert concepts[0]["main_net_ratio"] == pytest.approx(7.75)


def test_concept_negative_inflow_keeps_sign() -> None:
    """净流出方向不能被换算抹掉。"""
    concepts, _, _ = info_stream._fetch_concepts(_concept_source([_concept_item(-850.0)]))
    assert concepts[0]["main_net_inflow"] == pytest.approx(-8_500_000.0)
