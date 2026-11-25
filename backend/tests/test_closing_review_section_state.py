"""收评段「为什么没数据」的状态契约（fail-soft 的诚实化）。

背景：6 个 `build_*_section()` 原来在**四种完全不同的情况**下一律 `return ""`：
产物文件不存在 / 文件在但读失败 / 表头残缺 / 读到了但没有可用行。端点再把它折成
`available: false`，前端只能猜一句「盘后预载未落盘或数据源缺缓存」——于是「读失败」
被写成「还没生成」，把用户引去等下一次调度，而真正的原因是文件坏了 / 权限不对 / 磁盘满。

本文件守的是：**这四态必须可分辨，且每一种都必须带上具体原因**。
"""

from __future__ import annotations

import json

import pytest

import aqsp.briefing.closing_review as cr
from aqsp.core.time import today_shanghai

# 每种段：header + 一行合法数据（走到 ok）+ 一行坏数据（走到 empty）+ 一个错表头
CSV_CASES: dict[str, dict[str, str]] = {
    "board_fund": {
        "builder": "build_board_fund_section",
        "header": "board_code,board_name,main_net_inflow\n",
        "good": "BK0475,半导体,12345.0\n",
        "bad_row": ",,,\n",
    },
    "longhubang": {
        "builder": "build_longhubang_section",
        "header": "symbol,name,net_amount\n",
        "good": "600000,浦发银行,1000.0\n",
        "bad_row": ",,,\n",
    },
    "news": {
        "builder": "build_news_section",
        "header": "title,ctime\n",
        "good": "测试快讯,2026-11-25 10:00\n",
        "bad_row": ",\n",
    },
    "announcements": {
        "builder": "build_announcements_section",
        "header": "symbol,name,notice_date,text\n",
        "good": f"600000,浦发银行,{today_shanghai().isoformat()},公司公告正文\n",
        "bad_row": ",,,\n",
    },
    "holder_concentration": {
        "builder": "build_holder_concentration_section",
        "header": "symbol,quarter,holder_count\n",
        "good": "600000,2026-09-30,10000\n600000,2026-06-30,12000\n",
        "bad_row": ",,,\n",
    },
}

SECTION_KEYS = ["factor_ic", *CSV_CASES.keys()]


def _build(name: str, path: str, diagnostics: dict[str, str] | None = None) -> str:
    builder = getattr(cr, CSV_CASES[name]["builder"]) if name in CSV_CASES else cr.build_factor_ic_section
    if diagnostics is None:
        return builder(path)
    return builder(path, diagnostics=diagnostics)


def _diag(name: str, path) -> tuple[str, dict[str, str]]:
    diag: dict[str, str] = {}
    md = _build(name, str(path), diag)
    return md, diag


# --------------------------------------------------------------- 四态逐个验证


def test_missing_artifact_is_reported_as_missing_not_failure(tmp_path) -> None:
    """文件不存在 ⇒ `missing`（调度未回流），不是 `unreadable`，且原因里带路径。"""
    for name in SECTION_KEYS:
        path = tmp_path / f"{name}.csv"
        md, diag = _diag(name, path)
        assert md == ""
        assert diag["state"] == cr.SECTION_MISSING, name
        assert "未落盘" in diag["reason"], name
        assert str(path) in diag["reason"], name


def test_unreadable_artifact_is_reported_as_failure_not_missing(tmp_path) -> None:
    """文件在但读不了 ⇒ `unreadable`。这一态原来被写成「还没生成」，是本次要修的核心。"""
    for name in SECTION_KEYS:
        path = tmp_path / f"{name}.csv"
        if name == "factor_ic":
            path.write_text("{ not json", encoding="utf-8")
        else:
            path.write_bytes(b"\xff\xfe\x00\x00 bad utf-8")
        md, diag = _diag(name, path)
        assert md == ""
        assert diag["state"] == cr.SECTION_UNREADABLE, name
        assert "读取失败" in diag["reason"], name
        # 与 missing 必须能分辨：同一条链路下两句话不能长一样。
        assert diag["reason"] != f"产物未落盘：{path}", name


def test_malformed_header_is_reported_as_malformed(tmp_path) -> None:
    """表头残缺 ⇒ `malformed`（产物坏了），既不是「没落盘」也不是「没内容」。"""
    for name, case in CSV_CASES.items():
        path = tmp_path / f"{name}.csv"
        path.write_text("foo,bar\n1,2\n", encoding="utf-8")
        md, diag = _diag(name, path)
        assert md == ""
        assert diag["state"] == cr.SECTION_MALFORMED, name
        assert "表头残缺" in diag["reason"], name
        assert case["header"].split(",")[0] in diag["reason"], name


