"""守卫：`--streaming` 的 walkforward 跑批**必须真的产出 report.md**。

背景（2026-10-08 实测，P0）：
``run_walkforward`` 里 ``fetch_result`` 只在 ``if streaming_context is None:`` 分支内赋值，
而报告段（**同一函数的尾部**）无条件读 ``fetch_result.pit_note``。生产 gate cron 走的
正是 ``--streaming``（架构上强制 ``--skip-pit-financials``，见 cli.py:3691），该分支
永不执行 ⇒ 必然
``UnboundLocalError: local variable 'fetch_result' referenced before assignment``。

危害在于**崩点位置**：全部臂 + DSR/PBO 都算完、写 ``report.md`` 之前。
- lab：8 臂约 81min 算完后白费，``report.md`` 从未生成；
- prod：每周六 01:00 的门禁会跑满约 9.8h 后崩，DSR/PBO 全部作废、``RESULT_READY`` 不更新。

修法是 ``fetch_result: Any = None`` 预初始化 —— ``getattr(None, "pit_note", None)`` 得
``None``，自动落入既有的 ``skip_pit_financials`` 兜底分支，报告照常生成、行为零变化。

为何要新开文件：既有的 ``test_report_pit_skip_disclosure.py`` 只做**源码文本**断言，
抓不到运行时作用域错误；而本缺陷的形态就是「跑完才崩」，必须真跑一次才能守住。
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import aqsp.cli as cli_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "src" / "aqsp" / "cli.py"

#: 报告里 PIT 跳过披露的固定开头（cli.py 报告段与 test_report_pit_skip_disclosure 同源）。
PIT_SKIP_MARKER = "本次跑批跳过 PIT 财务数据"


# --------------------------------------------------------------------------- #
# 跑批夹具
# --------------------------------------------------------------------------- #
def _parse_walkforward_args(monkeypatch: pytest.MonkeyPatch, *extra: str):
    """借真实 argparse 取一份完整 args（避免手搓 Namespace 随参数增删而漂移）。

    做法：把 ``run_walkforward`` 换成捕获器跑一次 ``main()``，拿到解析结果后再还原。
    """
    captured: dict[str, Any] = {}
    real = cli_mod.run_walkforward
    monkeypatch.setattr(
        cli_mod,
        "run_walkforward",
        lambda args: (captured.setdefault("args", args), 0)[1],
    )
    rc = cli_mod.main(list(extra))
    monkeypatch.setattr(cli_mod, "run_walkforward", real)
    assert rc == 0, "参数捕获阶段不应执行真正的 walkforward"
    return captured["args"]


def _stub_result() -> SimpleNamespace:
    """报告段会读到的全部 result 属性（多给无害，少给会 AttributeError）。"""
    overall = SimpleNamespace(
        total_return=0.05,
        annual_return=0.06,
        max_drawdown=-0.10,
        sharpe_ratio=1.20,
        win_rate=0.55,
        profit_factor=1.30,
        trades=40,
        not_executable=2,
        sharpe_ratio_active=1.20,
        win_rate_active=0.55,
    )
    period = SimpleNamespace(
        period="2024-01",
        total_return=0.01,
        sharpe_ratio=1.0,
        win_rate=0.5,
        trades=5,
        not_executable=0,
        skipped=False,
    )
    return SimpleNamespace(
        overall=overall,
        periods=[period],
        regime_winrates={},
        deflated_sharpe=1.5,
        pbo=0.3,
        robustness_score=0.8,
        parameter_std=0.01,
    )


class _StubEngine:
    """只实现 ``run`` / ``run_streaming`` —— 走到它们就够，不做真回测。"""

    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result
        self.calls: list[str] = []

    def run(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        self.calls.append("run")
        return self._result

    def run_streaming(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        self.calls.append("run_streaming")
        return self._result


def _patch_engine(monkeypatch: pytest.MonkeyPatch, result: SimpleNamespace) -> _StubEngine:
    engine = _StubEngine(result)
    resolution = SimpleNamespace(
        requested="builtin", resolved="builtin", mode="builtin", message="stub"
    )
    monkeypatch.setattr(
        cli_mod, "resolve_walkforward_engine", lambda _name: (engine, resolution)
    )
    # 不写真实 gate sidecar（避免污染仓库路径）
    monkeypatch.setattr(cli_mod, "_write_walkforward_gate", lambda **_kwargs: None)
    return engine


# --------------------------------------------------------------------------- #
# 1) 回归：`--streaming` 跑批必须产出 report.md
# --------------------------------------------------------------------------- #
def test_streaming_walkforward_writes_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--streaming`` 跑完必须落地 report.md（2026-10-08 就是崩在这一步之前）。"""
    report = tmp_path / "report.md"
    args = _parse_walkforward_args(
        monkeypatch,
        "walkforward",
        "--streaming",
        "--engine",
        "builtin",
        "--source",
        "sqlite_db",
        "--skip-pit-financials",
        "--symbols",
        "600000,600001",
        "--start",
        "2024-01-02",
        "--end",
        "2024-12-31",
        "--report",
        str(report),
        "--gate-path",
        str(tmp_path / "gate.json"),
    )
    assert args.streaming is True, "本用例的前提是走 --streaming 分支"

    monkeypatch.setattr(
        cli_mod,
        "_build_streaming_sqlite_context",
        lambda _args, symbols: (symbols, lambda *_a, **_k: {}, [], {}, {}),
    )
    engine = _patch_engine(monkeypatch, _stub_result())

    rc = cli_mod.run_walkforward(args)

    assert rc == 0
    assert engine.calls == ["run_streaming"], "streaming 路径必须走 run_streaming"
    assert report.exists(), (
        "`--streaming` 跑批没有产出 report.md —— 典型原因是报告段读到了只在 "
        "`if streaming_context is None:` 分支里赋值的变量（如 fetch_result），"
        "在 streaming 路径下会抛 UnboundLocalError 并崩在写报告之前。"
    )
    assert "Walk-Forward 回测报告" in report.read_text(encoding="utf-8")


