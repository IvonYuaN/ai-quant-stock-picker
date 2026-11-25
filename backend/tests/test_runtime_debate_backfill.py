"""同日多轮 runtime debate 回填：必须取**最新**一条合法讨论（issue #445）。

背景：`_attach_debates` 拿到的是 append-only 记录（旧→新），逆序遍历本意是
「从最新往前找」。但原实现只做 `debates_by_symbol[symbol] = debate` 且无退出
条件 —— 逆序遍历让**最早**那条合法记录最后写入并胜出。于是同交易日 09:15 跑过
一轮、14:00 又补跑一轮时，页面上显示的仍是 09:15 的旧结论，看起来像「补充的
讨论没生效」。

这里直接对 `_attach_debates` 做单元测试（不走 HTTP、纯内存），锁三件事：
1. 同日多条合法记录 ⇒ 取最新那条；
2. 最新那条**校验不通过**（不完整）⇒ 回退到较早的合法记录，且不被它占位；
3. 逆序遍历的下标顺序与记录时间顺序相反这一前提不变（顺序本身不改变结论）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

import aqsp_bridge  # noqa: E402

_DATE = "2026-07-14"


def _snapshot_payload() -> dict:
    return {
        "schema_version": "v1",
        "generated_at": f"{_DATE}T09:30:00+08:00",
        "selected_date": _DATE,
        "available_dates": [_DATE],
        "candidates": [
            {
                "symbol": "600519",
                "display_name": "贵州茅台",
                "score": 72.5,
                "research_status": "纸面复核",
                "next_step": "等待证据确认",
                "context": "合成测试快照",
                "deterministic_reasons": ["量价确认"],
                "strategies": ["demo"],
                "evidence_status": "充分",
            }
        ],
        "debates": [],
        "summaries": ["研究快照已生成"],
        "source": {
            "effective": "fixture",
            "latest_trade_date": _DATE,
            "lag_days": 0,
            "status": "fresh",
        },
        "coldstart": {"status": "完成", "detail": ""},
        "stale_after": "2099-01-01T09:30:00+08:00",
        "message_status": "未产出",
        "messages": [],
    }


def _record(verdict: str, *, complete: bool = True) -> dict:
    record = {
        "symbol": "600519",
        "candidate_signal_date": _DATE,
        "research_verdict": verdict,
        "primary_risk_gate": "证据确认",
        "next_trigger": "新增独立证据",
        "active_roles": ["risk"],
        "rounds": [{"round_num": 1, "summary": f"{verdict} 的初筛"}],
    }
    if not complete:
        # 委员会这一轮没跑完：必须被 _runtime_debate_is_complete 拦掉
        record["process_recorded"] = False
    return record


def _attach(records: list[dict]):
    snapshot = aqsp_bridge._parse_snapshot(_snapshot_payload())
    return aqsp_bridge._attach_debates(snapshot, tuple(records))


def test_same_day_multiple_debates_use_latest() -> None:
    """09:15 与 14:00 各有一条合法记录 ⇒ 必须显示 14:00 那条。"""
    result = _attach([_record("09:15 旧结论"), _record("14:00 新结论")])

    assert len(result.debates) == 1
    assert result.debates[0].conclusion == "14:00 新结论"


def test_latest_incomplete_falls_back_to_earlier_valid() -> None:
    """最新那条不完整 ⇒ 回退较早的合法记录，且不被它占位。"""
    result = _attach(
        [_record("09:15 旧结论"), _record("14:00 半成品", complete=False)]
    )

    assert len(result.debates) == 1
    assert result.debates[0].conclusion == "09:15 旧结论"


def test_all_incomplete_leaves_no_debate() -> None:
    """全部不完整 ⇒ 不产出讨论（不能编一条出来）。"""
    result = _attach([_record("甲", complete=False), _record("乙", complete=False)])

    assert result.debates == ()


def test_unknown_symbol_is_ignored() -> None:
    """记录指向不在候选池里的 symbol ⇒ 直接忽略。"""
    stranger = _record("别的票")
    stranger["symbol"] = "000001"

    result = _attach([stranger])

    assert result.debates == ()


def test_records_order_is_oldest_first_by_contract() -> None:
    """把顺序反过来（新→旧）时，按「逆序遍历取首个合法」语义会取到更早那条。

    这条不是需求，而是把「records 必须是追加顺序」这个前提固定下来：
    读盘侧 `_read_runtime_debate_records` 逐行 append，顺序即写入顺序。
    """
    result = _attach([_record("14:00 新结论"), _record("09:15 旧结论")])

    assert result.debates[0].conclusion == "09:15 旧结论"


@pytest.mark.parametrize("flag", ["conclusion_recorded", "evidence_sufficient"])
def test_other_incompleteness_flags_also_fall_back(flag: str) -> None:
    """三条记录、最新那条不完整 ⇒ 取中间那条（不是最早那条）。

    这条比「两条记录」更能分辨实现：没有 claimed 守卫时，逆序遍历会继续往后
    覆盖，最终写进最早那条，于是断言必须失败。
    """
    newest = _record("16:00 半成品")
    newest[flag] = False

    result = _attach([_record("09:15 最早"), _record("14:00 中间"), newest])

    assert result.debates[0].conclusion == "14:00 中间"