def test_header_ok_but_no_usable_rows_is_reported_as_empty(tmp_path) -> None:
    """表头对、但一行都用不上 ⇒ `empty`（确实没内容），不能与「产物坏了」混同。"""
    for name, case in CSV_CASES.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(case["header"] + case["bad_row"], encoding="utf-8")
        md, diag = _diag(name, path)
        assert md == ""
        assert diag["state"] == cr.SECTION_EMPTY, name
        assert diag["reason"], name


def test_factor_ic_without_factors_is_empty_not_failure(tmp_path) -> None:
    """factor_ic 的 `empty` 分支：JSON 合法但没有 factors 段。"""
    path = tmp_path / "factor_ic.json"
    path.write_text(json.dumps({"as_of": "2026-11-25", "factors": {}}), encoding="utf-8")
    md, diag = _diag("factor_ic", path)
    assert md == ""
    assert diag["state"] == cr.SECTION_EMPTY
    assert "factors" in diag["reason"]


def test_success_sets_ok_and_empty_reason(tmp_path) -> None:
    """读到可用数据 ⇒ `ok` + 空原因（`reason` 非空只用来解释「为什么没有」）。"""
    for name, case in CSV_CASES.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(case["header"] + case["good"], encoding="utf-8")
        md, diag = _diag(name, path)
        assert md.strip(), name
        assert diag["state"] == cr.SECTION_OK, name
        assert diag["reason"] == "", name

    path = tmp_path / "factor_ic.json"
    path.write_text(
        json.dumps({"as_of": "2026-11-25", "factors": {"momentum": {"mean": 0.01, "t": 2.5}}}),
        encoding="utf-8",
    )
    md, diag = _diag("factor_ic", path)
    assert md.strip()
    assert diag["state"] == cr.SECTION_OK
    assert diag["reason"] == ""


# --------------------------------------------------------------- 横切守卫


def test_every_non_ok_state_carries_a_reason(tmp_path) -> None:
    """遍历全部「没有数据」的分支：**每一个都必须有非空原因**。

    这是本 PR 的核心不变量 —— 状态码只区分类型，原因才是用户据以行动的东西。
    """
    reasons: dict[str, str] = {}
    for name in SECTION_KEYS:
        probes = {
            "missing": lambda n=name: (tmp_path / f"{n}-absent.csv"),
        }
        # missing
        _, diag = _diag(name, probes["missing"]())
        assert diag["state"] != cr.SECTION_OK
        assert diag["reason"].strip(), f"{name} missing 缺原因"
        reasons[f"{name}:missing"] = diag["reason"]
        # unreadable
        bad = tmp_path / f"{name}-bad.csv"
        bad.write_bytes(b"\xff\xfe\x00\x00")
        _, diag = _diag(name, bad)
        assert diag["reason"].strip(), f"{name} unreadable 缺原因"
        reasons[f"{name}:unreadable"] = diag["reason"]

    # 同一段的两种「没数据」必须说不同的话，否则状态码对用户不可见。
    for name in SECTION_KEYS:
        assert reasons[f"{name}:missing"] != reasons[f"{name}:unreadable"], name


def test_diagnostics_is_optional_and_side_effect_free(tmp_path) -> None:
    """不传 `diagnostics` 时行为完全不变（返回值逐字相同）。

    这 6 个 builder 的返回值被日报渲染与上百处测试当 `str` 消费，
    诊断必须走 out-param，不能动返回类型。
    """
    for name, case in CSV_CASES.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(case["header"] + case["good"], encoding="utf-8")
        plain = _build(name, str(path))
        diag: dict[str, str] = {}
        with_diag = _build(name, str(path), diag)
        assert plain == with_diag, name
        assert diag["state"] == cr.SECTION_OK, name

    absent = tmp_path / "absent.csv"
    assert _build("news", str(absent)) == ""


@pytest.mark.parametrize("state", [
    cr.SECTION_MISSING,
    cr.SECTION_UNREADABLE,
    cr.SECTION_MALFORMED,
    cr.SECTION_EMPTY,
])
def test_states_are_distinct_strings(state: str) -> None:
    """五个状态码必须两两不同 —— 否则前端没法分档。"""
    all_states = {
        cr.SECTION_OK,
        cr.SECTION_MISSING,
        cr.SECTION_UNREADABLE,
        cr.SECTION_MALFORMED,
        cr.SECTION_EMPTY,
    }
    assert len(all_states) == 5
    assert state in all_states