def test_streaming_report_keeps_pit_skip_disclosure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """预初始化不得吞掉披露：``fetch_result=None`` 时仍要落到 ``skip_pit_financials`` 兜底。

    ``--streaming`` 架构上强制跳过 PIT 财务 ⇒ quality/value/mean_reversion 三维恒为常数，
    报告必须如实标注。这条同时证明「预初始化」没有把披露一起吃掉。
    """
    report = tmp_path / "report.md"
    args = _parse_walkforward_args(
        monkeypatch,
        "walkforward",
        "--streaming",
        "--engine",
        "builtin",
        "--source",
        "sqlite_db",
        "--skip-pit-financials",
        "--symbols",
        "600000",
        "--start",
        "2024-01-02",
        "--end",
        "2024-12-31",
        "--report",
        str(report),
        "--gate-path",
        str(tmp_path / "gate.json"),
    )
    monkeypatch.setattr(
        cli_mod,
        "_build_streaming_sqlite_context",
        lambda _args, symbols: (symbols, lambda *_a, **_k: {}, [], {}, {}),
    )
    _patch_engine(monkeypatch, _stub_result())

    assert cli_mod.run_walkforward(args) == 0
    body = report.read_text(encoding="utf-8")
    assert PIT_SKIP_MARKER in body
    for dim in ("quality", "value", "mean_reversion"):
        assert dim in body, f"披露未点名空转维度 {dim}"


# --------------------------------------------------------------------------- #
# 2) 对照：非 streaming 路径的「运行时事实优先」契约不被预初始化破坏
# --------------------------------------------------------------------------- #
def _daily_frame(bars: int = 150) -> pd.DataFrame:
    close = pd.Series(range(1000, 1000 + bars), dtype=float)
    return pd.DataFrame(
        {
            "date": [d.date().isoformat() for d in pd.bdate_range("2024-01-02", periods=bars)],
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
            "amount": close * 1_000_000,
        }
    )


