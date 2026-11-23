"""多模式推荐卡片（`research/mode_card.py`）契约测试。

为什么值得单独测：这张卡是老大**唯一**能看到的推荐出口。
2026-11-23 老大原话：「你现在都推的是打板，我说了你可以拓展合理的参数模式」——
查下来根因不是没跑其他模式（09:27 竞价、10:00 超跌都在买、都在记账），
而是卡片只渲染了打板那一段。这类 bug 既不会报错也不会让测试变红：
账本数字全对，只是**人看不到**。

所以这里锁四件事：
1. 每个模式必须把**参数 + 证据状态**印出来。六个模式里只有 P1/P6 真过门，
   其余是「未过门·前向验证中」；不标状态就是把未验证的混进已验证里卖。
2. 有信号但一股没买成，**不能静默**（要写「有 N 个信号，全部未成交」+ 原因归并）。
3. 未成交原因必须归并（8 只票各打一行「买不起1手」会把真信息埋掉）。
4. 还没到时点的模式写「未到 14:45」，不能装作今天没这个模式。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import mode_card  # noqa: E402
from paper_strategies import STRATEGIES  # noqa: E402


# 2026-11-23 09:27 真实竞价结果（runner state/phase_2026-11-23_auction.done 原文），
# 用真数据做夹具：这批票当天真的买了，卡片渲染不出来就是老大的投诉本身。
AUCTION_20261123 = {
    "P4": {
        "name": "竞价抢筹",
        "signals": 8,
        "bought": [
            {"code": "002685", "name": "华东重机", "price": 7.86, "shares": 200,
             "note": "高开+6.2% 竞价额22946万(占昨8.0%)"},
            {"code": "000823", "name": "超声电子", "price": 18.99, "shares": 100,
             "note": "高开+2.6% 竞价额11494万(占昨5.4%)"},
        ],
        "skipped": [{"code": c, "why": "仓位已满"}
                    for c in ("002148", "002548", "600467", "600489", "600130", "301313")],
    }
}

MORNING_20261123 = {
    "P3": {
        "name": "超跌反抽",
        "signals": 7,
        "bought": [
            {"code": "688199", "name": "久日新材", "price": 16.45, "shares": 100,
             "note": "昨日-16.9% → 今-0.5%"},
            {"code": "603138", "name": "海量数据", "price": 11.74, "shares": 100,
             "note": "昨日-9.7% → 今+0.3%"},
        ],
        "skipped": [
            {"code": "300706", "why": "单笔¥2000买不起1手(价33.5)"},
            {"code": "688679", "why": "单笔¥2000买不起1手(价21.8)"},
            {"code": "301392", "why": "单笔¥2000买不起1手(价109.2)"},
            {"code": "603725", "why": "仓位已满"},
            {"code": "002205", "why": "仓位已满"},
        ],
    }
}


def test_registry_declares_params_and_evidence_for_every_mode():
    """六个模式必须都声明「参数 + 证据状态」，缺一个卡片就会印「未声明参数」。

    这是防止「加了个新 sleeve 忘了写口径」的守卫：卡片上出现
    「（未声明参数）/ ⏳未过门」的默认值，等于推了一个来路不明的模式。
    """
    for sid, spec in STRATEGIES.items():
        assert spec.get("params"), "%s 未声明 params" % sid
        assert spec.get("evidence"), "%s 未声明 evidence" % sid
        assert spec.get("evidence_state") in mode_card.EVIDENCE_TAG, \
            "%s 的 evidence_state=%r 不在枚举里" % (sid, spec.get("evidence_state"))


def test_passed_modes_tagged_passed_and_others_not():
    """🔴 当前状态：**一个过门的都没有**。P1/P2/P6 已撤回为「❌已否证」，P3/P4/P5 未过门。

    🔴 这是一份**故意的口径快照**（硬编码），不是「从注册表推导」——
    推导式断言会自己验自己、永远绿。改口径时必须回来改这里，
    这一步就是让人停下来确认「我真的有证据把它升为过门了吗」。

    这条断言直接对应红线「不冒充实有 alpha」：标签写错一个，
    老大就会把前向验证中的模式当成已验证的 alpha 去用。

    11-23 变更（一天内两次，方向相反，记清楚）：
      a) 上午把 P2 从「⚪对照」升为「✅过门」—— 依据是首板 B 格 t1_open +1.610%/t33.71。
      b) 下午**全部撤回**。`research/executable_entry_probe.py` 证明 B 格的定义是
         「收盘封死在涨停」，那个价买不到；换可成交口径后四个高度全为负：
         首板 −0.94%/t−16.5、2板 −2.30%/t−20.1、3板 −3.26%/t−13.7、4板+ −2.80%/t−8.0。
      ⇒ 教训：a) 升得太快，把「不可成交价上的统计显著」当成了 alpha。
         以后任何模式升 `passed` 前，必须先答一句「按哪个价成交」，答不出就是前视。
    """
    for sid in ("P1", "P2", "P6"):
        assert mode_card.tag_of(STRATEGIES[sid]) == "❌已否证", \
            "%s 的买点（收盘涨停价）不可成交，不得标为过门" % sid
    for sid in ("P3", "P4", "P5"):
        assert mode_card.tag_of(STRATEGIES[sid]) == "⏳未过门", sid
    assert not [s for s, spec in STRATEGIES.items()
                if spec.get("evidence_state") == "passed"], \
        "当前无任何模式过门；若要升 passed，必须先证明买点可成交"


def test_phase_card_prints_params_and_evidence_on_the_mode_head():
    """卡片必须能在同一条推送里看出「这个模式用什么参数、证据到哪一步」。"""
    md = mode_card.render_phase_card("auction", AUCTION_20261123)
    assert "P4 竞价抢筹" in md
    assert "高开2~7%" in md and "竞价额≥昨成交额2%" in md, "参数没印出来"
    assert "前向验证中·未过门" in md and "✅过门" not in md, \
        "竞价抢筹未过门，卡片不能出现 ✅过门 标签"


def test_phase_card_renders_the_fills_that_actually_happened():
    """今天真买的两只必须出现在卡片上 —— 这就是老大没看到的那两条推荐。"""
    md = mode_card.render_phase_card("auction", AUCTION_20261123)
    assert "002685" in md and "华东重机" in md and "7.86" in md
    assert "000823" in md and "超声电子" in md
    assert "高开+6.2%" in md, "推荐理由（note）必须原样带出，不能被改写"


def test_phase_card_reports_unfilled_instead_of_dropping_them():
    """有信号、一股没买成 ≠ 没信号。必须显式写「有 N 个信号，全部未成交」。"""
    no_fill = {"P5": {"name": "尾盘惯性", "signals": 3, "bought": [],
                      "skipped": [{"code": "600001", "why": "仓位已满"},
                                  {"code": "600002", "why": "单笔¥2000买不起1手(价88.0)"}]}}
    md = mode_card.render_phase_card("late", no_fill)
    assert "有 3 个信号，全部未成交" in md
    assert "超仓" in md and "买不起1手" in md


def test_all_empty_sleeves_renders_empty_string():
    """全空时返回空串，让调用方 fail-closed 静默 —— 不推「今天啥也没有」的空卡。"""
    assert mode_card.render_phase_card("morning", {}) == ""
    assert mode_card.render_phase_card(
        "morning", {"P3": {"name": "超跌反抽", "signals": 0, "bought": [], "skipped": []}}) == ""


def test_skip_summary_groups_and_orders_reasons():
    """8 只票的未成交原因必须归并成一行，且顺序固定（可复现、可比对）。"""
    skipped = ([{"why": "单笔¥2000买不起1手"} ] * 3 + [{"why": "仓位已满"}] * 2
               + [{"why": "现金不足"}] + [{"why": "别的"}])
    assert mode_card.skip_summary(skipped) == "3 买不起1手 · 2 超仓 · 1 现金不足 · 1 其他"
    assert mode_card.skip_summary([]) == ""


def test_long_fill_list_is_truncated_with_remainder_count():
    """超过 4 行要截断并写「还有 N 只」，否则一条消息刷一屏等于没推。"""
    rec = {"name": "竞价抢筹", "signals": 6,
           "bought": [{"code": "60000%d" % i, "name": "票%d" % i, "price": 10.0}
                      for i in range(6)], "skipped": []}
    md = mode_card.render_phase_card("auction", {"P4": rec})
    assert md.count("✅") == mode_card.MAX_ROWS
    assert "还有 2 只成交" in md


def test_footer_shows_daily_counts_and_pending_slots():
    """打板卡片的尾巴要让老大一眼看到「今天其他模式买了什么、哪些还没到点」。"""
    picks = {"P4": {"bought": [{}, {}]}, "P3": {"bought": [{}]}}
    footer = mode_card.render_daily_footer(picks, pending=[("P5", "14:45")])
    assert "P4 2笔" in footer and "P3 1笔" in footer
    assert "P5 未到14:45" in footer
    assert mode_card.render_daily_footer({}, pending=[]) == ""


def test_legend_explains_the_constant_stop_parameter():
    """老大点名问过「炸板-5% 永远一样」。它是规则值不是行情数据，卡片必须解释清楚。"""
    legend = mode_card.render_legend()
    assert "半个跌停5%" in legend and "双创10%" in legend
    assert "-5%" in legend


def test_legend_drops_board_only_wording_when_card_has_no_board_section():
    """纯竞价/超跌卡上写「炸板-5%」是噪音，还会让人以为这些模式也按打板口径止损。"""
    assert "炸板" not in mode_card.render_legend(with_board=False)
    assert "炸板" in mode_card.render_legend(with_board=True)


def test_board_row_marks_20cm_so_attribution_cannot_be_confused():
    """20cm 票必须打标：回测过门的是纯主板，混进去的创业板票不能算进同一个假设。"""
    import shortline_cloud

    main_board = {"code": "002006", "name": "精工科技", "price": 21.70, "board": 1,
                  "board_lim": 0.10, "strategy": "放量1板·B格可买·封单25342万 T+1兑现·炸板-5%"}
    growth = {"code": "300829", "name": "金丹科技", "price": 31.40, "board": 1,
              "board_lim": 0.20, "strategy": "放量1板·B格可买·封单18398万 T+1兑现·炸板-10%"}
    assert "20cm" not in shortline_cloud._row(main_board), "主板票不该带 20cm 标"
    assert "·20cm" in shortline_cloud._row(growth)
    assert "炸板-10%" in shortline_cloud._row(growth), "20cm 的止损幅度是 10%，不是 5%"


def test_render_card_joins_sections_and_skips_empty_ones():
    """打板段 + 多模式段 + 尾部 + 口径 + 账本摘要，按固定顺序拼成一条消息。"""
    md = mode_card.render_card("## 打板盘中 10:05", "## 🎯 10:00\n**P3**",
                               "> 今日各模式: P4 2笔", "> 影子账户 ¥10000")
    assert md.index("打板盘中") < md.index("🎯")
    assert md.index("🎯") < md.index("今日各模式") < md.index("影子账户")
    assert "半个跌停5%" in md


def test_render_card_works_with_only_one_section():
    """只有打板段（无新时点信号）时也要出卡；两段都空则返回空串。"""
    assert "打板盘中" in mode_card.render_card("## 打板盘中 10:05", "", "", "")
    assert mode_card.render_card("", "", "> 今日各模式: P4 2笔", "") == ""


def test_morning_payload_renders_two_distinct_modes_in_one_card():
    """同一张卡里两个模式各自成块，标题不串味（P3 的参数不能挂到 P4 下面）。"""
    merged = dict(AUCTION_20261123, **MORNING_20261123)
    md = mode_card.render_phase_card("morning", merged)
    p3_at, p4_at = md.index("P3 超跌反抽"), md.index("P4 竞价抢筹")
    assert p3_at < p4_at
    assert "昨跌≥0.7×跌停" in md[p3_at:p4_at]
    assert "高开2~7%" in md[p4_at:]


# ---------------- picks 回填（本改动上线前的时点） ----------------

# ---------------- picks 累积（同一天多轮不能互相覆盖） ----------------

def test_record_picks_accumulates_bought_instead_of_overwriting(tmp_path, monkeypatch):
    """同一天多轮 `record_picks`：后续轮次的空 bought **不能**把早上买的抹掉。

    打板 phase 每 3 分钟一轮。09:35 买满 5 只之后，后面每一轮的 `bought` 都是空列表。
    若直接覆盖，14:12 的卡片就只剩 P3/P4 —— 账本买了、卡片没字，
    而且不报错、不失败、测试也不红。这和「只推打板、其他模式完全没有」是同一类 bug 的镜像。
    """
    import shortline_cloud
    monkeypatch.setattr(shortline_cloud, "picks_path",
                        lambda d: tmp_path / ("picks_%s.json" % d))
    d = "2026-11-23"

    shortline_cloud.record_picks(d, "board", {"P1": {
        "name": "二板接力", "signals": 3,
        "bought": [{"code": "600001", "name": "A", "price": 10.0, "note": ""},
                   {"code": "600002", "name": "B", "price": 11.0, "note": ""}],
        "skipped": []}})
    first_at = shortline_cloud.today_picks(d)["P1"]["at"]

    # 后续轮次：吃满仓，没有新买入
    got = shortline_cloud.record_picks(d, "board", {"P1": {
        "name": "二板接力", "signals": 0, "bought": [], "skipped": []}})
    assert [b["code"] for b in got["P1"]["bought"]] == ["600001", "600002"], \
        "后续轮次的空 bought 把早上的买入抹掉了"
    assert got["P1"]["signals"] == 3, "signals 应当累加，不是被最后一轮的 0 覆盖"
    assert got["P1"]["at"] == first_at, "at 应保留首次推荐时间"

    # 第三轮又买了新的，且有一只重复：按 code 去重累积
    got = shortline_cloud.record_picks(d, "board", {"P1": {
        "name": "二板接力", "signals": 1,
        "bought": [{"code": "600002", "name": "B", "price": 11.0, "note": ""},
                   {"code": "600003", "name": "C", "price": 12.0, "note": ""}],
        "skipped": []}})
    assert [b["code"] for b in got["P1"]["bought"]] == ["600001", "600002", "600003"], \
        "应按 code 去重累积，不是简单相加也不是覆盖"


def test_backfill_picks_reconstructs_today_from_ledger(tmp_path, monkeypatch):
    """账本是权威：09:27/10:00 在改动上线前就买完了，没有 picks 文件也得能显示出来。"""
    import shortline_cloud
    monkeypatch.setattr(shortline_cloud, "picks_path",
                        lambda d: tmp_path / ("picks_%s.json" % d))
    led = {"sleeves": {"P4": {"positions": [
        {"code": "002685", "name": "华东重机", "buy_price": 7.86, "shares": 200,
         "funded": True, "note": "高开+6.2%", "buy_date": "2026-11-23", "phase": "auction"},
        {"code": "002148", "name": "仓满票", "buy_price": 9.0, "shares": 0,
         "funded": False, "unfunded_why": "仓位已满",
         "buy_date": "2026-11-23", "phase": "auction"},
        {"code": "600000", "name": "昨天的", "buy_price": 5.0, "shares": 100,
         "funded": True, "note": "", "buy_date": "2026-11-20", "phase": "auction"},
    ]}}}
    got = shortline_cloud.backfill_picks_from_ledger("2026-11-23", led=led)
    assert got["P4"]["name"] == "竞价抢筹"
    assert [b["code"] for b in got["P4"]["bought"]] == ["002685"], "只回填当日仓位"
    assert got["P4"]["skipped"] == [{"code": "002148", "why": "仓位已满"}]
    on_disk = json.loads((tmp_path / "picks_2026-11-23.json").read_text(encoding="utf-8"))
    assert on_disk["P4"]["signals"] == 2, "signals 只数当日（含未成交）"


def test_backfill_picks_returns_empty_when_no_positions(tmp_path, monkeypatch):
    """当天没有仓位就别造一个空 picks 文件 —— footer 要能干净地返回空串。"""
    import shortline_cloud
    monkeypatch.setattr(shortline_cloud, "picks_path",
                        lambda d: tmp_path / ("picks_%s.json" % d))
    assert shortline_cloud.backfill_picks_from_ledger("2026-11-23", led={"sleeves": {}}) == {}
    assert not (tmp_path / "picks_2026-11-23.json").exists()


# ---------------- 收盘结算段 ----------------

def test_settlement_card_shows_real_closed_trades_only():
    """收盘卡片只显示当天 sell_date 的真实平仓，不拿持仓浮盈冒充结算。"""
    led = {"sleeves": {
        "P4": {"trades": [
            {"code": "002685", "name": "华东重机", "sell_date": "2026-11-23",
             "funded": True, "ret_pct": 3.56, "excess_pct": 4.01, "pnl": 56.0},
            {"code": "000823", "name": "超声电子", "sell_date": "2026-11-22",
             "funded": True, "ret_pct": 9.99, "excess_pct": 9.0, "pnl": 99.0},
        ]},
        "P1": {"trades": [
            # 未成交的信号平仓不该算盈亏
            {"code": "301575", "name": "艾芬达", "sell_date": "2026-11-23",
             "funded": False, "ret_pct": 0.0, "excess_pct": None, "pnl": 0.0},
        ]},
    }}
    md = mode_card.render_settlement_card(led, "2026-11-23")
    assert "P4 竞价抢筹" in md
    assert "华东重机 +3.56%" in md and "超额+4.01%" in md
    assert "超声电子" not in md, "昨天平仓的不算今天"
    assert "艾芬达" not in md, "未成交信号不该进盈亏"
    assert "P1 二板接力" not in md, "全是未成交的模式不该显示 0 笔 0 盈亏"


def test_settlement_card_says_not_settled_instead_of_zero_excess():
    """0 结算样本写「未结算」——首日恒 0% 的超额恰是基准反数，显示 0 就是假数字。"""
    md = mode_card.render_settlement_card({"sleeves": {}}, "2026-11-23")
    assert "今日无结算" in md
    assert "0.00%" not in md and "超额0" not in md


def test_settlement_card_missing_excess_shows_dash_not_zero():
    """缺基准时 excess_pct=None ⇒ 显示「超额—」，不显示 0。"""
    led = {"sleeves": {"P3": {"trades": [
        {"code": "688199", "name": "久日新材", "sell_date": "2026-11-23",
         "funded": True, "ret_pct": -2.1, "excess_pct": None, "pnl": -30.0},
    ]}}}
    md = mode_card.render_settlement_card(led, "2026-11-23")
    assert "超额—" in md
    assert "超额0" not in md


def test_settlement_card_shows_stop_counterfactual():
    """结算卡必须显示「若不止损」——回测没有止损，账本有，差多少要看得见。"""
    led = {"sleeves": {"P1": {"trades": [
        {"code": "002749", "name": "国光股份", "sell_date": "2026-11-23",
         "funded": True, "ret_pct": -6.88, "excess_pct": None, "pnl": -133.0,
         "no_stop_ret_pct": -1.2},
        {"code": "002199", "name": "东晶电子", "sell_date": "2026-11-23",
         "funded": True, "ret_pct": -6.88, "excess_pct": None, "pnl": -72.0,
         "no_stop_ret_pct": -2.3},
    ]}}}
    md = mode_card.render_settlement_card(led, "2026-11-23")
    assert "若不止损-1.20%" in md and "若不止损-2.30%" in md
    assert "止损2笔净影响-5.13pp" in md, "两笔分别差 5.68 / 4.58pp，均值 5.13pp"


def test_picks_only_shows_every_mode_not_just_board():
    """老大 11-23 点名：推送只有打板，其他策略完全没有。

    新卡必须把 P1~P6 今天买了什么全列出来，每个模式一行、带代码/价/理由。
    """
    picks = {
        "P1": {"bought": [{"code": "002749", "name": "国光股份", "price": 9.56,
                            "note": "2板·封单510万"}]},
        "P3": {"bought": [{"code": "688199", "name": "久日新材", "price": 16.45,
                            "note": "昨日-16.9% → 今-0.5%"}]},
        "P4": {"bought": [{"code": "002685", "name": "华东重机", "price": 7.86,
                            "note": "高开+6.2% 竞价额22946万"}]},
    }
    md = mode_card.render_picks_only(picks)
    for sid, code in (("P1", "002749"), ("P3", "688199"), ("P4", "002685")):
        assert sid in md and code in md, "%s 的推荐没推出来" % sid
    assert "昨日-16.9%" in md and "高开+6.2%" in md, "理由必须带，不能只给代码"


def test_picks_only_has_no_boilerplate():
    """老大 11-23 点名「通知太多废话」：口径长句、账本摘要、笔数统计一律不出现。"""
    picks = {"P1": {"bought": [{"code": "002749", "name": "国光股份",
                                "price": 9.56, "note": "2板"}]}}
    md = mode_card.render_picks_only(picks)
    for noise in ("炸板-5%", "半个跌停", "影子账户", "今日各模式", "超t", "池", "单笔≤20%"):
        assert noise not in md, "废话字段 %s 还在" % noise


def test_picks_only_marks_passed_modes_only(monkeypatch):
    """过门用 ✓ 标，其余用 ·，不写解释——状态要有但不能占篇幅。

    🔴 不能拿生产状态当输入（11-23 起一个过门的都没有）：那样这条测试只会走到
    「全是 ·」的分支，✓ 分支等于从没被覆盖过，将来真有过门模式时才发现 bug。
    改成自己造一个 passed 状态，两边分支都验。
    """
    monkeypatch.setitem(STRATEGIES["P3"], "evidence_state", "passed")
    picks = {
        "P1": {"bought": [{"code": "002749", "name": "国光", "price": 9.56, "note": "2板"}]},
        "P3": {"bought": [{"code": "688199", "name": "久日", "price": 16.45, "note": "超跌"}]},
    }
    md = mode_card.render_picks_only(picks)
    assert "P3✓" in md, "被人为置为 passed 的模式必须打 ✓"
    assert "P1·" in md, "已否证的模式只能打 ·，不能打 ✓"
    assert "过门" not in md and "未过门" not in md


def test_picks_only_ignores_unknown_sids():
    """只遍历注册表的六个模式——picks 里混进脏 sid 不能推给老大。"""
    picks = {
        "P1": {"bought": [{"code": "002749", "name": "国光", "price": 9.56, "note": "2板"}]},
        "XX": {"bought": [{"code": "999999", "name": "脏数据", "price": 1.0, "note": "不该出现"}]},
    }
    md = mode_card.render_picks_only(picks)
    assert "P1" in md and "002749" in md
    assert "XX" not in md and "999999" not in md
