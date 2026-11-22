"""影子账户账本引擎（research/paper_ledger.py）的算钱契约测试。

为什么要单独给账本写一组测试：它负责从「买入」到「结算」到「算 t」的全部算术，
这些数字会原样出现在前端面板上，看起来和实盘一样真。算错一步，面板就会
显示一个**很可信的假数字**——这比没有数字更糟。

每条断言都对应一种具体的自欺方式：

| 自欺方式                                    | 对应测试                                      |
|--------------------------------------------|----------------------------------------------|
| cron 反复 ingest 同一信号 ⇒ 收益翻倍            | test_ingest_is_idempotent                     |
| 买当日就卖 ⇒ 前视交易                         | test_settle_respects_t_plus_one               |
| 止损等到收盘才认 ⇒ 系统性低估亏损                | test_stop_loss_does_not_wait_for_close        |
| 一字跌停当成卖得掉 ⇒ 打板尾部风险消失             | test_sealed_limit_down_defers_instead_of_selling |
| 连续跌停强平不标注 ⇒ 把卖不掉当成卖掉了           | test_stuck_limit_down_is_force_closed_after_cap |
| t 值按「笔」算 ⇒ 样本虚高、几天就「显著」          | test_t_is_clustered_by_trading_day             |
| 封死票混进主统计 ⇒ 把买不到的收益算自己赚的        | test_sealed_trades_leave_main_stats            |
| 多重检验按「已有数据」校正 ⇒ 偷偷放宽门槛          | test_fdr_penalty_uses_planned_strategy_total   |
| 缺基准记 0 ⇒ 把「没数据」说成「与大盘持平」        | test_missing_benchmark_records_none_not_zero   |

不触网、不写真实账本、不碰 2.6GB 行情库：账本函数全是纯 dict 运算，
测试只构造内存对象，`LEDGER` 与 `_pm` 都在 fixture 里被隔离。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import paper_ledger as pl  # noqa: E402

BUY_DAY = "2026-11-20"
SELL_DAY = "2026-11-23"     # 次一交易日（周一，避开周末）


# ---------------- fixtures / helpers ----------------

@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离真实账本与真实行情库。

    - `LEDGER` 指到 tmp_path：万一将来有人在这组测试里误调 save()，也写不到
      research/paper_arena.json（那是正在跑的实盘账本）。
    - `_pm` 默认置 None：账本必须在缺基准时记 None，绝不允许意外读本机库。
    """
    monkeypatch.setattr(pl, "LEDGER", tmp_path / "paper_arena.json")
    monkeypatch.setattr(pl, "_pm", None)


@pytest.fixture
def led() -> dict:
    return pl._blank()


@pytest.fixture
def sleeve(led) -> dict:
    return led["sleeves"]["P3"]


def _row(p: float, o: float | None = None, h: float | None = None,
         lo: float | None = None, ld: float | None = None) -> dict:
    """构造一条快照行（字段名与 paper_market 快照一致：p 现价 / o 开 / h 高 / l 低 / ld 跌停价）。"""
    o = p if o is None else o
    h = max(p, o) if h is None else h
    lo = min(p, o) if lo is None else lo
    return {"p": p, "o": o, "h": h, "l": lo, "ld": ld if ld is not None else round(p * 0.9, 2)}


def _trade(day: str, ret_pct: float, funded: bool = True,
           excess_pct: float | None = None, sealed: bool = False) -> dict:
    """手工造一笔已结算成交（用于精确断言统计口径，_close 的正确性由结算组测试保证）。"""
    return {"sell_date": day, "ret_pct": ret_pct, "funded": funded,
            "excess_pct": excess_pct, "sealed_entry": sealed, "unfunded_why": ""}


class _StubBench:
    """固定收益的基准桩：只回答「这段时间全市场等权涨了多少」，None 表示缺快照。"""

    def __init__(self, ret: float | None) -> None:
        self.ret = ret

    def bench_between(self, d1: str, d2: str) -> float | None:  # 签名与真实模块一致
        return self.ret


# ---------------- 买入 ----------------

