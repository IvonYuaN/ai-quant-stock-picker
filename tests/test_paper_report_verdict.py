"""影子账户 HTML 报告的「判决列」守卫。

🔴 背景：老大 11-24 说「P1 P2 P3 P4 P5 P6 我都不知道什么逻辑」。
#598 修的是**推送卡片**（每行印模式名 + 判决），但 `paper_report.py` 生成的
HTML 报告**从来没印过判决** —— 那张表只有「策略 / 假设」一列文字。

于是报告上 P1 那行长这样：

    二板接力
    唯一在 3y 回测过门的假设，本账本做前向验证……
    超额均收益 —   超额胜率 —   t —   q —   统计进度 待积累

一份把**已否证**策略写成「唯一过门」、所有数字列都是 `—` 的报告，比没有报告更糟：
读者会把「—」理解成「还没跑出来」，而真实结论是「已经跑出来了，是负的」。

| 自欺方式                                | 对应测试                                      |
|----------------------------------------|----------------------------------------------|
| 只印假设、不印判决                        | test_report_table_shows_verdict_column        |
| 判决词自己另写一套（与卡片不一致）            | test_verdict_words_come_from_the_single_source |
| 未知 sid 直接 KeyError，整张报告崩掉         | test_unknown_sleeve_does_not_crash            |
| 颜色把「否证」显示成绿色（看着像好消息）        | test_rejected_is_rendered_in_red_class        |
"""
from __future__ import annotations

import sys
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import paper_ledger as pl  # noqa: E402
import paper_report as pr  # noqa: E402
from mode_card import EVIDENCE_TAG  # noqa: E402


def test_verdict_words_come_from_the_single_source():
    """判决词只能来自 `mode_card.EVIDENCE_TAG` —— 卡片和报告必须是同一套说法。"""
    for sid in ("P1", "P2", "P3", "P4", "P5", "P6"):
        state, tag, _why = pr.verdict_of(sid)
        assert tag == EVIDENCE_TAG[state]


def test_unknown_sleeve_does_not_crash():
    """账本里出现注册表没有的 sid 时，报告不能整张崩掉（降级成「未过门」）。"""
    state, tag, why = pr.verdict_of("PX", specs={})
    assert state == "unverified"
    assert tag == "⏳未过门"
    assert why == "（未声明证据）"


def test_rejected_is_rendered_in_red_class():
    """「已否证」必须用红色（页面已有的 .down），不能是绿色或灰色。"""
    html = pr._verdict_html("P1")
    assert "class='down'" in html
    assert "❌已否证" in html
    assert "买点不可成交" in html


def test_report_table_shows_verdict_column(tmp_path, monkeypatch):
    """端到端：报告表格里必须同时有「假设」和「判决」两列内容。

    用空账本渲染（六个 sleeve 全 0 笔）—— 这正是最容易骗人的状态：
    所有数字列都是 `—`，唯一能说明方向的就是判决。
    """
    monkeypatch.setattr(pl, "LEDGER", tmp_path / "arena.json")
    pl.save(pl._blank())
    html = pr.render()

    assert "判决（已做的证据）" in html, "报告表格少了一列判决"
    # 五个已否证 + 一个未验证
    assert html.count("❌已否证") == 5
    assert html.count("⏳未过门") == 1
    # 假设文字仍然保留（判决不替代假设，两者都要）
    assert "情绪接力" in html


# ---------------- 否证敞口 ----------------
#
# 🔴 报告以前只有两样东西：每行的判决、底下的总额度。**两者不交叉**，
# 于是有一个事实从来没被印出来过 —— 钱有多少压在已否证的策略上。
# 这一组守的就是那个交叉。

def _ov(**kw):
    """构造 overview 的最小子集：只要 sleeves[sid].exposure。"""
    base = {sid: {"exposure": 0.0} for sid in ("P1", "P2", "P3", "P4", "P5", "P6")}
    for sid, e in kw.items():
        base[sid]["exposure"] = float(e)
    return {"sleeves": base}


