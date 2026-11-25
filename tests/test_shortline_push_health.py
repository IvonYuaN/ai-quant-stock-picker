"""推送健康状态（`research/shortline_cloud.py`）契约测试。

为什么值得单独测：`#654` 让推荐类推送只推已过门模式之后，「今天 0 次推送」变成常态
（当前 0 个模式过门）。而原来的 `push_health.json` 只有 `ok_count`/`fail_count`，
于是三种完全不同的情况长得一模一样：

    「非盘中」 / 「无新信号」 / 「有成交但模式都不过门」 →  ok_count:0, fail_count:0

老大问「怎么不推了」时，只能去翻 stderr 日志猜。这里锁四件事：
1. 静默**不计入失败** —— 混在一起会让 `fail_count` 失去意义（一天上百次静默 = 上百次"失败"）。
2. 静默原因按**固定枚举**计数，不接受自由文本。
3. 跨天重置要清计数、但**保留结构**（原来直接赋新 dict，新增字段会被漏掉）。
4. `main()` 真的在每个静默出口调用了记录（端到端，不是"源码里出现过"）。
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


@pytest.fixture()
def health(tmp_path, monkeypatch):
    path = tmp_path / "push_health.json"
    monkeypatch.setattr(sc, "HEALTH_FILE", path)
    return path


def test_skip_does_not_count_as_failure(health):
    """🔴 静默 ≠ 失败。失败要人去修通道，静默说明系统正常、只是没东西可推。"""
    sc._record_skip("no_signal")
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["skip_count"] == 1
    assert h["fail_count"] == 0, "静默被算成了推送失败"
    assert h["ok_count"] == 0
    assert h["skip_reasons"] == {"no_signal": 1}
    assert h["last_skip_reason"] == sc.SKIP_CODES["no_signal"]
    assert h["last_skip"]


def test_skip_reasons_accumulate_by_code(health):
    for code in ("no_signal", "no_signal", "no_pushable"):
        sc._record_skip(code)
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["skip_count"] == 3
    assert h["skip_reasons"] == {"no_signal": 2, "no_pushable": 1}


def test_unknown_skip_code_falls_back_to_other(health):
    """未知码不能静默丢弃，也不能把自由文本写进 skip_reasons。"""
    sc._record_skip("some_new_code")
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["skip_reasons"] == {"other": 1}


def test_skip_and_failure_are_separately_counted(health):
    sc._record_skip("no_fill")
    sc._record_health(False, "send rc=1: boom")
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["skip_count"] == 1 and h["fail_count"] == 1
    assert h["last_error"].startswith("send rc=1")


def test_successful_push_clears_last_error(health):
    sc._record_health(False, "boom")
    sc._record_health(True)
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["ok_count"] == 1 and h["fail_count"] == 1
    assert h["last_error"] == ""


def test_cross_day_reset_clears_counters_but_keeps_shape(health):
    """跨天重置要清计数，但**不能把结构一起换掉** —— `skip_reasons` 就是这么丢过一次。"""
    health.write_text(json.dumps({
        "date": "2020-01-01", "ok_count": 9, "fail_count": 3,
        "skip_count": 7, "skip_reasons": {"no_signal": 7},
    }), encoding="utf-8")
    h = sc._load_health()
    assert h["date"] != "2020-01-01"
    assert h["ok_count"] == 0 and h["fail_count"] == 0
    assert h["skip_count"] == 0 and h["skip_reasons"] == {}
    assert "skip_reasons" in h and "skip_count" in h


def test_corrupt_health_file_does_not_raise(health):
    """健康文件坏了不能反过来把推送流程打挂（它只是诊断数据）。"""
    health.write_text("{not json", encoding="utf-8")
    sc._record_skip("no_signal")
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["skip_count"] == 1


def test_silence_reason_distinguishes_no_fill_from_no_pushable():
    """「今天真没成交」与「成交了但模式都不过门」对排查是完全不同的事。"""
    assert sc._silence_reason({}) == "no_fill"
    assert sc._silence_reason({"P1": {"bought": []}}) == "no_fill"
    assert sc._silence_reason({"P1": None}) == "no_fill"
    assert sc._silence_reason({"P1": {"bought": [{"code": "600000"}]}}) == "no_pushable"


def test_skip_codes_are_a_fixed_enum():
    """守卫：原因码必须固定 —— 自由文本会把 health JSON 变成第二份日志。"""
    assert set(sc.SKIP_CODES) == {
        "no_signal", "no_fill", "no_pushable", "duplicate", "other"}
    assert all(isinstance(v, str) and v for v in sc.SKIP_CODES.values())


# ---------------- 端到端：main() 的静默出口真的记了原因 ----------------

def _stub_main(monkeypatch):
    monkeypatch.setattr(sc, "arena_board", lambda det: None)
    monkeypatch.setattr(sc, "arena_due_phases", lambda dry=False: [])
    # `main()` 现在会把今日结算结果透成 `stale=` —— stub 必须接住这个参数，
    # 否则测试会因 TypeError 假红（不是被测逻辑的问题，是替身过期）。
    monkeypatch.setattr(sc, "_arena_brief", lambda stale=False: "")


def test_main_records_no_signal_when_nothing_happened(health, monkeypatch):
    _stub_main(monkeypatch)
    monkeypatch.setattr(sc, "run_detection", lambda: {})
    assert sc.main(["--force"]) == 0
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["skip_reasons"] == {"no_signal": 1}


def test_main_records_no_pushable_when_all_modes_are_unproven(health, monkeypatch):
    """🔴 这条就是老大 11-25 那次静默：有成交，但全是未过门/已否证模式。"""
    _stub_main(monkeypatch)
    monkeypatch.setattr(sc, "run_detection",
                        lambda: {"new_hits": [{"code": "600000", "actionable": True}]})
    monkeypatch.setattr(sc, "today_picks",
                        lambda d: {"P1": {"bought": [{"code": "600000"}]}})
    assert sc.main(["--force"]) == 0
    h = json.loads(health.read_text(encoding="utf-8"))
    assert h["skip_reasons"] == {"no_pushable": 1}
    assert h["fail_count"] == 0, "闸门静默被算成了推送失败"


def test_health_reports_exact_exit_counts_not_log_line_count(health, monkeypatch, capsys, tmp_path):
    """`--health` 原来把「含今日日期的日志行」当 cron 触发次数 —— 那是假数字。

    卡片正文（`## 打板收盘复盘 2026-11-25`）含日期，而大量 `[cloud] …静默` 行
    根本不带日期 ⇒ 数出来既不是触发数也不是下界，无法拿来证明「系统还在跑」。
    改用健康文件里每次出口都记的三个精确计数。
    """
    monkeypatch.setattr(sc, "HERE", tmp_path)
    today = sc.datetime.now(sc.CST).strftime("%Y-%m-%d")
    # 100 行里 40 行含日期 —— 旧实现会报「cron 触发 40 次」，纯属幻觉。
    (tmp_path / "cron.log").write_text(
        "## 打板收盘复盘 %s\n" % today * 40 + "[cloud] 本轮无新增触板票，不推\n" * 60,
        encoding="utf-8")
    health.write_text(json.dumps({"date": today, "ok_count": 2, "fail_count": 1,
                                  "skip_count": 7, "skip_reasons": {},
                                  "last_attempt": "15:10", "last_skip": "15:03"}),
                      encoding="utf-8")

    assert sc.main(["--health"]) == 0
    out = capsys.readouterr().out

    assert "今日推送出口 10 次（成功 2 / 失败 1 / 静默 7）" in out, out
    assert "cron 触发" not in out, "别再拿日志行数冒充触发次数：%s" % out