def test_ingest_is_idempotent(led, sleeve):
    """同一 code|date|phase 被 cron 重复 ingest 两次，只能记一次账、扣一次钱。

    cron 每 3 分钟跑一轮，同一信号会被扫到十几次。幂等键失效 = 收益凭空翻十倍。
    """
    sigs = [{"code": "600000", "name": "浦发银行", "price": 10.0}]
    first = pl.ingest(led, "P3", sigs, BUY_DAY, "morning")
    cash_after_first = sleeve["cash"]
    second = pl.ingest(led, "P3", sigs, BUY_DAY, "morning")

    assert len(first["bought"]) == 1
    assert second["bought"] == [] and second["skipped"] == []
    assert len(sleeve["positions"]) == 1
    assert sleeve["cash"] == pytest.approx(cash_after_first)


def test_cash_and_positions_stay_conserved(led, sleeve):
    """买了 N 只之后：现金 + 各仓成本 == 初始本金（一分钱都不许凭空生出来）。"""
    sigs = [{"code": "60000%d" % i, "price": round(8.0 + i, 2)} for i in range(3)]
    pl.ingest(led, "P3", sigs, BUY_DAY, "morning")

    funded_cost = sum(p["cost"] for p in sleeve["positions"] if p["funded"])
    assert funded_cost > 0
    assert sleeve["cash"] + funded_cost == pytest.approx(sleeve["capital"])


def test_one_lot_budget_blocks_expensive_stock(led, sleeve):
    """¥1 万本金 × 20% = ¥2000 ⇒ 只够 1 手 ≤¥19.8 的票。

    30.5 元的票照记信号但买不进（实盘 603931 格林达就是这条），原因必须写清楚，
    不许悄悄按 0 股记账、也不许假装买到了。
    """
    res = pl.ingest(led, "P3", [{"code": "603931", "name": "格林达", "price": 30.5}],
                    BUY_DAY, "morning")
    pos = sleeve["positions"][0]

    assert res["bought"] == []
    assert pos["funded"] is False
    assert pos["shares"] == 0
    assert pos["cost"] == 0.0
    assert "买不起1手" in pos["unfunded_why"]
    assert sleeve["cash"] == pytest.approx(sleeve["capital"])     # 一分钱没扣


def test_sealed_entry_still_buys_at_limit_price(led):
    """B 格「检测时已封死」也必须成交 —— 这是回测唯一过门的作业集（11-22 口径修正）。

    🔴 为什么不能判「封死排不进」：回测 B 格的定义是
    「open 没顶死 + 摸板 + **收停板**」，「收停板」在盘中就表现为 sealed=True。
    若在 ingest 层把它判死，等于把 +1.37%/t19.2/n23571 那批全部排除，
    P1/P2 从起跑起 0 成交、资金 100% 闲置 —— 验的根本不是回测那个假设。

    实盘对照：002579 中京电子 11-20 open 12.48 < 涨停 13.39（B 格可买），
    09:37 封死。原实现判「封死排不进」= 白扔掉一个真实可成交样本。
    """
    s = led["sleeves"]["P2"]
    res = pl.ingest(led, "P2",
                    [{"code": "002086", "name": "东方海洋", "price": 3.75, "sealed": True}],
                    BUY_DAY, "board")
    pos = s["positions"][0]

    assert len(res["bought"]) == 1                    # 必须真的买
    assert pos["funded"] is True and pos["shares"] >= pl.LOT
    assert pos["sealed_entry"] is True                # 但仍要留标记，供事后拆保守口径
    assert pos["unfunded_why"] == ""
    assert s["cash"] < s["capital"]                   # 真的花了钱


def test_max_positions_caps_concurrent_holdings(led, sleeve):
    """最多 5 个仓位：第 6 只必须被「仓位已满」挡掉（不是悄悄挤进来）。"""
    sigs = [{"code": "6000%02d" % i, "price": 10.0} for i in range(6)]
    pl.ingest(led, "P3", sigs, BUY_DAY, "morning")

    assert len([p for p in sleeve["positions"] if p["funded"]]) == 5
    assert sleeve["positions"][-1]["unfunded_why"] == "仓位已满"


# ---------------- 结算 ----------------