def test_rejected_exposure_is_value_weighted_not_count_weighted():
    """83% 的假设被否 ≠ 83% 的钱在否证策略上。

    六个 sleeve 名义各一万，但有四个空仓 ⇒ 真实敞口只落在 P1/P2/P4 上。
    按「6 个里 5 个被否」会报 83%，按钱算是 (P1+P2)/(P1+P2+P4)。
    """
    rx = pr.rejected_exposure(_ov(P1=27497.0, P2=0.0, P3=0.0, P4=13947.0, P5=0.0, P6=0.0))
    assert rx["total"] == 41444.0
    assert rx["rejected"] == 27497.0
    assert rx["rejected_pct"] == 66.3          # 不是 83.3
    assert rx["sids"] == ["P1"]


def test_passed_sleeve_is_not_counted_as_rejected():
    """P4 是 unverified（未过门），不能混进「已否证」—— 否则这个数会虚高。"""
    rx = pr.rejected_exposure(_ov(P4=5000.0))
    assert rx["rejected"] == 0.0
    assert rx["rejected_pct"] == 0.0
    assert rx["sids"] == []


def test_empty_book_says_nothing_instead_of_zero_percent():
    """空仓时不印「0 分钱挂在否证策略上」这种没信息量的话，直接不印。"""
    assert pr._rejected_html(_ov()) == ""


def test_rejected_banner_names_every_rejected_sleeve():
    """横幅必须点名是哪几个 sleeve —— 只说「66%」读者没法去撤。"""
    html = pr._rejected_html(_ov(P1=27497.0, P6=3947.0, P4=10000.0))
    assert "66.3%" not in html          # 这个组合是 76.0%，防止照抄上一个用例
    assert "75.9%" in html
    assert "P1" in html and "P6" in html
    assert "P4" not in html.split("（")[1].split("）")[0]   # 括号里只列被否证的


def test_rejected_banner_is_not_a_no_op_when_nothing_is_rejected():
    """全部干净时也要有一行 —— 否则「横幅不出现」会被误读成「这条逻辑没跑」。"""
    html = pr._rejected_html(_ov(P4=8000.0))
    assert "没有一分钱" in html


def test_composition_splits_by_state_not_just_rejected():
    """敞口必须按状态拆全 —— 只报「否证 80.5%」会让读者以为剩下 19.5% 是好的。

    而真实剩下的那个（P4）是 `unverified`，它已测的那一半是 −0.33%/t−5.69。
    **「不是坏消息」不等于「是好消息」**，两者必须分开印。
    """
    comp = pr.exposure_by_verdict(_ov(P1=27497.0, P4=8423.0, P6=3947.0))
    by = comp["by_state"]
    assert set(by) == {"rejected", "unverified"}
    assert by["rejected"]["value"] == 31444.0
    assert by["unverified"]["value"] == 8423.0
    assert abs(by["rejected"]["pct"] + by["unverified"]["pct"] - 100.0) < 0.2


def test_banner_names_the_unverified_remainder():
    """剩下一部分必须带状态名出现，否则横幅留了个「剩下应该是好的」的口子。"""
    html = pr._rejected_html(_ov(P1=27497.0, P4=8423.0, P6=3947.0))
    assert "其余" in html
    assert "未过门" in html and "P4" in html


def test_zero_exposure_state_is_not_listed():
    """空仓的 sleeve 不进构成 —— 否则「未过门 0.0%（P2/P3/P5）」这种噪声会稀释重点。"""
    comp = pr.exposure_by_verdict(_ov(P1=1000.0))
    assert list(comp["by_state"]) == ["rejected"]
    assert comp["by_state"]["rejected"]["pct"] == 100.0


def test_passed_state_is_rendered_differently_from_unverified():
    """真过门了要显示「已过门」—— 这个函数将来要能反映好消息，不能只认否证。"""
    specs = {"P1": {"evidence_state": "passed", "evidence": "x"}}
    comp = pr.exposure_by_verdict(_ov(P1=1000.0), specs=specs)
    assert "passed" in comp["by_state"]
    assert pr._STATE_WORD["passed"] == "已过门"