def test_non_streaming_report_prefers_runtime_pit_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """非 streaming 路径：``fetch_result.pit_note`` 是**运行时真实事实**，优先于 args 开关。"""
    report = tmp_path / "report.md"
    args = _parse_walkforward_args(
        monkeypatch,
        "walkforward",
        "--engine",
        "builtin",
        "--source",
        "sqlite_db",
        "--symbols",
        "600000",
        "--start",
        "2024-01-02",
        "--end",
        "2024-12-31",
        "--report",
        str(report),
        "--gate-path",
        str(tmp_path / "gate.json"),
    )
    assert getattr(args, "streaming", False) is False

    frames = {"600000": _daily_frame()}
    from aqsp.services import walkforward_data as wd

    monkeypatch.setattr(
        wd,
        "fetch_walkforward_frames",
        lambda *_a, **_k: SimpleNamespace(
            frames=frames, symbols=["600000"], pit_note="运行时事实：本次已跳过 PIT"
        ),
    )
    _patch_engine(monkeypatch, _stub_result())

    assert cli_mod.run_walkforward(args) == 0
    body = report.read_text(encoding="utf-8")
    assert "运行时事实：本次已跳过 PIT" in body, (
        "报告应优先采用 fetch_result.pit_note（运行时真实事实），而不是 args 开关假设"
    )
    assert PIT_SKIP_MARKER not in body, "有真实 pit_note 时不应再打印 args 兜底假设"


# --------------------------------------------------------------------------- #
# 3) 结构性守卫：同函数内不得「条件赋值 + 无条件读取」
# --------------------------------------------------------------------------- #
NESTED_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
COMP_CONTAINERS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
BODY_FIELDS = {"body", "orelse", "finalbody", "handlers", "cases"}
ASSIGN_STMTS = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Import, ast.ImportFrom)


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for elt in target.elts:
            out |= _target_names(elt)
        return out
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return set()


def _direct_binds(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, ast.Assign):
        out: set[str] = set()
        for target in stmt.targets:
            out |= _target_names(target)
        return out
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        return _target_names(stmt.target)
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in stmt.names}
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return _target_names(stmt.target)
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.ExceptHandler):
        return {stmt.name} if stmt.name else set()
    return set()


def _scope_binds(node: ast.AST) -> set[str]:
    """node 所在函数作用域内被绑定过的名字（不进入嵌套作用域 / 推导式作用域）。"""
    found: set[str] = set()

    def walk(cur: ast.AST) -> None:
        nonlocal found
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, NESTED_SCOPE):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    found.add(child.name)
                continue
            if isinstance(child, ast.comprehension):
                walk(child.iter)
                continue
            if isinstance(child, ast.withitem):
                if child.optional_vars is not None:
                    found |= _target_names(child.optional_vars)
            else:
                found |= _direct_binds(child)
            walk(child)

    walk(node)
    for child in ast.walk(node):
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            found -= set(child.names)
    return found


def _block_terminates(block: list[ast.stmt]) -> bool:
    """该块是否必然以 return/raise/break/continue 收尾。"""
    if not block:
        return False
    last = block[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.If):
        return bool(last.orelse) and _block_terminates(last.body) and _block_terminates(last.orelse)
    if isinstance(last, ast.With):
        return _block_terminates(last.body)
    if isinstance(last, ast.Try):
        return _block_terminates(last.body) and all(
            _block_terminates(h.body) for h in last.handlers
        )
    return False


def _definite_block(block: list[ast.stmt]) -> set[str]:
    out: set[str] = set()
    for stmt in block:
        out |= _definite_binds(stmt)
    return out


def _definite_binds(stmt: ast.stmt) -> set[str]:
    """stmt 执行完后**必然**已绑定的名字（保守：拿不准就少报）。"""
    if isinstance(stmt, ast.If):
        if not stmt.orelse:
            return set()
        live = [b for b in (stmt.body, stmt.orelse) if not _block_terminates(b)]
        if not live:
            return set()
        inter = _definite_block(live[0])
        for block in live[1:]:
            inter &= _definite_block(block)
        return inter
    if isinstance(stmt, ast.Try):
        out = _definite_block(stmt.finalbody)
        main = _definite_block(stmt.body) | _definite_block(stmt.orelse)
        if not stmt.handlers:
            return out | main
        survivors = [h for h in stmt.handlers if not _block_terminates(h.body)]
        if not survivors:
            return out | main
        inter = _definite_block(survivors[0].body)
        for handler in survivors[1:]:
            inter &= _definite_block(handler.body)
        return out | (main & inter)
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        return _definite_block(stmt.orelse)
    if isinstance(stmt, ast.With):
        out: set[str] = set()
        for item in stmt.items:
            if item.optional_vars is not None:
                out |= _target_names(item.optional_vars)
        return out | _definite_block(stmt.body)
    if isinstance(stmt, ast.Match):
        return set()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ASSIGN_STMTS):
        return _direct_binds(stmt)
    return set()


