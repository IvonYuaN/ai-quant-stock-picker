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
