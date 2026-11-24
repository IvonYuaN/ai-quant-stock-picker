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
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
# `cb_pick` 用 spec 加载，不会把 research/ 放进 sys.path；但 `_sym` 那条测试要
# 直接 import 探针模块（`cb_pick --fetch` 运行时也是同目录导入）。
if str(ROOT / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "research"))


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


def test_missing_open_price_is_reported_not_silently_dropped():
    """缺开盘价的票必须**单独返回**，不能让均值在缩水的分母上算。

    真实场景：19 只候选里只有 10 只有开盘价（新债未上市/停牌/取数失败），
    均值 +3.83% 是这 10 只的均值。如果这 9 只被静默跳过，卡片上「候选 19 只」
    与「均值 +3.83%」并列，读的人会以为 19 只都赚了 3.83% —— 最经典的高估路径。
    """
    codes = _codes(3)
    sel = [(codes[0], 100.0, 1.0), (codes[1], 100.0, 1.0), (codes[2], 100.0, 1.0)]
    op_row = np.array([110.0, np.nan, 0.0])       # 只有第一只有开盘价
    rets, missing = cp.realized(sel, op_row, codes)
    assert len(rets) == 1 and abs(rets[0] - 0.10) < 1e-9
    assert missing == [codes[1], codes[2]], "缺开盘价的票没有被点名"


