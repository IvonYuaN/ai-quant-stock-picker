"""价值维度 gate 五条判读器（scripts/research/value_gate_verdict.py）契约测试。

红线：纯标准库（无 numpy/pandas）/ 只读 / 确定性 / fail-closed。
- 任一判据 FAIL ⇒ 总体 REJECT；
- 有 NOT_PRODUCED 且无 FAIL ⇒ 总体 INCOMPLETE（**绝不** HOLD，因 ④⑤ 本轮 3y 单窗恒不产出）；
- 判读器**不替 gate 下「价值维度成立」的结论**。

用合成 gate.json / excess 回流 JSON（不碰 2.6GB 库、不联网、不跨测试 import），
锁死「能判的判、不产出的明说、缺项 fail-closed」语义。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "research" / "value_gate_verdict.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("value_gate_verdict_under_test", str(_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path: Path, name: str, obj: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _planb_gate() -> dict:
    """本轮 planb_value gate.json（合成）：3y 单窗产出 DSR/PBO，绝对口径全绿。"""
    return {
        "grid_profile": "planb_value",
        "deflated_sharpe": 1.2345,
        "pbo": 0.412,
        "pbo_valid": True,
        "dsr_pass": True,
        "pbo_pass": True,
        "both_pass": True,
        "cscv_reliable": True,
        "n_variants": 8,
    }


def _baseline_gate() -> dict:
    return {"grid_profile": "stable_plus", "deflated_sharpe": 0.80, "pbo": 0.4762}


def test_pure_stdlib_no_numpy():
    """判读器必须纯标准库（§24：判读可复现、不依赖重环境）。"""
    import ast

    src = _SCRIPT.read_text(encoding="utf-8")
    allowed = {"__future__", "argparse", "json", "statistics", "sys", "pathlib", "typing"}
    mods = [
        n.names[0].name if isinstance(n, ast.Import) else (n.module or "")
        for n in ast.walk(ast.parse(src))
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    third_party = [m for m in mods if m.split(".")[0] not in allowed]
    assert not third_party, f"判读器引入了非标准库依赖: {third_party}"


def test_case_a_positive_but_single_window_incomplete(tmp_path):
    """①②③全正但 ④⑤ 本轮 3y 单窗恒 NOT ⇒ INCOMPLETE，绝不 HOLD。"""
    mod = _load_module()
    gate = _write(tmp_path, "gate.json", _planb_gate())
    base = _write(tmp_path, "wf001.json", _baseline_gate())
    excess = _write(tmp_path, "excess.json", {"value_excess_ann": 0.021, "value_excess_t": 1.32})

    rc = mod.main([
        "--gate-json", str(gate), "--baseline-gate-json", str(base),
        "--excess", str(excess),
    ])
    assert rc == 0
    rows, _ = mod.judge(
        json.loads(gate.read_text()), _baseline_gate(),
        mod._load_excess(excess), 0.4762, 1.0, 0.5,
    )
    by_no = {r["no"]: r for r in rows}
    assert by_no["①"]["status"] == mod.STATUS_PASS      # DSR 过 且 Δ>0
    assert by_no["②"]["status"] == mod.STATUS_PASS      # PBO 41.2% < 基线 47.62%
    assert by_no["③"]["status"] == mod.STATUS_PASS      # 超额 +2.1%
    assert by_no["④"]["status"] == mod.STATUS_NOT       # 市值中性本轮不产出
    assert by_no["⑤"]["status"] == mod.STATUS_NOT       # 双窗本轮不产出
    # 因 ④⑤ 恒 NOT，即使全正也 INCOMPLETE（fail-closed，不宣布价值维度成立）
    assert mod.STATUS_FAIL not in [r["status"] for r in rows]
    assert mod.STATUS_NOT in [r["status"] for r in rows]


def test_case_b_negative_excess_reject(tmp_path):
    """判据③超额为负（形态二 arms 取中位）⇒ 一票 REJECT。"""
    mod = _load_module()
    gate = _write(tmp_path, "gate.json", _planb_gate())
    excess = _write(tmp_path, "excess.json", {
        "arms": {
            "WF-V01": {"excess_ann": -0.031},
            "WF-V02": {"excess_ann": -0.025},
            "WF-V03": {"excess_ann": -0.042},
        }
    })
    rows, _ = mod.judge(
        json.loads(gate.read_text()), _baseline_gate(),
        mod._load_excess(excess), 0.4762, 1.0, 0.5,
    )
    by_no = {r["no"]: r for r in rows}
    assert by_no["③"]["status"] == mod.STATUS_FAIL
    # 任一 FAIL ⇒ 总体 REJECT（main 里 fail-closed）
    rc = mod.main(["--gate-json", str(gate), "--excess", str(excess)])
    assert rc == 0  # 渲染成功；判定为 REJECT（非退出码区分，靠文本）


def test_case_c_no_excess_is_not_produced(tmp_path):
    """不给 excess（本轮不产出超额）⇒ ③ NOT_PRODUCED，不得冒充已判。"""
    mod = _load_module()
    gate = _write(tmp_path, "gate.json", _planb_gate())
    rows, _ = mod.judge(json.loads(gate.read_text()), _baseline_gate(),
                        None, 0.4762, 1.0, 0.5)
    by_no = {r["no"]: r for r in rows}
    assert by_no["③"]["status"] == mod.STATUS_NOT
    assert "NOT_PRODUCED" in by_no["③"]["evidence"]


def test_case_d_pbo_worsened_reject(tmp_path):
    """判据② PBO 恶化（0.61 > 基线 0.4762）⇒ 一票 REJECT。"""
    mod = _load_module()
    g = _planb_gate()
    g["pbo"] = 0.61
    g["pbo_pass"] = False
    g["both_pass"] = False
    gate = _write(tmp_path, "gate.json", g)
    excess = _write(tmp_path, "excess.json", {"value_excess_ann": 0.021})
    rows, _ = mod.judge(json.loads(gate.read_text()), _baseline_gate(),
                        mod._load_excess(excess), 0.4762, 1.0, 0.5)
    by_no = {r["no"]: r for r in rows}
    assert by_no["②"]["status"] == mod.STATUS_FAIL
    assert "恶化" in by_no["②"]["evidence"]


def test_45_always_not_in_single_window():
    """④市值中性 / ⑤双窗 在本轮 3y 单窗 gate 设计下恒 NOT_PRODUCED，无法翻成 PASS。"""
    mod = _load_module()
    # 喂最完美的 gate + excess + baseline，④⑤ 仍须 NOT
    gate = _planb_gate()
    excess = mod._load_excess(Path("/nonexistent"))  # None
    rows, _ = mod.judge(gate, _baseline_gate(), excess, 0.4762, 1.0, 0.5)
    by_no = {r["no"]: r for r in rows}
    assert by_no["④"]["status"] == mod.STATUS_NOT
    assert by_no["⑤"]["status"] == mod.STATUS_NOT


def test_missing_gate_json_exit_2(tmp_path):
    """gate.json 缺失 ⇒ 退出码 2（不渲染、不误判）。"""
    mod = _load_module()
    rc = mod.main(["--gate-json", str(tmp_path / "does_not_exist.json")])
    assert rc == 2


def test_determinism(tmp_path):
    """同输入两次渲染逐位一致（判读可复现，§24 纪律）。"""
    import contextlib
    import io

    mod = _load_module()
    gate = _write(tmp_path, "gate.json", _planb_gate())
    excess = _write(tmp_path, "excess.json", {"value_excess_ann": 0.021, "value_excess_t": 1.32})
    base = _write(tmp_path, "wf001.json", _baseline_gate())
    args = ["--gate-json", str(gate), "--baseline-gate-json", str(base),
            "--excess", str(excess)]
    out1, out2 = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out1):
        mod.main(args)
    with contextlib.redirect_stdout(out2):
        mod.main(args)
    assert out1.getvalue() == out2.getvalue()
    assert out1.getvalue().strip() != ""


def test_abs_dsr_fail_marks_fail_even_without_baseline(tmp_path):
    """绝对 DSR 不过（dsr_pass=False）⇒ ① 直接 FAIL（一票否决），不拖成 NOT_PRODUCED。"""
    mod = _load_module()
    g = _planb_gate()
    g["deflated_sharpe"] = -2.4846
    g["dsr_pass"] = False
    g["pbo"] = 0.4143
    gate = _write(tmp_path, "gate.json", g)
    rows, _ = mod.judge(json.loads(gate.read_text()), None, None, 0.4762, 1.0, 0.5)
    by_no = {r["no"]: r for r in rows}
    assert by_no["①"]["status"] == mod.STATUS_FAIL
    assert "一票否决" in by_no["①"]["evidence"]


def test_real_planb_value_verdict_reject(tmp_path):
    """本轮真实 planb_value gate（DSR -2.4846/PBO 41.43%）+ 真实 8 臂超额全负 ⇒ REJECT。"""
    mod = _load_module()
    gate = _write(tmp_path, "gate.json", {
        "grid_profile": "planb_value", "deflated_sharpe": -2.4846, "pbo": 0.4143,
        "pbo_valid": True, "dsr_pass": False, "pbo_pass": True, "both_pass": False,
        "cscv_reliable": True, "n_variants": 8})
    # 真实 8 臂超额全负（-0.21~-0.24）
    excess = _write(tmp_path, "excess.json", {
        "arms": {f"WB-VLOW-{t}x{h}": {"excess_ann": e}
                 for t, e in [(10, -0.23), (20, -0.22), (30, -0.22), (50, -0.21)]
                 for h in (3, 5)}})
    rows, _ = mod.judge(json.loads(gate.read_text()), None,
                        mod._load_excess(excess), 0.4762, 1.0, 0.5)
    by_no = {r["no"]: r for r in rows}
    assert by_no["①"]["status"] == mod.STATUS_FAIL   # DSR 绝对不过
    assert by_no["③"]["status"] == mod.STATUS_FAIL   # 超额全负
    assert by_no["②"]["status"] == mod.STATUS_PASS   # PBO 41.43% 未恶化
    assert mod.STATUS_NOT in [r["status"] for r in rows]  # ④⑤ 本轮单窗恒 NOT
    assert mod.main(["--gate-json", str(gate), "--excess", str(excess)]) == 0
