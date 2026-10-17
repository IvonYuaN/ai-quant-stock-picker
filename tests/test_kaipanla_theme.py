"""`aqsp.data.kaipanla_theme` 单测：开盘啦轮动/龙榜解析契约 + 独立表存储。

守卫重点（#398 实测契约，全部进 fixture）：
- 🔴 **code/name 在 td 标签属性上不在内容**——只取内容会静默 0 条记录；
- 🔴 **「当日无领涨」占位缺 `</td>`（实为 `</div>`）**——按 `</td>` 切列会把
  占位列与次日列合并（行数对但日期错位）；
- 龙榜响应**无日期列**，列 i ↔ 轮动日期列 i 按位置对齐，列数不符即 DataError；
- 全是占位 = 无数据（不是「今天没龙头」），不抛错不写空行；
- 独立表幂等：同交易日旧行先删再写、原子写不留 .tmp、默认落点运行时 pit_cache。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.kaipanla_theme import (
    LEADERS_COLUMNS,
    LEADERS_FILENAME,
    ROTATION_COLUMNS,
    ROTATION_FILENAME,
    KaipanlaThemeSource,
    LeaderRecord,
    RotationRecord,
    append_leaders_records,
    append_rotation_records,
    load_leaders,
    load_rotation,
    parse_leaders_payload,
    parse_rotation_payload,
)

# ---------------------------------------------------------------------------
# fixture：2026-10-17 runner 实测响应整形（保留畸形结构）
# ---------------------------------------------------------------------------

#: 轮动：2 个排名槽 × 2 个日期（最新在前）。注意 td 内容形如「低空经济…15400」，
#: code/name 只在标签属性上。
ROTATION_HTML = (
    "<tr><td style='width:50px;vertical-align: middle;'>排名</td>"
    "<td style='vertical-align: middle;line-height:160%;'>2026-10-16</td>"
    "<td style='vertical-align: middle;line-height:160%;'>2026-10-15</td></tr>"
    "<tr><td style='vertical-align: middle;'><span class='rank' "
    "style='color:white;background:#FF2237;'>1</span></td>"
    "<td class='plate plate801829' code='801829' name='低空经济' "
    "style='background:rgba(1,184,199,0.07)'><span style='white-space: nowrap;'>低空经济</span><br>"
    "<span style='font-size:12px;'><span style='color:red;'>15400</span></span></td>"
    "<td class='plate plate801660' code='801660' name='通信' style=''><span "
    "style='white-space: nowrap;'>通信</span><br><span style='font-size:12px;'>"
    "<span style='color:red;'>3125</span></span></td></tr>"
    "<tr><td style='vertical-align: middle;'><span class='rank' "
    "style='color:white;background:#FF2237;'>2</span></td>"
    "<td class='plate plate801062' code='801062' name='军工' style=''><span "
    "style='white-space: nowrap;'>军工</span><br><span style='font-size:12px;'>"
    "<span style='color:red;'>9601</span></span></td>"
    "<td class='plate plate801250' code='801250' name='并购重组' "
    "style='background:rgba(252,38,50,0.2)'><span style='white-space: nowrap;'>并购重组</span><br>"
    "<span style='font-size:12px;'><span style='color:red;'>1833</span></span></td></tr>"
)

ROTATION_PAYLOAD = {"first": "801829", "html": ROTATION_HTML}


#: 龙榜：表头 + 2 个日列（最新在前）。**首列是「当日无领涨」占位且缺 `</td>`**
#: （实际 `</div>`）——按 `</td>` 切列会把两列合并。
LEADERS_HTML = (
    "<td style='width:50px;vertical-align: middle;height:99px;'>"
    "<b style='box-shadow: 0 1px 6px rgba(0, 0, 0, .06);font-weight:normal;"
    "padding:2px 3px;border:1px solid #f5f5f5;'>领涨</b></td>"
    "<td style='text-align:center;color:#bbb;vertical-align: middle;font-size:90%:'>"
    "当日无领涨</div>"
    "<td style='text-align:left;padding-bottom:5px;'>"
    "<div class='kline' code='605058'><span>龙一</span>澳弘电子</div>"
    "<div class='kline' code='603386'><span>龙二</span>骏亚科技</div>"
    "</td>"
)

LEADERS_DATES = ["2026-10-16", "2026-10-15"]
LEADERS_PAYLOAD = {"html": LEADERS_HTML}


# ---------------------------------------------------------------------------
# 轮动解析
# ---------------------------------------------------------------------------


def test_rotation_parses_records_with_dates_newest_first() -> None:
    dates, records, stats = parse_rotation_payload(ROTATION_PAYLOAD)

    assert dates == ["2026-10-16", "2026-10-15"]
    assert len(records) == 4
    assert stats.anomalies == 0
    first = records[0]
    assert (first.trade_date, first.board_code, first.board_name, first.rank, first.heat) == (
        "2026-10-16",
        "801829",
        "低空经济",
        1,
        15400,
    )


def test_rotation_reads_code_name_from_tag_attributes_not_content() -> None:
    """🔴 契约陷阱：td 内容是「低空经济…15400」无分隔符，code/name 必须来自标签属性。"""
    _dates, records, _stats = parse_rotation_payload(ROTATION_PAYLOAD)

    codes = {r.board_code for r in records}
    names = {r.board_name for r in records}
    assert codes == {"801829", "801660", "801062", "801250"}
    # 名字里绝不能混进热度数字（若按内容切分，「低空经济15400」会被当成板块名）
    assert "低空经济15400" not in names
    assert all(r.heat > 0 for r in records)


def test_rotation_skips_record_missing_code_attribute() -> None:
    html = ROTATION_HTML.replace("code='801062' ", "")
    payload = {"first": "", "html": html}

    _dates, records, stats = parse_rotation_payload(payload)

    assert len(records) == 3  # 军工那条被跳过
    assert stats.anomalies == 1


def test_rotation_skips_record_without_numeric_heat() -> None:
    html = ROTATION_HTML.replace(
        "<span style='font-size:12px;'><span style='color:red;'>9601</span></span>",
        "<span style='font-size:12px;'><span style='color:red;'>--</span></span>",
    )
    payload = {"first": "", "html": html}

    _dates, records, stats = parse_rotation_payload(payload)

    assert len(records) == 3
    assert stats.anomalies == 1


def test_rotation_raises_without_date_header() -> None:
    with pytest.raises(DataError, match="无日期列表头"):
        parse_rotation_payload({"first": "", "html": "<tr><td>排名</td></tr>"})


def test_rotation_raises_without_any_valid_record() -> None:
    html = (
        "<tr><td>排名</td><td>2026-10-16</td></tr>"
        "<tr><td><span class='rank'>1</span></td><td class='plate' name='X'>无热度</td></tr>"
    )
    with pytest.raises(DataError, match="无任何有效板块记录"):
        parse_rotation_payload({"first": "", "html": html})


def test_rotation_raises_on_non_json() -> None:
    with pytest.raises(DataError, match="不是合法 JSON"):
        parse_rotation_payload("not-json")


def test_rotation_raises_on_non_object() -> None:
    with pytest.raises(DataError, match="不是对象"):
        parse_rotation_payload([1, 2, 3])


def test_rotation_raises_on_empty_html() -> None:
    with pytest.raises(DataError, match="html 为空"):
        parse_rotation_payload({"first": "", "html": ""})


def test_rotation_skips_row_with_mismatched_tag_content_counts() -> None:
    """tr 内 td 标签/内容数不匹配 ⇒ 整行跳过（不猜列），有效行照常保留。

    注意：若**所有**行都被跳过 ⇒ 整批 DataError（fail-closed，见下一个测试）。
    """
    broken = (
        "<tr><td>排名</td><td>2026-10-16</td></tr>"
        "<tr><td><span class='rank'>1</span></td>"
        "<td class='plate' code='801829' name='低空经济'><span>低空经济</span>"
        "<br><span style='font-size:12px;'><span>15400</span></span></td>"
        "<td class='plate' code='801660' name='通信'><span>通信</span>"
        "<br><span style='font-size:12px;'><span>3125</span></span></tr>"
        "<tr><td><span class='rank'>2</span></td>"
        "<td class='plate' code='801062' name='军工'><span>军工</span>"
        "<br><span style='font-size:12px;'><span>9601</span></span></td></tr>"
    )
    payload = {"first": "", "html": broken}

    _dates, records, stats = parse_rotation_payload(payload)

    assert len(records) == 1  # 只有第二行（军工）有效
    assert records[0].board_code == "801062"
    assert stats.anomalies == 1
    assert "td 标签/内容数不匹配" in stats.detail[0]


def test_rotation_accepts_compact_and_ymd_dash_forms() -> None:
    """日期列带连字符（API 原样）；存储层另行支持 YYYYMMDD。"""
    _dates, records, _stats = parse_rotation_payload(ROTATION_PAYLOAD)
    assert {r.trade_date for r in records} == {"2026-10-16", "2026-10-15"}


def test_rotation_parses_negative_heat_as_signed() -> None:
    """🔴 热度带符号（实测负值绿色渲染，如 -25/-46/-92）：正则必须匹配负号，
    否则负热度记录被静默丢弃（分析脚本曾因此只覆盖正值）。"""
    html = (
        "<tr><td>排名</td><td>2026-10-15</td></tr>"
        "<tr><td><span class='rank'>1</span></td>"
        "<td class='plate' code='801031' name='文化传媒'><span>文化传媒</span>"
        "<br><span style='font-size:12px;'><span style='color:green;'>-25</span></span></td></tr>"
        "<tr><td><span class='rank'>2</span></td>"
        "<td class='plate' code='801071' name='保险'><span>保险</span>"
        "<br><span style='font-size:12px;'><span style='color:green;'>-92</span></span></td></tr>"
    )

    _dates, records, stats = parse_rotation_payload({"first": "", "html": html})

    assert len(records) == 2
    assert [r.heat for r in records] == [-25, -92]
    assert stats.anomalies == 0


def test_rotation_skips_empty_layout_rows_silently() -> None:
    """表格尾部的空 <tr id='long'> 是布局行：静默跳过，不算 anomaly。"""
    html = ROTATION_HTML + "<tr id='long' class='kaipan'></tr>"

    _dates, records, stats = parse_rotation_payload({"first": "", "html": html})

    assert len(records) == 4
    assert stats.anomalies == 0


# ---------------------------------------------------------------------------
# 龙榜解析
# ---------------------------------------------------------------------------


def test_leaders_aligns_columns_by_position_and_skips_placeholder() -> None:
    """首列占位=无数据；次列对齐第二个日期。🔴 缺 </td> 的占位不能串到次日列。"""
    records, stats = parse_leaders_payload(
        LEADERS_PAYLOAD, board_code="801660", dates=LEADERS_DATES
    )

    assert len(records) == 2
    assert stats.anomalies == 0
    assert {r.trade_date for r in records} == {"2026-10-15"}  # 10-16 是占位日
    assert records[0].stock_code == "605058"
    assert records[0].stock_name == "澳弘电子"
    assert records[0].leader_rank == 1
    assert records[1].leader_rank == 2


def test_leaders_placeholder_only_returns_empty_without_error() -> None:
    """全是「当日无领涨」= 无数据：不抛错、不写空行。"""
    payload = {"html": "<td>领涨</td><td style='font-size:90%:'>当日无领涨</div>"}

    records, stats = parse_leaders_payload(payload, board_code="801660", dates=["2026-10-16"])

    assert records == []
    assert stats.anomalies == 0


def test_leaders_raises_when_column_count_mismatches_dates() -> None:
    with pytest.raises(DataError, match="对齐假设被破坏"):
        parse_leaders_payload(LEADERS_PAYLOAD, board_code="801660", dates=LEADERS_DATES + ["2026-10-14"])


def test_leaders_raises_on_non_json() -> None:
    with pytest.raises(DataError, match="不是合法 JSON"):
        parse_leaders_payload("<html>", board_code="801660", dates=LEADERS_DATES)


def test_leaders_skips_unparseable_rank() -> None:
    payload = {
        "html": (
            "<td>领涨</td>"
            "<td style='text-align:left;'><div class='kline' code='600000'><span>龙六</span>测试股</div>"
            "<div class='kline' code='600001'><span>龙一</span>龙头股</div></td>"
        )
    }

    records, stats = parse_leaders_payload(payload, board_code="801660", dates=["2026-10-16"])

    assert len(records) == 1
    assert records[0].stock_code == "600001"
    assert stats.anomalies == 1


def test_leaders_raises_without_any_column() -> None:
    with pytest.raises(DataError, match="html 为空"):
        parse_leaders_payload({"html": ""}, board_code="801660", dates=["2026-10-16"])


def test_leaders_raises_when_no_column_after_header() -> None:
    with pytest.raises(DataError, match="无日列"):
        parse_leaders_payload({"html": "<td>领涨</td>"}, board_code="801660", dates=["2026-10-16"])


# ---------------------------------------------------------------------------
# 存储：轮动表
# ---------------------------------------------------------------------------


def _rotation_records() -> list[RotationRecord]:
    return [
        RotationRecord("2026-10-16", "801829", "低空经济", 1, 15400),
        RotationRecord("2026-10-16", "801062", "军工", 2, 9601),
        RotationRecord("2026-10-15", "801660", "通信", 1, 3125),
    ]


def test_rotation_append_creates_table_with_columns(tmp_path: Path) -> None:
    target = tmp_path / ROTATION_FILENAME

    written = append_rotation_records(_rotation_records(), path=target)

    assert written == 3
    frame = pd.read_csv(target)
    assert tuple(frame.columns) == ROTATION_COLUMNS


def test_rotation_append_is_idempotent_for_same_date(tmp_path: Path) -> None:
    target = tmp_path / ROTATION_FILENAME

    append_rotation_records(_rotation_records(), path=target)
    append_rotation_records(_rotation_records(), path=target)

    frame = pd.read_csv(target)
    assert len(frame) == 3  # 10-16 两行被替换而非叠加
    assert frame["trade_date"].value_counts().to_dict() == {"2026-10-16": 2, "2026-10-15": 1}


def test_rotation_append_replaces_corrected_values(tmp_path: Path) -> None:
    target = tmp_path / ROTATION_FILENAME
    bad = _rotation_records()
    bad[0] = RotationRecord("2026-10-16", "801829", "低空经济", 1, 999)
    append_rotation_records(_rotation_records(), path=target)
    append_rotation_records(bad, path=target)

    frame = pd.read_csv(target)
    row = frame[(frame["trade_date"] == "2026-10-16") & (frame["board_code"].astype(str) == "801829")]
    assert len(row) == 1
    assert int(row.iloc[0]["heat"]) == 999


def test_rotation_append_sorts_by_date_then_rank(tmp_path: Path) -> None:
    target = tmp_path / ROTATION_FILENAME

    append_rotation_records(_rotation_records(), path=target)

    frame = pd.read_csv(target)
    assert frame["trade_date"].tolist() == ["2026-10-15", "2026-10-16", "2026-10-16"]
    assert frame["rank"].tolist() == [1, 1, 2]


def test_rotation_append_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="为空"):
        append_rotation_records([], path=tmp_path / ROTATION_FILENAME)


def test_rotation_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / ROTATION_FILENAME

    append_rotation_records(_rotation_records(), path=target)

    assert sorted(p.name for p in tmp_path.iterdir()) == [ROTATION_FILENAME]


def test_rotation_load_missing_returns_empty_frame(tmp_path: Path) -> None:
    frame = load_rotation(path=tmp_path / "nope.csv")

    assert frame.empty
    assert tuple(frame.columns) == ROTATION_COLUMNS


def test_rotation_load_raises_when_columns_missing(tmp_path: Path) -> None:
    target = tmp_path / ROTATION_FILENAME
    target.write_text("trade_date,board_code\n2026-10-16,801829\n", encoding="utf-8")

    with pytest.raises(DataError, match="缺列"):
        load_rotation(path=target)


def test_default_paths_live_under_runtime_pit_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(runtime_root))

    import aqsp.data.kaipanla_theme as mod

    rotation_path = Path(mod._rotation_cache_path()).resolve()
    leaders_path = Path(mod._leaders_cache_path()).resolve()

    assert rotation_path.parent == (runtime_root / "pit_cache").resolve()
    assert rotation_path.name == ROTATION_FILENAME
    assert leaders_path.name == LEADERS_FILENAME
    assert "src" not in rotation_path.parts


def test_default_path_follows_env_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aqsp.data.kaipanla_theme as mod

    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path / "first"))
    assert Path(mod._rotation_cache_path()).parent == (tmp_path / "first" / "pit_cache")

    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path / "second"))
    assert Path(mod._leaders_cache_path()).parent == (tmp_path / "second" / "pit_cache")


# ---------------------------------------------------------------------------
# 存储：龙榜表
# ---------------------------------------------------------------------------


def _leader_records(board_code: str = "801660") -> list[LeaderRecord]:
    return [
        LeaderRecord("2026-10-15", board_code, "", 1, "605058", "澳弘电子"),
        LeaderRecord("2026-10-15", board_code, "", 2, "603386", "骏亚科技"),
    ]


def test_leaders_append_creates_table_with_board_name(tmp_path: Path) -> None:
    target = tmp_path / LEADERS_FILENAME

    written = append_leaders_records(_leader_records(), board_name="通信", path=target)

    assert written == 2
    frame = pd.read_csv(target)
    assert tuple(frame.columns) == LEADERS_COLUMNS
    assert frame["board_name"].unique().tolist() == ["通信"]


def test_leaders_append_idempotent_per_board_per_date(tmp_path: Path) -> None:
    target = tmp_path / LEADERS_FILENAME

    append_leaders_records(_leader_records(), board_name="通信", path=target)
    append_leaders_records(_leader_records("801062"), board_name="军工", path=target)
    append_leaders_records(_leader_records(), board_name="通信", path=target)

    frame = pd.read_csv(target)
    assert len(frame) == 4  # 通信 2 行被替换，军工 2 行保留
    assert set(frame["board_code"].astype(str)) == {"801660", "801062"}


def test_leaders_append_empty_returns_zero(tmp_path: Path) -> None:
    assert append_leaders_records([], path=tmp_path / LEADERS_FILENAME) == 0


def test_leaders_load_missing_returns_empty_frame(tmp_path: Path) -> None:
    frame = load_leaders(path=tmp_path / "nope.csv")

    assert frame.empty
    assert tuple(frame.columns) == LEADERS_COLUMNS


# ---------------------------------------------------------------------------
# 源层装配（_post 打桩，不触网）
# ---------------------------------------------------------------------------


def test_source_load_writes_both_tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_post(self: KaipanlaThemeSource, url: str, data: dict[str, str]) -> object:
        calls.append((url, data))
        if url.endswith("getPlateRotatData"):
            return ROTATION_PAYLOAD
        return LEADERS_PAYLOAD

    monkeypatch.setattr(KaipanlaThemeSource, "_post", fake_post)
    source = KaipanlaThemeSource(
        leader_interval=0,
        rotation_path=tmp_path / ROTATION_FILENAME,
        leaders_path=tmp_path / LEADERS_FILENAME,
    )

    written = source.load()

    assert written["rotation"] == 4
    # 轮动里 4 个板块码（低空经济/通信/军工/并购重组）各抓一次龙榜
    assert written["leaders"] == 8  # 4 板块 × 2 条（10-16 占位日无记录）
    rotation_calls = [c for c in calls if c[0].endswith("getPlateRotatData")]
    leaders_calls = [c for c in calls if c[0].endswith("getLongByPlate")]
    assert len(rotation_calls) == 1
    assert len(leaders_calls) == 4
    frame = pd.read_csv(tmp_path / LEADERS_FILENAME)
    assert set(frame["board_name"]) == {"低空经济", "通信", "军工", "并购重组"}


def test_source_load_creates_empty_leaders_table_when_all_placeholder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """全占位日（无任何龙头）也必须创建龙榜表文件——否则 relay rsync 源文件不存在会失败。"""

    def fake_post(self: KaipanlaThemeSource, url: str, data: dict[str, str]) -> object:
        if url.endswith("getPlateRotatData"):
            return ROTATION_PAYLOAD
        # 两个日列全是「当日无领涨」占位（列数须与轮动日期数一致）
        return {
            "html": (
                "<td>领涨</td>"
                "<td style='font-size:90%:'>当日无领涨</div>"
                "<td style='font-size:90%:'>当日无领涨</div>"
            )
        }

    monkeypatch.setattr(KaipanlaThemeSource, "_post", fake_post)
    source = KaipanlaThemeSource(
        leader_interval=0,
        rotation_path=tmp_path / ROTATION_FILENAME,
        leaders_path=tmp_path / LEADERS_FILENAME,
    )

    written = source.load()

    assert written["leaders"] == 0
    assert (tmp_path / LEADERS_FILENAME).exists()
    frame = pd.read_csv(tmp_path / LEADERS_FILENAME)
    assert frame.empty
    assert tuple(frame.columns) == LEADERS_COLUMNS


def test_source_stats_track_anomalies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_post(self: KaipanlaThemeSource, url: str, data: dict[str, str]) -> object:
        if url.endswith("getPlateRotatData"):
            return ROTATION_PAYLOAD
        return LEADERS_PAYLOAD

    monkeypatch.setattr(KaipanlaThemeSource, "_post", fake_post)
    source = KaipanlaThemeSource(
        leader_interval=0,
        rotation_path=tmp_path / ROTATION_FILENAME,
        leaders_path=tmp_path / LEADERS_FILENAME,
    )
    source.load()

    assert source.stats["rotation"].total == 4
    assert source.stats["rotation"].anomalies == 0
    assert source.stats["leaders:801660"].total == 2