def test_settle_respects_t_plus_one(led, sleeve):
    """买入当日不许卖。T+1 破了就是前视交易——用当天收盘价卖掉当天买的票。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")
    rows = {"600000": _row(10.5)}

    assert pl.settle(led, rows, BUY_DAY) == []
    assert len(sleeve["positions"]) == 1

    closed = pl.settle(led, rows, SELL_DAY)
    assert len(closed) == 1
    assert sleeve["positions"] == []


def test_stop_loss_does_not_wait_for_close(led, sleeve):
    """最低价已击穿止损价，就必须按止损价成交，不能等收盘看它反弹回 9.6 再算。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    closed = pl.settle(led, {"600000": _row(9.6, o=9.2, h=9.7, lo=9.0)}, SELL_DAY)

    assert closed[0]["reason"] == "止损-5%"
    assert closed[0]["ret_pct"] < 0
    assert sleeve["trades"][0]["sell_price"] == 9.2      # 不是收盘的 9.6


def test_stop_loss_on_gap_down_uses_open(led, sleeve):
    """跳空低开到止损价下方：按开盘价卖（比止损价更差），不许按止损价假装成交。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    closed = pl.settle(led, {"600000": _row(8.9, o=9.0, h=9.2, lo=8.8)}, SELL_DAY)

    assert closed[0]["reason"] == "止损-5%"
    assert sleeve["trades"][0]["sell_price"] == 9.0      # min(止损 9.5, 开盘 9.0)


def test_sealed_limit_down_defers_instead_of_selling(led, sleeve):
    """一字跌停卖不掉：仓位留着、stuck_days 累加、不产生任何成交。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")
    limit_down = {"p": 9.0, "o": 9.0, "h": 9.0, "l": 9.0, "ld": 9.0}

    assert pl.settle(led, {"600000": limit_down}, SELL_DAY) == []
    assert len(sleeve["positions"]) == 1
    assert sleeve["positions"][0]["stuck_days"] == 1
    assert sleeve["trades"] == []


def test_stuck_limit_down_is_force_closed_after_cap(led, sleeve):
    """连续跌停到上限后按收盘强平，且必须标注「乐观上界」——那天其实也卖不掉。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")
    limit_down = {"p": 9.0, "o": 9.0, "h": 9.0, "l": 9.0, "ld": 9.0}
    day = date(2026, 11, 23)

    for _ in range(pl.MAX_STUCK_DAYS):          # 前 10 次：卖不掉，继续持有
        assert pl.settle(led, {"600000": limit_down}, day.isoformat()) == []
        day += timedelta(days=1)

    closed = pl.settle(led, {"600000": limit_down}, day.isoformat())
    assert len(closed) == 1
    assert closed[0]["reason"].startswith("连续跌停")
    assert "乐观上界" in closed[0]["reason"]
    assert sleeve["positions"] == []


def test_sealed_limit_down_requires_known_limit_price():
    """缺跌停价时不许判定成一字跌停——否则一条缺字段的正常票会被冻结 10 天。"""
    flat = {"p": 9.0, "o": 9.0, "h": 9.0, "l": 9.0}

    assert pl._sealed_limit_down(flat) is False                    # 没 ld，不猜
    assert pl._sealed_limit_down({**flat, "ld": 9.0}) is True       # 明确跌停
    assert pl._sealed_limit_down({"p": 9.6, "o": 9.0, "h": 9.7,
                                  "l": 8.9, "ld": 9.0}) is False    # 区间有波动，卖得掉


def test_missing_quote_defers_then_closes_at_cost(led, sleeve):
    """停牌/数据缺失：先顺延，超过 10 天按成本平。不让仓位永久悬空，也不假装有收益。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    assert pl.settle(led, {}, "2026-11-25") == []       # 第 5 天：还在等
    assert len(sleeve["positions"]) == 1

    closed = pl.settle(led, {}, "2026-12-15")           # 第 25 天：按成本平
    assert len(closed) == 1
    assert closed[0]["reason"] == "无数据·按成本平"


# ---------------- 成本与基准 ----------------

