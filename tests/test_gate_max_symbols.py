"""守卫：`--max-symbols`（小规模对照实验入口）。

**为什么需要**（2026-08-08 实测）：
生产 gate 默认用**全部** covered symbols（实测 **5164 只**），在 runner（4C）上
单次 3y 全规模跑批**跑不完** —— 三次尝试全部超时：
1. 无 `--cache-path` ⇒ 39h 卡在 period 18/19；
2. 有 `--cache-path` ⇒ warm cache 让 symbols 从 4412 涨到 5164 ⇒ 更慢，4h 超时；
3. `--symbols-cache-path`（传 1200 只）⇒ **实测无效**：该参数只控制"缓存读取"，
   **不控制规模**（gate 每次仍自己生成 5164 只的 symbols 文件）。

⇒ 没有显式限规模入口 ⇒ **任何小规模对照实验都做不了**。

## 契约
1. **默认 `None` ⇒ 生产行为逐位不变**
2. 上限 < `--min-symbols` 时**报错退出**，而不是静默跑一个无意义的门禁
3. 截断按 **inspection 的确定顺序**，保证可复现
4. 使用者必须知道：**限规模结果与全规模不可直接比较**（样本不同）
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_SRC = REPO_ROOT / "scripts" / "runner" / "run_production_walkforward_gate.py"


def _source() -> str:
    return GATE_SRC.read_text(encoding="utf-8")


def test_default_is_none_so_production_unchanged() -> None:
    """默认必须 None（不限）—— 加这个参数不得改变任何生产行为。"""
    assert '"--max-symbols"' in _source()
    # 确认 default=None 确实挂在 --max-symbols 上（ast 解析后是 Constant(None)）
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            if node.args and getattr(node.args[0], "value", "") == "--max-symbols":
                kw = {k.arg: k.value for k in node.keywords}
                # ast 把 `None` 解析成 Constant 节点，需 literal_eval 解包
                default = ast.literal_eval(kw["default"]) if "default" in kw else "<missing>"
                assert default is None, f"--max-symbols 默认必须是 None，实为 {default!r}"
                return
    raise AssertionError("未找到 --max-symbols 的 add_argument 调用")


def test_rejects_limit_below_min_symbols() -> None:
    """🔴 上限 < `--min-symbols` 时必须**报错退出**，不能静默跑一个无意义门禁。"""
    src = _source()
    assert "args.max_symbols < args.min_symbols" in src, "必须比较上限与下限"
    assert "BLOCK: --max-symbols=" in src, "必须给出明确 BLOCK 原因"
    assert "return 2" in src, "必须以非零码退出（与其它 BLOCK 一致）"
    # 🔴 但 --experiment 可显式豁免（否则小规模实验无法做）
    assert "and not args.experiment" in src, "必须有 --experiment 豁免路径"
    assert '"--experiment"' in src, "必须提供 --experiment 开关"


def test_experiment_mode_is_explicitly_labelled() -> None:
    """★ 实验模式必须**自我标注**，避免小实验结果被当成门禁结论使用。"""
    src = _source()
    assert "EXPERIMENT MODE" in src, "必须打印显式警告"
    assert "不得" in src, "必须写明结果不得用于放行决策"
    assert "[EXPERIMENT]" in src, "截断日志必须带 EXPERIMENT 标记"
    assert "MIN_PRODUCTION_GATE_SYMBOLS" in src, "必须对照生产门禁下限给出差距"


def test_truncation_is_deterministic() -> None:
    """截断必须用**切片**（按 inspection 已排好的确定顺序）⇒ 可复现。"""
    src = _source()
    assert "covered_symbols[: args.max_symbols]" in src, (
        "必须用切片截断（inspection 已排序 ⇒ 结果确定可复现）；"
        "不能用 random.sample / set 迭代顺序"
    )
    assert "max-symbols applied" in src, "必须打印实际生效的规模（便于审计）"


def test_docstring_warns_results_not_comparable() -> None:
    """必须写明「限规模结果与全规模不可直接比较」——防止误用小实验结论。"""
    src = _source()
    assert "不可直接比较" in src, "必须在参数说明里警告结果不可直接比较"
    assert "timeout_seconds" in src, "必须说明会影响 timeout 自动放大公式"


def test_applied_after_coverage_inspection() -> None:
    """上限必须作用在 `inspection.covered_symbols` **之后**（覆盖预检结果，而非原始库）。"""
    src = _source()
    i_assign = src.index("covered_symbols = inspection.covered_symbols")
    i_apply = src.index("if args.max_symbols is not None")
    assert i_assign < i_apply, "必须在 coverage 预检之后再截断"
    # 且截断要落在 min_symbols 检查之前，否则上限会被下限先拦住
    i_min = src.index("if len(covered_symbols) < args.min_symbols", i_apply)
    assert i_apply < i_min, "截断必须在 min_symbols 检查之前"
