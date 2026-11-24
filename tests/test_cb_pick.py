"""转债隔夜「每日候选清单」（`research/cb_pick.py`）的选票守卫。

这份清单每天推给老大照着做纸面跟踪，所以它错不起 —— 一旦选票用了
**今天**才有的数据，清单上会出现一批「事后看很准、实盘买不到」的票，
而这种错在数字上表现为「收益特别漂亮」，最不容易被怀疑。

本项目已经犯过一次：`cb_overnight_probe.py` 第一版用**今日**成交额选票、
算**今日**收益，得到 +0.283%/天(t5.77)，全段 +0.993%/天 = 238%/年。
验伪器一眼看穿——任何真实资产都不可能。根因是 A 股/转债「阳线放量、阴线缩量」，
用当天成交额筛样本 = 按结果筛样本。

| 自欺方式                               | 对应测试                                        |
|---------------------------------------|------------------------------------------------|
| 用参考日之后的成交额选票 ⇒ 语义前视        | test_pick_ignores_rows_after_the_reference_day   |
| 门槛写成 >=，把 130.00 收进来            | test_price_threshold_is_strictly_greater_than    |
| 「前 1/3」实际取了全部或取了 1 只          | test_takes_exactly_one_third_largest_by_amount   |
| 合格池太小仍硬选 ⇒ 单票满仓               | test_too_few_candidates_means_no_trade_today     |
| 返回顺序随机 ⇒ 同一天两次跑出的清单不一样     | test_output_is_sorted_by_amount_desc             |
| 成交额用库里全 NULL 的 amount 列           | test_amount_is_volume_times_close                |
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def _load():
    p = ROOT / "research" / "cb_pick.py"
    spec = importlib.util.spec_from_file_location("cb_pick", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["cb_pick"] = m
    spec.loader.exec_module(m)
    return m


cp = _load()


def _codes(n: int) -> list[str]:
    return ["1%05d" % i for i in range(n)]


def _panel(closes, amounts):
    """单行面板：index 0 = 参考日。"""
    cl = np.asarray([closes], dtype=float)
    amt = np.asarray([amounts], dtype=float)
    return cl, amt


# ---------------- 语义前视 ----------------

def test_pick_ignores_rows_after_the_reference_day():
    """参考日 t 的选票，必须完全不看 t 之后的行。

    做法：先算一次，再把「未来」那些行的成交额改成天量（会彻底换掉选票），
    断言 t 的清单一个元素都没动。反向再断言「未来行确实被改动影响」——
    否则测试可能因为根本没读写这块内存而恒真。
    """
    codes = _codes(12)
    T, N = 3, len(codes)
    cl = np.full((T, N), 140.0)
    amt = np.tile(np.arange(1.0, N + 1.0), (T, 1))       # 每行相同，t=1 与 t=2 都可用

    before_t1 = cp.pick(1, cl, amt, codes)
    assert before_t1 is not None

    amt2 = amt.copy()
    amt2[2, :] = amt2[2, ::-1] * 1000.0                  # 只动「未来」那一行
    assert cp.pick(1, cl, amt2, codes) == before_t1, "参考日 t 的选票被未来数据影响了 —— 前视"

    # 反向断言：同一个扰动确实会改变 t+1 的选票（证明上面的相等不是因为扰动没生效）
    assert cp.pick(2, cl, amt2, codes) != cp.pick(2, cl, amt, codes)


def test_pick_ignores_future_price_rows():
    """价格侧同样不能有前视。"""
    codes = _codes(12)
    T, N = 3, len(codes)
    cl = np.full((T, N), 140.0)
    amt = np.tile(np.arange(1.0, N + 1.0), (T, 1))
    cl2 = cl.copy()
    cl2[2, :] = 999.0
    assert cp.pick(1, cl2, amt, codes) == cp.pick(1, cl, amt, codes)


def test_replay_buys_at_previous_close_sells_at_next_open():
    """账本回放必须「t-1 收盘买、t 开盘卖」，且**用 t-1 的成交额选票**。

    这是整套逻辑里唯一真正会前视的地方：`pick()` 本身只读一行，前视只可能发生在
    「传了哪一行」。把 `pick(t - 1, ...)` 写成 `pick(t, ...)` 之后，回放照样能跑出
    一个漂亮数字，而那是拿当天收盘数据选当天该买的票。

    构造上让两天选出**完全不同**的四只债，收益也拉开，这样两种口径不可能同值：
      · 按 t-1 选 ⇒ 选中收益率 8/9/10/11% 那四只 ⇒ 均值 9.5%
      · 按 t   选 ⇒ 选中收益率 0/1/2/3% 那四只     ⇒ 均值 1.5%
    """
    codes = _codes(12)
    dates = ["2026-11-20", "2026-11-23"]
    n = len(codes)
    cl = np.full((2, n), 140.0)
    op = np.full((2, n), 140.0)
    for j in range(n):
        op[1, j] = 140.0 * (1 + j / 100.0)        # 次日开盘涨幅 0%..11%
    amt = np.zeros((2, n))
    amt[0, :] = np.arange(1.0, n + 1.0)           # t-1：越靠后成交额越大
    amt[1, :] = np.arange(n, 0, -1, dtype=float)  # t  ：反过来

    r = cp.replay(dates, codes, op, cl, amt, 1)
    assert r["n"] == 1
    row = r["rows"][0]
    assert row["buy_date"] == "2026-11-20" and row["sell_date"] == "2026-11-23"
    assert row["n_pick"] == 4
    assert abs(row["gross_pct"] - 9.5) < 1e-6, "回放用了卖出日的成交额选票（前视）"


# ---------------- 门槛与取数 ----------------

def test_price_threshold_is_strictly_greater_than():
    """130.00 恰好等于强赎线，**不能**进池（`>` 不是 `>=`）。"""
    codes = _codes(9)
    closes = [130.0] * 9                    # 全部等于门槛
    amounts = np.arange(1.0, 10.0)
    cl, amt = _panel(closes, amounts)
    assert cp.pick(0, cl, amt, codes) is None

    closes = [130.01] * 9                   # 刚过门槛
    cl, amt = _panel(closes, amounts)
    got = cp.pick(0, cl, amt, codes)
    assert got is not None and len(got) == 3


def test_takes_exactly_one_third_largest_by_amount():
    """12 只合格 ⇒ 取成交额最大的 4 只（1/3），不是 3 只也不是 6 只。"""
    codes = _codes(12)
    amounts = np.array([float(i) for i in range(1, 13)])   # 1..12，最大的是最后 4 个
    cl, amt = _panel([140.0] * 12, amounts)
    got = cp.pick(0, cl, amt, codes)
    assert [c for c, _, _ in got] == [codes[11], codes[10], codes[9], codes[8]]


def test_too_few_candidates_means_no_trade_today():
    """合格池 8 只 ⇒ 前 1/3 不足 3 只 ⇒ 当天不做（返回 None，而不是硬选几只）。"""
    codes = _codes(8)
    cl, amt = _panel([140.0] * 8, np.arange(1.0, 9.0))
    assert cp.pick(0, cl, amt, codes) is None


def test_illiquid_and_nonfinite_rows_are_excluded():
    """成交额为 0 / nan、收盘价 nan 的债一律不进池 —— 否则会选到「昨天没成交」的票。

    故意把这三只的成交额设成**最大**：只要过滤漏一处，它们必定排进前 1/3。
    """
    codes = _codes(16)
    closes = [140.0] * 5 + [np.nan] + [140.0] * 10
    amounts = [1.0, 2.0, 3.0, 4.0, 5.0,
               1e9,                    # 5: 收盘价 nan（成交额最大也没用）
               0.0,                    # 6: 成交额 0
               np.nan,                 # 7: 成交额 nan
               np.inf,                 # 8: 成交额 inf
               6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    cl, amt = _panel(closes, amounts)
    got = cp.pick(0, cl, amt, codes)
    assert got is not None
    picked = {c for c, _, _ in got}
    assert codes[5] not in picked, "收盘价 nan 的票被选中"
    assert codes[6] not in picked, "成交额 0 的票被选中"
    assert codes[7] not in picked, "成交额 nan 的票被选中"
    assert codes[8] not in picked, "成交额 inf 的票被选中（`> 0` 挡不住 inf）"
    assert len(got) == 4                      # 12 只合格 × 1/3


def test_output_is_sorted_by_amount_desc():
    """返回必须按成交额降序 —— 清单要能复现，同一天跑两次得一模一样。"""
    codes = _codes(15)
    rng = np.random.default_rng(7)
    amounts = rng.permutation(np.arange(1.0, 16.0))
    cl, amt = _panel([140.0] * 15, amounts)
    got = cp.pick(0, cl, amt, codes)
    amts = [a for _, _, a in got]
    assert amts == sorted(amts, reverse=True)
    assert [c for c, _, _ in got] == [c for c, _, _ in cp.pick(0, cl, amt, codes)]


def test_amount_is_volume_times_close():
    """库 `amount` 列全 NULL ⇒ 成交额只能 `volume × close` 近似，这里守这个口径。"""
    raw = {"113001": [["2026-11-20", 140.0, 150.0, 2_000_000.0],
                      ["2026-11-23", 151.0, 152.0, 1_000_000.0]]}
    dates, codes, op, cl, amt = cp.build(raw)
    assert dates == ["2026-11-20", "2026-11-23"]
    assert cl[0, 0] == 150.0 and op[1, 0] == 151.0
    assert amt[0, 0] == 150.0 * 2_000_000.0