def test_round_trip_cost_is_charged(led, sleeve):
    """平进平出必须亏掉往返成本，不许出现 0% 的「免费交易」。

    买入 100 股 @10：成本 1010（含单边 1%）；卖出 990（再扣 1%）⇒ 净 -20 / 1010 = -1.98%。
    注意它比朴素的 -2% 略小 —— 因为 1/1.01 与 0.99 不对称，按真实现金流算才是对的。
    """
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    closed = pl.settle(led, {"600000": _row(10.0)}, SELL_DAY)

    assert closed[0]["funded"] is True
    assert closed[0]["ret_pct"] == pytest.approx(-1.98, abs=0.01)
    assert closed[0]["pnl"] == pytest.approx(-20.0, abs=0.5)


def test_unfunded_trade_is_statistics_only(led):
    """买不起的票只记统计收益（朴素 2% 成本），不产生任何现金流、不动现金。"""
    s = led["sleeves"]["P3"]
    pl.ingest(led, "P3", [{"code": "603931", "price": 30.5}], BUY_DAY, "morning")

    closed = pl.settle(led, {"603931": _row(30.5)}, SELL_DAY)

    assert closed[0]["funded"] is False
    assert closed[0]["pnl"] == 0.0
    assert closed[0]["ret_pct"] == pytest.approx(-2.0, abs=0.01)
    assert s["cash"] == pytest.approx(s["capital"])


