"""转债隔夜「资金约束 / 分档 / β 归因」的守卫。

这三个函数都是**用来推翻我自己结论**的工具，所以它们错的方式很隐蔽：
结论看起来更漂亮，而不是更难看。

| 自欺方式                                          | 对应测试                                    |
|--------------------------------------------------|--------------------------------------------|
| 实买口径偷偷用了比等权口径更多的交易日 ⇒ n 不可比      | test_affordable_day_set_matches_daily_series  |
| 只买到 1 只的「单票赌博日」被静默丢弃                 | test_thin_days_are_recorded_not_dropped       |
| 「单只上限」形同虚设，全部现金压最贵那只              | test_cap_per_name_blocks_the_oversized_lot    |
| 分档函数边界算错，Q1 里混进高价债                    | test_buckets_partition_the_pool_without_overlap |
| β 归因把截距和斜率搞反 / 漏掉截距                    | test_attrib_recovers_beta_and_alpha_separately |
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    p = ROOT / "research" / "cb_overnight_probe.py"
    spec = importlib.util.spec_from_file_location("cb_overnight_probe", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["cb_overnight_probe"] = m
    spec.loader.exec_module(m)
    return m


pr = _load()


def _panel(prev, over):
    """prev/over 都是 (T, N)。返回与 build_panel 相同形状的最小可用集合。"""
    prev = np.asarray(prev, dtype=float)
    over = np.asarray(over, dtype=float)
    T, N = prev.shape
    return prev, over, ["d%03d" % i for i in range(T)], T, 1


def _all_n(n):
    return lambda t: np.arange(n)


# ---------------- 实买口径 ----------------

def test_affordable_day_set_matches_daily_series():
    """实买口径的 n 必须等于等权口径的 n —— 否则两个数字根本不可比。

    这是我自己踩过的坑：第一版实买只要求「买到 ≥2 只」，而等权要求「池内 ≥5 只有效」，
    于是 n=1253 vs 1126，两个数放在一起比就是耍流氓。
    """
    rng = np.random.default_rng(7)
    T, N = 40, 12
    prev = np.full((T, N), 150.0)
    over = rng.normal(0.001, 0.01, (T, N))
    # 第 10~14 天只有 3 只有效 ⇒ 等权口径会跳过，实买也必须跳过
    over[10:15, 3:] = np.nan
    prev, over, dates, T_, N_ = _panel(prev, over)
    n = prev.shape[1]

    series = pr.daily_series(over, _all_n(n), T)
    aff = pr.affordable(over, prev, dates, T, _all_n(n), 60_000.0, order="price")
    # T=40 ⇒ 39 个交易日；第 10~14 天池内只剩 3 只有效 ⇒ 两边都该跳过这 5 天
    assert len(aff["records"]) == series.size == 34
    assert aff["thin_days"] == []


def test_thin_days_are_recorded_not_dropped():
    """资金只够买 1 只时，那天不能假装不存在 —— 要进 thin_days 留痕。"""
    T, N = 8, 6
    prev = np.full((T, N), 941.0)          # 一手 9410 元，¥10000 只买得起 1 只
    over = np.full((T, N), 0.001)
    prev, over, dates, T_, _ = _panel(prev, over)
    aff = pr.affordable(over, prev, dates, T, _all_n(N), 10_000.0)
    assert aff["records"] == []
    assert len(aff["thin_days"]) == T - 1  # t=0 不参与
    assert aff["thin_days"][0] == "d001"


def test_cap_per_name_blocks_the_oversized_lot():
    """单只上限必须真的挡住「一只 941 元的债吃掉全部现金」。

    对照：不设上限 ⇒ 只买 1 只且进 thin_days；设上限 capital/3 ⇒ 买得起 3 只。
    """
    T, N = 6, 6
    px = [941.0, 150.0, 160.0, 170.0, 180.0, 190.0]
    prev = np.tile(np.asarray(px), (T, 1))
    over = np.full((T, N), 0.002)
    prev, over, dates, T_, _ = _panel(prev, over)

    free = pr.affordable(over, prev, dates, T, _all_n(N), 10_000.0,
                         order="price_desc", cap_per_name=None)
    assert free["records"] == [] and len(free["thin_days"]) == T - 1

    capped = pr.affordable(over, prev, dates, T, _all_n(N), 10_000.0,
                           order="price_desc", cap_per_name=10_000.0 / 3)
    assert capped["thin_days"] == []
    # 涨停的 941 元被挡掉 ⇒ 只买 150~190 那几只，绝不出现单票满仓
    assert all(r[2] >= 3 for r in capped["records"])
    assert max(r[3] for r in capped["records"]) <= 10_000.0


def test_affordable_never_overspends():
    rng = np.random.default_rng(11)
    T, N = 50, 15
    prev = rng.uniform(130, 400, (T, N))
    over = rng.normal(0.001, 0.01, (T, N))
    prev, over, dates, T_, _ = _panel(prev, over)
    aff = pr.affordable(over, prev, dates, T, _all_n(N), 12_000.0, order="price")
    assert aff["records"]
    for _d, _v, held, spent in aff["records"]:
        assert spent <= 12_000.0
        assert held >= 2


# ---------------- 分档 ----------------

def test_buckets_partition_the_pool_without_overlap():
    """因子单调递增、收益也单调递增 ⇒ 三档必须严格递增，且档内只数加起来等于池子。

    若分位边界算错（比如右端少一只），Q3 会掉一档，单调性就断了。
    """
    T, N = 60, 12
    factor = np.tile(np.arange(N, dtype=float), (T, 1))
    over = np.tile(np.arange(N, dtype=float) * 0.001, (T, 1))
    res = pr.quantile_buckets(over, factor, ["d%03d" % i for i in range(T)], T,
                              _all_n(N), n=3)
    q1, q2, q3 = res["Q1"], res["Q2"], res["Q3"]
    assert q1["mean_pct"] < q2["mean_pct"] < q3["mean_pct"]
    per_day = sum(r[2] for r in q1["records"][:1] + q2["records"][:1] + q3["records"][:1])
    assert per_day == N          # 12 只全被分掉，没有重复、没有遗漏
    assert q1["median_val"] == pytest.approx(1.5)
    assert q3["median_val"] == pytest.approx(9.5)


def test_constant_series_does_not_report_infinite_significance():
    """常数序列 sd=0 ⇒ t 落 0.0。

    不能是 inf（会被读成「极显著」），也不能是 nan（NaN 不是合法 JSON，
    写出去整份结果就废了）。这类退化输入在拼接多段样本时真的会遇到。
    """
    out = pr.summarise(np.full(50, 0.001))
    assert out["t"] == 0.0
    json.dumps(out, allow_nan=False)          # allow_nan=False 会拒绝 inf/nan


# ---------------- β 归因 ----------------

def test_attrib_recovers_beta_and_alpha_separately():
    """构造 r = 0.001 + 2.0×mkt + 噪声 ⇒ 必须还原 α=0.1%/天、β=2.0。

    这是整轮结论的支点：如果归因写错（漏截距、行列搞反），
    「高价档的超额全是 β」这句话就没有依据。
    """
    rng = np.random.default_rng(3)
    mkt = rng.normal(0.0005, 0.004, 400)
    series = 0.0010 + 2.0 * mkt + rng.normal(0, 0.0002, 400)
    at = pr.market_attrib(series, mkt)
    assert at["beta"] == pytest.approx(2.0, abs=0.1)
    assert at["alpha_pct"] == pytest.approx(0.10, abs=0.02)
    assert at["t_alpha"] > 5


def test_attrib_does_not_turn_beta_into_alpha():
    """纯 β（α 真为 0）时，α 的 t 值必须塌下来 —— 否则「有 alpha」是假的。"""
    rng = np.random.default_rng(5)
    mkt = rng.normal(0.0005, 0.004, 400)
    series = 2.3 * mkt + rng.normal(0, 0.0005, 400)
    at = pr.market_attrib(series, mkt)
    assert abs(at["alpha_pct"]) < 0.02
    assert abs(at["t_alpha"]) < 3


def test_amount_order_uses_amount_not_price():
    """「成交额序」必须按 amt 排，不能按价格排 —— 两者经常反相关。

    构造：便宜的那只成交额最小、贵的那只成交额最大。
    - 升序 ⇒ 先买便宜的
    - 降序 ⇒ 先买贵的（`cb_pick.budget_pick` 的真实行为：最活跃的先买）
    资金只够 1 只时，两种顺序买到的票完全不同。
    """
    T, N = 6, 6                              # ≥5 只才算合格日（与 daily_series 同门槛）
    px = [140.0, 400.0, 141.0, 401.0, 142.0, 402.0]
    amt = [1.0, 9.0, 2.0, 8.0, 3.0, 7.0]     # 贵的成交额大 ⇒ 升/降序买到的票完全不同
    prev = np.tile(np.asarray(px), (T, 1))
    over = np.zeros((T, N))
    prev, over, dates, T_, _ = _panel(prev, over)
    am = np.tile(np.asarray(amt), (T, 1))

    desc = pr.affordable(over, prev, dates, T, _all_n(N), 4200.0,
                         order="amount_desc", amt=am)
    asc = pr.affordable(over, prev, dates, T, _all_n(N), 4200.0,
                        order="amount_asc", amt=am)
    # 降序先遇 400 元（成交额 9）⇒ 一手 4000 买下，剩 200 买不起别的 ⇒ 只 1 只 ⇒ thin
    assert len(desc["thin_days"]) == T - 1
    # 升序先遇 140 元（成交额 1）⇒ 1400 + 1410 = 2810，再买 4000 不够 ⇒ 2 只
    assert all(r[2] == 2 for r in asc["records"])


def test_amount_order_requires_amt_matrix():
    """没给 amt 却要求按成交额排 ⇒ 直接报错，不许静默退化成别的顺序。"""
    T, N = 6, 5
    prev = np.full((T, N), 150.0)
    over = np.full((T, N), 0.001)
    prev, over, dates, T_, _ = _panel(prev, over)
    with pytest.raises(ValueError):
        pr.affordable(over, prev, dates, T, _all_n(N), 10_000.0, order="amount_desc")


def test_segment_attrib_does_not_lend_the_beta():
    """样本外「原始收益 t 值过门」不等于「α 过门」—— 这一组守的就是别把 β 借来当成绩。

    构造：样本内 α=0.05%/天、β=1.5；样本外 α 恰好 0、β=3.0。
    全样本归因会得到一个「平均后的」α 看着还行；分段才露出样本外是零。
    """
    rng = np.random.default_rng(21)
    days = (["2025-0%d-15" % m for m in (1, 2, 3, 4, 5, 6)] +
            ["2026-0%d-15" % m for m in (1, 2, 3, 4, 5, 6)])
    mkt, recs = {}, []
    for i in range(120):
        d = days[i % len(days)] + "-%03d" % i        # 唯一日期
        m = rng.normal(0.0005, 0.004)
        mkt[d] = float(m)
        alpha, beta = (0.0005, 1.5) if d < "2025-07-01" else (0.0, 3.0)
        recs.append((d, alpha + beta * m + rng.normal(0, 0.0003), 5, 200.0))

    seg = pr.segment_attrib(recs, mkt, "2025-07-01")
    assert set(seg) == {"in", "out"}
    assert seg["in"]["alpha_pct"] == pytest.approx(0.05, abs=0.02)
    assert seg["out"]["alpha_pct"] == pytest.approx(0.0, abs=0.02)
    assert seg["in"]["beta"] == pytest.approx(1.5, abs=0.2)
    assert seg["out"]["beta"] == pytest.approx(3.0, abs=0.2)
    # 样本外 β 明显更高 —— 这正是「原始收益显著性靠借」的机制
    assert seg["out"]["beta"] > seg["in"]["beta"]


def test_segment_attrib_skips_a_too_short_segment():
    """切完只剩几天的段直接不要，不许拿 10 天的 α 报 t 值。"""
    rng = np.random.default_rng(22)
    mkt, recs = {}, []
    # 切点前 35 天（够 30）、切点后只有 5 天（不够）⇒ out 段必须被丢掉。
    # 若把门槛从 30 放宽到 1，这 5 天就会被当成「样本外」报出 α 和 t —— 那才是错的。
    days = ["2025-01-%03d" % i for i in range(1, 36)] + \
           ["2025-05-%03d" % i for i in range(1, 6)]
    for d in days:
        m = float(rng.normal(0, 0.004))
        mkt[d] = m
        recs.append((d, 0.0005 + m, 5, 200.0))
    seg = pr.segment_attrib(recs, mkt, "2025-04-01")
    assert "in" in seg and "out" not in seg
