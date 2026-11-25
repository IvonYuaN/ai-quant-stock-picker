"""影子账户「时点调度」与推送卡片（`research/shortline_cloud.py`）契约测试。

## 为什么测这个

调度靠「每 3 分钟的 cron 撞窗口」：`ARENA_WINDOWS` 里每个时点只有十几分钟宽，
撞不上就**当天永远不跑**，而日志只会显示「非盘中静默」—— 看不出漏了。

`due_phases` 的 docstring 写着「抽出来是为了能单测时点窗口（cron 每 3 分钟一跳，
跨窗口边界最容易漏跑）」，但此前一条测试都没有。

最要紧的一条是 `test_every_phase_is_reachable_by_3min_cron`：
它把整天按 cron 的真实触发节奏（分钟数 % 3 == 0）走一遍，确认 4 个时点都能被撞上。
窗口一旦被改窄到 2 分钟以内，这条会立刻红。

另外测推送卡片：**「没有信号」和「有信号但推送失败」必须能区分开** ——
前者静默是对的，后者静默就是故障静默。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import shortline_cloud as sc  # noqa: E402


def _hm(h: int, m: int) -> int:
    return h * 60 + m


# ---------------- 时点窗口 ----------------

def test_weekend_runs_nothing():
    """周六周日不跑（A 股不开市）。"""
    assert sc.due_phases(_hm(9, 30), 5, set()) == []
    assert sc.due_phases(_hm(14, 50), 6, set()) == []


@pytest.mark.parametrize("phase,lo,hi", [
    ("auction", _hm(9, 26), _hm(9, 40)),
    ("morning", _hm(10, 0), _hm(10, 15)),
    ("late", _hm(14, 45), _hm(14, 58)),
    ("close", _hm(15, 5), _hm(15, 57)),
])
def test_window_boundaries_are_inclusive(phase, lo, hi):
    """窗口两端都算命中（cron 可能在正好压线的那一分钟触发）。"""
    assert phase in sc.due_phases(lo, 0, set())
    assert phase in sc.due_phases(hi, 0, set())
    assert phase not in sc.due_phases(lo - 1, 0, set())
    assert phase not in sc.due_phases(hi + 1, 0, set())


def test_done_phases_are_skipped():
    """当天已跑过的时点不再跑 —— 这是「每 3 分钟一跳但每天只记一次账」的关键。"""
    assert "morning" in sc.due_phases(_hm(10, 5), 0, set())
    assert sc.due_phases(_hm(10, 5), 0, {"morning"}) == []


@pytest.mark.parametrize("hm", [_hm(8, 0), _hm(12, 0), _hm(13, 30), _hm(16, 0)])
def test_outside_all_windows(hm):
    """盘前 / 午休 / 收盘后都不该触发任何时点。"""
    assert sc.due_phases(hm, 0, set()) == []


def test_every_phase_is_reachable_by_3min_cron():
    """🔴 核心断言：4 个时点都必须能被「每 3 分钟」的 cron 撞上。

    cron 是 `*/3`，所以只可能在「分钟数 % 3 == 0」的时刻触发。
    如果某个窗口被改窄到 2 分钟以内、又恰好跨过这些分钟，该时点就会永久漏跑，
    而日志里只显示「非盘中静默」，完全看不出来。
    """
    hit: set[str] = set()
    for hm in range(_hm(0, 0), _hm(23, 59) + 1):
        if hm % 3 != 0:
            continue
        hit.update(sc.due_phases(hm, 0, set()))

    assert hit == {"auction", "morning", "late", "close"}


def test_windows_do_not_overlap():
    """窗口不允许重叠 —— 重叠意味着同一分钟会跑两个时点，账本会串。"""
    ws = sorted((lo, hi, p) for p, lo, hi, _lb in sc.ARENA_WINDOWS)
    for (lo1, hi1, p1), (lo2, hi2, p2) in zip(ws, ws[1:]):
        assert hi1 < lo2, "%s 与 %s 窗口重叠" % (p1, p2)


# ---------------- 推送卡片 ----------------

def _hit(code: str, board: int, actionable: bool, price: float = 10.0) -> dict:
    return {"code": code, "name": "测试", "price": price, "board": board,
            "strategy": "放量%d板" % board, "actionable": actionable}


def test_markdown_separates_buyable_and_observe():
    """B 格（收盘封死）与 A 格（一字排不进）必须分两栏，不能混在一起推。"""
    det = {"pool": 31, "new_hits": [
        _hit("002579", 2, True),
        _hit("000838", 2, False),
    ]}

    md = sc.build_markdown(det)

    assert "新增触板" in md and "002579" in md
    assert "一字观察" in md and "000838" in md


def test_markdown_without_new_hits_says_none():
    """无新增时卡片要写「无」，不能留空行让人以为漏渲染。"""
    md = sc.build_markdown({"pool": 31, "new_hits": []})

    assert "无" in md


def test_markdown_mentions_pool_size():
    """池子大小要出现在标题里 —— 池子异常缩小时能第一眼看出来。"""
    md = sc.build_markdown({"pool": 31, "new_hits": []})

    assert "池31只" in md


def test_recap_counts_both_cells():
    det = {"all_hits": [_hit("A", 2, True), _hit("B", 1, True), _hit("C", 2, False)]}

    md = sc.build_recap(det)

    assert "B格(盘中可排进) 2 只" in md
    assert "A格(排不进) 1 只" in md


def test_recap_empty_day_is_stated_not_blank():
    """全天空仓也要明说「当日无触板票」，不能给一张空卡片。"""
    md = sc.build_recap({"all_hits": []})

    assert "当日无触板票" in md


def test_row_truncates_long_name():
    """名称截 4 字对齐（超长名不撑破卡片）。"""
    row = sc._row({"code": "600000", "name": "一个非常长的股票名称", "price": 12.34,
                   "board": 2, "strategy": "放量2板"})

    assert "一个非常" in row
    assert "长的股票名称" not in row
    assert "12.34" in row


# ---------------- 推送健康（区分「没信号」与「推失败」） ----------------

def test_health_counts_ok_and_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "HEALTH_FILE", tmp_path / "push_health.json")

    sc._record_health(True)
    sc._record_health(False, "send rc=1: boom")

    h = json.loads(sc.HEALTH_FILE.read_text(encoding="utf-8"))
    assert h["ok_count"] == 1
    assert h["fail_count"] == 1
    assert "boom" in h["last_error"]


def test_health_resets_counts_on_new_day(tmp_path, monkeypatch):
    """跨天必须重置计数，否则昨天失败过今天就永远显示「在失败」。"""
    monkeypatch.setattr(sc, "HEALTH_FILE", tmp_path / "push_health.json")
    sc.HEALTH_FILE.write_text(json.dumps(
        {"date": "2026-11-19", "ok_count": 9, "fail_count": 4}), encoding="utf-8")

    sc._record_health(True)

    h = json.loads(sc.HEALTH_FILE.read_text(encoding="utf-8"))
    assert h["ok_count"] == 1 and h["fail_count"] == 0


def test_health_clear_error_after_success(tmp_path, monkeypatch):
    """成功一次后要清掉上次的错误信息 —— 否则历史错误一直挂着误导排查。"""
    monkeypatch.setattr(sc, "HEALTH_FILE", tmp_path / "push_health.json")
    sc._record_health(False, "no_chat_id")
    sc._record_health(True)

    h = json.loads(sc.HEALTH_FILE.read_text(encoding="utf-8"))
    assert h["last_error"] == ""


def test_health_survives_corrupt_file(tmp_path, monkeypatch):
    """健康文件损坏时重置而不是崩 —— 它只是观测数据，不该拖垮推送。"""
    monkeypatch.setattr(sc, "HEALTH_FILE", tmp_path / "push_health.json")
    sc.HEALTH_FILE.write_text("{not json", encoding="utf-8")

    sc._record_health(True)

    h = json.loads(sc.HEALTH_FILE.read_text(encoding="utf-8"))
    assert h["ok_count"] == 1


def test_board_cards_state_fill_feasibility_not_a_buy_recommendation():
    """卡片要**分开说**两件事：成交可行性（B 格盘中能排进）与策略有效性（已否证）。

    🔴 11-25 这里改错过一次，记下来：当时以为「B 格 = 收盘封死 ⇒ 买不到」，
    把卡片写成「B格(收盘封死·当日买不到)」。**这是错的**，两个层被混了：
      - **回测** B 格 = `收盘 ≥ 涨停 × 0.998`（日线只能这么判）⇒ 收盘价确实买不到，
        这正是回测收益属于语义前视的原因（`executable_entry_probe.py`）；
      - **实盘检测** B 格 = `open 没顶死 + 摸板`（实时报价能判）⇒ 集合竞价确实成交了、
        盘中存在可买时点（`paper_strategies.py` 的 B 格说明）。
    实盘卡片显示的是实盘检测结果，所以「盘中可排进」是事实，「买不到」反而是假陈述。
    而策略被否证的原因是**按可成交的「摸板即买」口径算收益为负**（首板 −0.94%/t−16.5），
    不是买不到 —— 把否证原因写成「买不到」是另一种不诚实。

    所以守三件事：① 不出现有歧义的「可买」（会被读成「该买」）；
    ② 不出现「买不到」（错）；③ 复盘必须同时给出判决，否则等于在推已否证的策略。
    """
    recap = sc.build_recap({"all_hits": [_hit("A", 2, True), _hit("B", 1, False)]})
    markdown = sc.build_markdown({"pool": 31, "new_hits": [_hit("002579", 2, True)]})
    for md in (recap, markdown):
        assert "可买" not in md, "「可买」有歧义（会被读成「该买」）：\n%s" % md
        assert "买不到" not in md, "B 格盘中可排进，「买不到」是假陈述：\n%s" % md
    assert "盘中可排进" in recap, "必须描述成交可行性"
    assert "已否证" in recap, "复盘必须同时给出判决"


def test_no_source_uses_the_wrong_bcell_wording():
    """守卫：全仓不许出现两种错误措辞 —— `B格可买`（歧义）与 `B格收盘封死`（错）。

    🔴 为什么用源码扫描而不是渲染断言：文案生成在 `shortline_signal` 内部
    （`放量N板·…` 那段），要覆盖它得构造整个检测流程；而**卡片模板改了
    不代表数据里的 strategy 改了** —— `#655` 第一版就漏了这里，
    收盘复盘 dry-run 出来才发现每行末尾还是旧文案。

    ⚠️ 这两个词都**没有合法用法**：实盘 B 格 = 开盘没顶死 + 盘中摸板（可排进），
    「收盘封死」是**回测**用日线近似出来的口径，不能拿来描述实盘卡片。
    """
    root = Path(__file__).resolve().parents[1]
    files = [p for p in (root / "research").rglob("*.py")
             if "converge-backup" not in str(p) and "_bak" not in str(p)]
    assert len(files) > 20, "扫描范围异常（零命中会假绿）：只有 %d 个文件" % len(files)
    for bad in ("B格可买", "B格收盘封死"):
        offenders = [p.name for p in files
                     if bad in p.read_text(encoding="utf-8", errors="ignore")]
        assert not offenders, "仍在用 %r：%s" % (bad, offenders)


def test_recap_truncates_long_board_lists():
    """20 只票不能全列 —— 一屏约 20 行，超了等于没推。

    2026-11-25 实测当天 B 格 20 只 + A 格 5 只 = 一条 30 行的消息。
    盘中卡片早就守了 `mode_card.MAX_ROWS`，复盘卡漏了同一个约束。
    """
    det = {"all_hits": [_hit("60%04d" % i, 2, True) for i in range(20)]}
    md = sc.build_recap(det)
    listed = [ln for ln in md.splitlines() if ln[:6].isdigit()]
    assert len(listed) == sc.MAX_RECAP_ROWS, "列了 %d 只，上限是 %d" % (
        len(listed), sc.MAX_RECAP_ROWS)
    assert "还有 12 只" in md, "截断必须说明还有多少，不能静默丢弃"
    assert "按封单降序取前 8" in md, "要说清截断口径"


def test_recap_orders_rows_by_seal_amount():
    """封单厚的排前面 —— 它才是「排不排得进 / 封得牢不牢」的判断依据。"""
    hits = [dict(_hit("600001", 2, True), seal_amt_wan=100.0),
            dict(_hit("600002", 2, True), seal_amt_wan=9000.0)]
    md = sc.build_recap({"all_hits": hits})
    assert md.index("600002") < md.index("600001")