def test_missing_benchmark_records_none_not_zero(led, sleeve):
    """没有基准数据时超额必须记 None（前端显示「暂无」），绝不能用 0 冒充「与大盘持平」。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    closed = pl.settle(led, {"600000": _row(10.5)}, SELL_DAY)

    assert closed[0]["excess_pct"] is None
    assert sleeve["trades"][0]["excess_pct"] is None
    assert pl.sleeve_stats(sleeve)["exc_mean"] is None


def test_excess_subtracts_benchmark(led, sleeve, monkeypatch):
    """超额 = 策略收益 − 全市场等权基准；基准涨 5% 而策略只涨 5% ⇒ 超额为 0 附近，不能当成 alpha。"""
    monkeypatch.setattr(pl, "_pm", _StubBench(0.05))
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    closed = pl.settle(led, {"600000": _row(10.5)}, SELL_DAY)
    trade = sleeve["trades"][0]

    assert trade["bench_pct"] == 5.0                       # 基准原样记下，可复核
    assert closed[0]["excess_pct"] == pytest.approx(closed[0]["ret_pct"] - 5.0, abs=0.01)


def test_benchmark_gap_records_none_not_zero(led, sleeve, monkeypatch):
    """基准缺快照时 bench_between 返回 None：超额必须记 None，不能当成「大盘走平」。

    这是最容易偷偷发生的一条：把 None 当 0，超额就等于策略收益本身，
    beta 直接被记账成 alpha。行情库停在 11-13 时这条会天天触发。
    """
    monkeypatch.setattr(pl, "_pm", _StubBench(None))
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    closed = pl.settle(led, {"600000": _row(10.5)}, SELL_DAY)

    assert sleeve["trades"][0]["bench_pct"] is None
    assert closed[0]["excess_pct"] is None
    assert pl.sleeve_stats(sleeve)["exc_mean"] is None


def test_benchmark_failure_does_not_break_settlement(led, sleeve, monkeypatch):
    """基准模块抛异常时结算必须继续（否则一轮结算半截、账本进不去下一时点），超额记 None。"""

    class _Boom:
        @staticmethod
        def bench_between(d1: str, d2: str) -> float:
            raise RuntimeError("基准库挂了")

    monkeypatch.setattr(pl, "_pm", _Boom)
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    closed = pl.settle(led, {"600000": _row(10.5)}, SELL_DAY)

    assert len(closed) == 1
    assert closed[0]["excess_pct"] is None


# ---------------- 净值 ----------------

def test_mark_nav_overwrites_same_day(led, sleeve):
    """同一天多次 mark_nav（cron 每 3 分钟）必须覆盖而不是追加，否则净值曲线会重复。"""
    pl.ingest(led, "P3", [{"code": "600000", "price": 10.0}], BUY_DAY, "morning")

    pl.mark_nav(led, {"600000": _row(10.5)}, BUY_DAY)
    first_nav = sleeve["nav"][0]["nav"]
    pl.mark_nav(led, {"600000": _row(11.0)}, BUY_DAY)

    assert len(sleeve["nav"]) == 1
    assert sleeve["nav"][0]["nav"] > first_nav


# ---------------- 统计口径 ----------------

def test_t_is_clustered_by_trading_day(led, sleeve):
    """同一卖出日的多笔必须先在日内平均，再用「天数」做 t 检验。

    4 笔分属 2 天：按笔算 t≈0.76（看起来有苗头），按日算只有 ≈0.08（什么都不是）。
    差近 10 倍——这就是「几天就显著」的来源，也是这套账本最容易骗自己的地方。
    """
    sleeve["trades"] = [
        _trade("2026-11-21", 5.0),
        _trade("2026-11-21", -1.0),
        _trade("2026-11-21", 3.0),
        _trade("2026-11-24", -2.0),
    ]

    st = pl.sleeve_stats(sleeve)

    assert st["n"] == 4 and st["days"] == 2
    assert st["t"] == pytest.approx(0.76, abs=0.02)          # 按笔（只作参考）
    assert st["t_day"] == pytest.approx(0.08, abs=0.02)      # 按日（主判）
    assert st["t_day"] < st["t"] / 5                          # 主判必须明显更保守


def test_sealed_trades_enter_main_stats(led, sleeve):
    """sealed_entry 的成交**进**主统计，但单独计数（11-22 口径修正）。

    回测 B 格本身含「收停板」⇒ 主判口径必须含 sealed，否则验的不是同一假设。
    保留 sealed_trades / sealed_n 是为了随时能拆出「剔除封死」的保守对照。
    """
    sleeve["trades"] = [
        _trade("2026-11-21", 8.0),
        _trade("2026-11-21", 30.0, sealed=True),
    ]
    sleeve["sealed_codes"] = ["002086"]

    st = pl.sleeve_stats(sleeve)

    assert st["n"] == 2                              # 两笔都算
    assert st["mean"] == pytest.approx(19.0)         # (8 + 30) / 2
    assert st["sealed_trades"] == 1                  # 但能拆出来
    assert st["sealed_n"] == 1


def test_empty_sleeve_reports_none_not_zero(led, sleeve):
    """一笔都没有时，均值/t/胜率全是 None（前端显示「—」），不是 0。"""
    st = pl.sleeve_stats(sleeve)

    assert st["n"] == 0
    assert st["mean"] is None and st["t"] is None and st["win"] is None
    assert st["exc_mean"] is None and st["t_exc"] is None
    assert st["funded_ret"] == 0.0       # 净值口径可以给 0（本金没动，这是事实）


def test_fdr_penalty_uses_planned_strategy_total():
    """一次性测了 5 个策略就必须按 5 个校正；按「已有数据的 2 个」算等于偷偷放宽门槛。

    同一个 t=2.0：只测 2 个策略时 q≈0.046（过），测 5 个时 q≈0.114（不过）。
    """
    ts = {"a": 2.0, "b": 2.5}

    q5 = pl.fdr_qvalues(ts, m_total=5)
    q2 = pl.fdr_qvalues(ts, m_total=2)

    assert q5["a"] > q2["a"] and q5["b"] > q2["b"]
    assert q5["b"] < q5["a"]                  # q 随 p 单调，不许乱序
    assert q2["a"] < 0.10 < q5["a"]           # 同一 t，换个 m 就换结论


def test_overview_totals_five_sleeves(led):
    """组合总净值 = 5 个 sleeve 之和，总收益率按 5 份本金算（不是按单账户算）。"""
    ov = pl.overview(led)

    assert ov["total_capital"] == pytest.approx(pl.INITIAL_CAPITAL * 5)
    assert ov["total_nav"] == pytest.approx(pl.INITIAL_CAPITAL * 5)
    assert ov["total_ret_pct"] == 0.0
    assert len(ov["sleeves"]) == len(pl.STRATEGIES)