def _unconditional_reads(node: ast.AST, acc: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """node 执行时**必然求值**的名字读取（不进入复合语句体 / 嵌套作用域）。"""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        acc.append((node.id, node.lineno))
        return acc
    if isinstance(node, NESTED_SCOPE):
        return acc
    if isinstance(node, COMP_CONTAINERS):
        if node.generators:
            _unconditional_reads(node.generators[0].iter, acc)
        return acc
    for field, value in ast.iter_fields(node):
        if field in BODY_FIELDS:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, ast.AST):
                _unconditional_reads(item, acc)
    return acc


def _conditional_binding_violations(source: str, func_name: str) -> list[tuple[int, str]]:
    """列出「在函数体顶层被必然读取、但此刻尚未被无条件绑定」的局部名。"""
    tree = ast.parse(source)
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == func_name
    )
    params = {a.arg for a in func.args.args + func.args.kwonlyargs + func.args.posonlyargs}
    if func.args.vararg:
        params.add(func.args.vararg.arg)
    if func.args.kwarg:
        params.add(func.args.kwarg.arg)

    local_any = _scope_binds(func) | params
    builtin_names = set(dir(builtins))
    bound = set(params)
    violations: list[tuple[int, str]] = []
    for stmt in func.body:
        for name, lineno in _unconditional_reads(stmt, []):
            if name in builtin_names or name not in local_any:
                continue
            if name not in bound:
                violations.append((lineno, name))
        bound |= _definite_binds(stmt)
    return violations


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        # 缺陷形态：只在 if 分支里赋值，之后无条件读
        (
            "def f(flag):\n"
            "    if flag:\n"
            "        x = 1\n"
            "    return x\n",
            ["x"],
        ),
        # 安全：if/else 两边都赋值
        (
            "def f(flag):\n"
            "    if flag:\n"
            "        x = 1\n"
            "    else:\n"
            "        x = 2\n"
            "    return x\n",
            [],
        ),
        # 安全：try 的 except 直接 return
        (
            "def f(raw):\n"
            "    try:\n"
            "        x = int(raw)\n"
            "    except ValueError:\n"
            "        return 0\n"
            "    return x\n",
            [],
        ),
        # 安全：推导式目标属于推导式自己的作用域
        (
            "def f(items):\n"
            "    total = sum(1 for item in items if item)\n"
            "    return total\n",
            [],
        ),
    ],
)
def test_binding_guard_is_not_vacuous(snippet: str, expected: list[str]) -> None:
    """先证明这个守卫本身有判别力（否则「零违规」可能只是空跑）。"""
    found = [name for _line, name in _conditional_binding_violations(snippet, "f")]
    assert found == expected


def test_run_walkforward_has_no_conditional_binding_violations() -> None:
    """``run_walkforward`` 内不得再有「条件赋值 + 无条件读取」。

    2026-10-08 的 P0（``fetch_result``）正是这一形态，代价是整轮 ~9.8h 跑批白费。
    ⚠️ 本守卫只覆盖 ``run_walkforward``：该分析器在此函数上实测零假阳性
    （其余函数里仍有少量「依赖早期 return 兜底」的结构，需先补条件相关性分析再推广）。
    """
    violations = _conditional_binding_violations(CLI.read_text(encoding="utf-8"), "run_walkforward")
    assert violations == [], (
        "run_walkforward 出现「只在条件分支里赋值、之后却无条件读取」的局部变量：\n"
        + "\n".join(f"  L{line}: {name}" for line, name in violations)
        + "\n\n修法：在分支之前无条件预初始化（如 `fetch_result: Any = None`）。"
        "否则 --streaming 路径会抛 UnboundLocalError，并崩在写 report.md 之前。"
    )
