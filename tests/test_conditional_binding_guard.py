"""守卫：``run_walkforward`` 内不得出现「条件赋值 + 无条件读取」的局部变量。

背景（2026-10-08 实测，P0，已修）：
``run_walkforward`` 里 ``fetch_result`` 只在 ``if streaming_context is None:`` 分支内赋值，
而报告段（**同一函数尾部**）无条件读 ``fetch_result.pit_note``。生产 gate cron 走的正是
``--streaming``（架构上强制 ``--skip-pit-financials``），该分支永不执行 ⇒ 必然
``UnboundLocalError``，且崩在「全部臂 + DSR/PBO 都算完、写 report.md 之前」——
lab 白费约 81min，prod 门禁白费约 9.8h。

行为侧契约已由 ``test_walkforward_streaming_report.py`` 守住（真跑一次、断言产出报告）；
本文件补的是**同类复发**的结构性守卫：只要有人再往 ``run_walkforward`` 里加
「只在某个分支里赋值、之后却无条件读取」的局部名，这里就会红。

分析器口径（保守，只报「必然未绑定」）：
- 只检查**函数体顶层**必然求值的读取（不进入 if/for/try 的「体」、不进入嵌套函数与推导式作用域）；
- 「必然绑定」按 definite-assignment 近似：if/else 取交集、终止的分支不参与、
  try 取「主路径 ∩ 存活 handler」、for/while 体不算必然；
- ⚠️ 已知盲区：不做**条件相关性**分析（如 ``if flag: x = f()`` 紧接 ``if flag and x > 0``
  实际安全，但会被报出来）。目前 ``run_walkforward`` 上实测零假阳性；
  **未推广到全仓** —— 其余函数仍有同类「依赖早期 return 兜底」的结构会误报。
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "src" / "aqsp" / "cli.py"


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