def test_main_warns_when_some_candidates_have_no_open_price(tmp_path, capsys):
    """端到端：缺开盘价时，stdout 必须出现「只有 N/M 只有开盘价」。

    上一版这条只测了 `realized()` 的返回值，把打印那行删掉测试照样全绿 ——
    又是「断言守错了层」。这里直接跑 `main()` 看输出。
    """
    codes = _codes(12)
    cache = tmp_path / "cb.json"
    raw = {}
    for i, c in enumerate(codes):
        raw[c] = [["2026-11-20", 140.0, 140.0, float(i + 1)],          # 成交额 1..12
                  ["2026-11-23", 140.0, 140.0, 1.0]]
    # 次日开盘：成交额最大的那只（100011，会被选中）故意缺开盘价
    for i, c in enumerate(codes):
        raw[c][1][1] = float("nan") if i == 11 else 141.0
    cache.write_text(json.dumps(raw), encoding="utf-8")

    rc = cp.main(["--cache", str(cache), "--names", str(tmp_path / "none.json")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "规则选 4 只" in out
    assert "只有 3/4 只有开盘价" in out, "缺开盘价的票被静默跳过了"
    assert codes[11] in out


def test_symbol_prefix_routes_shenzhen_bonds_to_sz():
    """深市转债（123/127/128）必须走 sz 前缀。

    🔴 原来写成 `"sh" if code[0] in "16" else "sz"`，把所有 1 开头的都判成沪市。
    深市转债占全池 51%（162/315）。用 sh 前缀请求，腾讯**照样返回历史、
    逐行与 sz 完全相同**，只是永远少最后一根 K 线 —— 于是最新一天深市整批缺席：
    既选不进候选池，也从不进入「今日兑现」的均值。历史统计不受影响，
    所以做统计时完全看不出来，只有逐日清单会错。
    """
    import cb_overnight_probe as probe

    for code in ("123255", "127096", "128136"):
        assert probe._sym(code) == "sz" + code, code
    for code in ("110059", "111012", "113643", "118058", "132014"):
        assert probe._sym(code) == "sh" + code, code


def test_fetch_cache_falls_back_to_existing_names_on_list_failure(tmp_path, monkeypatch, capsys):
    """名单接口偶发 502 时，只要还有旧名单就继续拉日线。

    东财 clist 的 502 是已知问题；名单几乎不变，为一次限流断掉整条取数链不值得。
    """
    import cb_overnight_probe as probe

    names = tmp_path / "names.json"
    names.write_text(json.dumps({"111012": "福新转债"}), encoding="utf-8")
    monkeypatch.setattr(probe, "fetch_list", lambda: (_ for _ in ()).throw(OSError("502")))
    monkeypatch.setattr(probe, "fetch_daily",
                        lambda codes, n=640: {"111012": [["2026-11-20", 1, 2, 3]]})
    cache = tmp_path / "cb.json"
    cp.fetch_cache(cache, names)
    assert json.loads(cache.read_text(encoding="utf-8")) == {"111012": [["2026-11-20", 1, 2, 3]]}
    assert "沿用旧名单 1 只" in capsys.readouterr().out


def test_fetch_cache_fails_loudly_without_any_name_list(tmp_path, monkeypatch):
    """连旧名单都没有就必须失败退出 —— 不许静默产出一个空池当结果。"""
    import cb_overnight_probe as probe

    monkeypatch.setattr(probe, "fetch_list", lambda: (_ for _ in ()).throw(OSError("502")))
    with pytest.raises(SystemExit) as ei:
        cp.fetch_cache(tmp_path / "cb.json", tmp_path / "names.json")
    assert ei.value.code == 2
    assert not (tmp_path / "cb.json").exists()


def test_merge_cache_keeps_long_history_and_patches_the_tail():
    """`--fetch` 必须是合并，不能覆盖。

    `fetch_daily` 一次只拿得到 2 年多（腾讯单次上限 320）。直接覆盖会把 6 年历史
    砍成 2.4 年 —— 我自己踩过：1126 天的样本变 591 天，t 从 5.28 掉到 3.16，
    看起来像 alpha 衰减，其实是数据变短了。
    """
    old = {"123255": [["2021-08-02", 1.0, 1.0, 1.0], ["2026-11-23", 2.0, 2.0, 1.0]]}
    new = {"123255": [["2026-11-23", 9.0, 9.0, 1.0],   # 同一根，新值应覆盖
                      ["2026-11-24", 3.0, 3.0, 1.0]]}
    m = cp.merge_cache(old, new)
    assert [r[0] for r in m["123255"]] == ["2021-08-02", "2026-11-23", "2026-11-24"]
    assert m["123255"][1] == ["2026-11-23", 9.0, 9.0, 1.0], "同日期没有用新值覆盖"
    assert m["123255"][0] == ["2021-08-02", 1.0, 1.0, 1.0], "老历史被砍掉了"


def test_merge_cache_adds_brand_new_bonds():
    """新债不在旧缓存里，要原样并入，不能因为「旧里没有」就丢掉。"""
    m = cp.merge_cache({"111012": [["2026-11-24", 1.0, 1.0, 1.0]]},
                       {"123270": [["2026-11-24", 2.0, 2.0, 1.0]]})
    assert set(m) == {"111012", "123270"}


def test_anchor_uses_last_close_not_last_open():
    """清单锚点是「最后一个有**收盘价**的交易日」，不是「有开盘价的日子减一」。

    盘中跑的时候，最后一天已经有开盘价（甚至已有最新价）但还没收盘。
    旧写法 `last = 最后一个有开盘价的日子; ref = last - 1` 在收盘后跑，
    给出的仍然是「昨天买什么」，**永远看不到今天该买哪几只**。
    """
    cl = np.full((3, 3), 140.0)
    cl[2, :] = np.nan                  # 第三天还没收盘
    assert cp.anchor(["d0", "d1", "d2"], cl) == 1

    cl[2, :] = 141.0                   # 收盘了
    assert cp.anchor(["d0", "d1", "d2"], cl) == 2


def test_main_shows_today_list_then_previous_batch(tmp_path, capsys):
    """端到端：清单必须按「今天收盘买」给，兑现单独一块。

    构造上让两天选出**完全不同**的四只：d1 成交额 1..12，d2 反过来。
    锚点若正确（d2），今日清单里是 0..3 号；错成 d1 则会是 11..8 号。
    """
    codes = _codes(12)
    raw = {}
    for i, c in enumerate(codes):
        raw[c] = [["2026-11-23", 140.0, 140.0, float(i + 1)],
                  ["2026-11-24", 140.0 + i + 1.0, 140.0, float(12 - i)]]
    cache = tmp_path / "cb.json"
    cache.write_text(json.dumps(raw), encoding="utf-8")

    rc = cp.main(["--cache", str(cache), "--names", str(tmp_path / "none.json")])
    out = capsys.readouterr().out
    assert rc == 0
    head, tail = out.split("【上一批兑现】")
    assert "【今日清单】2026-11-24" in head
    assert codes[0] in head, "今日清单没有按最新收盘价选票"
    assert codes[11] not in head, "今日清单用了前一天的数据（锚点错成 last-1）"
    assert "2026-11-23 买 → 2026-11-24 开盘卖" in tail
    # 兑现必须按「d1 选出的那 4 只（成交额最大的 8/9/10/11 号）」算：
    # 它们的次日涨幅 9%~12%，均值 10.5/140 = +7.500%。
    # 若错用了今日清单那 4 只（0~3 号），均值会是 2.5/140 = +1.786%。
    assert "+7.500%" in tail, "兑现用错了批次"


def test_session_closed_boundary():
    """15:00 是分界：14:59 还没收盘，15:00 收盘。"""
    assert cp.session_closed(14 * 60 + 59) is False
    assert cp.session_closed(15 * 60) is True
    assert cp.session_closed(15 * 60 + 5) is True


def test_main_marks_intraday_run_as_preview(tmp_path, capsys, monkeypatch):
    """盘中跑必须标成「预览」，不能把实时价说成收盘价。

    腾讯日线在盘中也会给出一根「今天」的 K 线，close 是当前最新价、
    volume 是开盘至今的累计量。不判就会把 13:23 的价格当成「今日收盘买入价」，
    而这份清单唯一的价值就是「收盘价买」。
    """
    codes = _codes(12)
    raw = {c: [["2026-11-23", 140.0, 140.0, float(i + 1)],
               ["2026-11-24", 141.0, 141.0, float(12 - i)]]
           for i, c in enumerate(codes)}
    cache = tmp_path / "cb.json"
    cache.write_text(json.dumps(raw), encoding="utf-8")

    # 把「今天」钉到 2026-11-24，时钟拨到 13:23
    monkeypatch.setattr(cp, "today_cst", lambda: "2026-11-24")
    monkeypatch.setattr(cp, "now_hm", lambda: 13 * 60 + 23)
    cp.main(["--cache", str(cache), "--names", str(tmp_path / "none.json")])
    out = capsys.readouterr().out
    assert "📌预览（尚未收盘，按实时价）" in out
    assert "还没收盘" in out

    # 收盘后（15:05）同一份数据就不该再有预览标记
    monkeypatch.setattr(cp, "now_hm", lambda: 15 * 60 + 5)
    cp.main(["--cache", str(cache), "--names", str(tmp_path / "none.json")])
    out2 = capsys.readouterr().out
    assert "预览" not in out2
    assert "【今日清单】2026-11-24 收盘买入" in out2


def test_budget_pick_buys_in_amount_order_until_cash_runs_out():
    """¥10000 实买：按成交额降序买满为止，买不起的明确列出来。

    这是「理想化」最具体的一处：规则选的是「合格池前 1/3 全部等权」，回测按
    **等权全部持有**算收益；而 ¥10000 只买得起 4~5 只。两者不是同一个组合，
    只报等权收益而不报实买收益，就是把不可执行的组合当成结论。
    """
    sel = [("A", 900.0, 9.0), ("B", 300.0, 8.0), ("C", 200.0, 7.0), ("D", 100.0, 6.0)]
    bought, cash, skipped = cp.budget_pick(sel, 10_000.0)
    # 一手 = 10 张：A 9000 → 剩 1000；B 3000 / C 2000 买不起跳过；D 1000 刚好买得起
    assert [c for c, _, _ in bought] == ["A", "D"]
    assert cash == 0.0
    assert [c for c, _, _ in skipped] == ["B", "C"], "买不起的必须列出来，不能静默丢掉"

    bought2, cash2, skipped2 = cp.budget_pick(sel[:3], 10_000.0)
    assert [c for c, _, _ in bought2] == ["A"]
    assert skipped2  # 现金不够就是不够，不许四舍五入硬买


def test_budget_pick_never_overspends():
    """永远不许超支：花掉的钱 + 剩余现金 == 本金。"""
    sel = [("A", 137.7, 5.0), ("B", 221.3, 4.0), ("C", 96.5, 3.0), ("D", 45.0, 2.0)]
    for cap in (10_000.0, 5_000.0, 2_000.0, 100.0):
        bought, cash, _ = cp.budget_pick(sel, cap)
        spent = sum(p for _, p, _ in bought) * cp.LOT_VALUE
        assert abs(spent + cash - cap) < 1e-9, cap


def test_replay_reports_both_weights_and_they_differ(tmp_path):
    """回放必须同时给「等权全部持有」和「¥capital 实买」两个口径。

    构造：12 只候选，价格从 50 到 600（一手 500~6000 元）。¥10000 只买得起
    最贵的一只或最便宜的几只，与等权 4 只完全不同 ⇒ 两个数必须不一样，
    并且实买只数要小于规则只数。
    """
    codes = _codes(12)
    dates = ["2026-11-20", "2026-11-23"]
    n = len(codes)
    closes = np.array([140.0 + 40.0 * i for i in range(n)])    # 140..580，全部过 130 门槛
    cl = np.vstack([closes, closes])
    op = np.vstack([closes, closes * (1 + 0.01 * np.arange(1, n + 1))])
    amt = np.zeros((2, n))
    amt[0, :] = np.arange(1.0, n + 1.0)                        # 越贵成交额越大
    amt[1, :] = 1.0

    r = cp.replay(dates, codes, op, cl, amt, 1, capital=10_000.0)
    row = r["rows"][0]
    assert row["n_pick"] == 4
    assert row["n_bought"] < row["n_pick"], "¥10000 竟然买得起全部 4 只？"
    assert row["gross_pct"] is not None and row["gross_cap_pct"] is not None
    assert abs(row["gross_pct"] - row["gross_cap_pct"]) > 1e-6, "两个口径不该同值"


def test_main_warns_when_capital_buys_only_one_name(tmp_path, capsys):
    """只买得起 1~2 只时必须警告「高集中度赌博」。

    真实案例：2026-11-23 成交额第一的是 884 元的福新转债，一手 8840 元 ⇒
    ¥10000 买完只剩 ¥1160，第二只一只都买不起。规则设计的「前 1/3 等权」
    在这里退化成单票押注 —— 不警告就等于默认它能代表策略。
    """
    codes = _codes(12)
    raw = {}
    for i, c in enumerate(codes):
        px = 880.0 if i == 11 else 140.0 + i          # 11 号超贵且成交额最大
        raw[c] = [["2026-11-20", px, px, float(i + 1)],
                  ["2026-11-23", px, px, 1.0]]
    cache = tmp_path / "cb.json"
    cache.write_text(json.dumps(raw), encoding="utf-8")

    cp.main(["--cache", str(cache), "--names", str(tmp_path / "none.json")])
    out = capsys.readouterr().out
    assert "只买得起 1 只" in out
    assert "高集中度赌博" in out
