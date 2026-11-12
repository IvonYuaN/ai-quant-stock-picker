from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_RUNTIME_ROOTS = (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts")
SHELL_RUNTIME_ROOT = PROJECT_ROOT / "scripts"
STRATEGY_ROOT = PROJECT_ROOT / "src" / "aqsp" / "strategies"

ALLOWED_DATETIME_NOW = {
    Path("src/aqsp/core/time.py"),
}
ALLOWED_DATE_TODAY = {
    Path("src/aqsp/core/time.py"),
}

# 🔴 2026-11-10：离线**研究标签**脚本的负向 shift 豁免（窄口径、白名单式）。
#
# 本守卫的意图是防「**运行时策略用未来数据选股**」的前视。而离线研究脚本
# 需要构造**评估标签**（前向收益 fwd5/fwd10/fwd20）来事后评价「已选出的股票」，
# 其 `shift(-h)` 取的是**未来价格做标签**，不参与任何选股决策 ⇒ 方法论正当、
# 与前视无关（标签用的未来价在选股时不可见，正因如此它才是有效的评估标签）。
#
# 为什么必须显式列出而不是按目录豁免：按目录豁免会把整个 scripts/research/
# 变成免检区（将来有人在那里写真前视也查不出来）⇒ 白名单式最小豁免，
# 与本文件既有的 ALLOWED_DATETIME_NOW / ALLOWED_DATE_TODAY 同构。
#
# ⚠️ 豁免**只针对负向 shift**；`rolling(center=True)` 连标签场景也是真前视，
#    仍然对这些文件生效（见 test 内的判定顺序）。
# ⚠️ 豁免成立的前提 = 这些脚本不在运行时 import 图内（否则等于放前视进生产）
#    ⇒ 由 test_research_label_scripts_are_not_in_runtime_import_graph 守卫。
ALLOWED_LOOKAHEAD_LABEL_SCRIPTS = {
    Path("scripts/research/holder_concentration_verify.py"),
    Path("scripts/research/holder_diagnostic.py"),
    Path("scripts/research/holder_overlay_verify.py"),
}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_RUNTIME_ROOTS:
        files.extend(root.rglob("*.py"))
    return sorted(files)


def _strategy_files() -> list[Path]:
    return sorted(STRATEGY_ROOT.glob("*.py"))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def _is_call_name(node: ast.AST, dotted_name: str) -> bool:
    parts = dotted_name.split(".")
    current: ast.AST = node
    for part in reversed(parts):
        if isinstance(current, ast.Attribute) and current.attr == part:
            current = current.value
            continue
        if isinstance(current, ast.Name) and current.id == part:
            return True
        return False
    return False


def _relative(path: Path) -> Path:
    return path.relative_to(PROJECT_ROOT)


def _is_negative_expr(node: ast.AST | None) -> bool:
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)


def test_runtime_python_uses_project_clock_for_current_time() -> None:
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = _relative(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_call_name(node.func, "datetime.now") or _is_call_name(
                node.func, "datetime.utcnow"
            ):
                if rel not in ALLOWED_DATETIME_NOW:
                    offenders.append(f"{rel}:{node.lineno}")
            if _is_call_name(node.func, "date.today") or _is_call_name(
                node.func, "datetime.today"
            ):
                if rel not in ALLOWED_DATE_TODAY:
                    offenders.append(f"{rel}:{node.lineno}")

    assert offenders == []


def test_runtime_python_does_not_spawn_shell_commands() -> None:
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = _relative(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_call_name(node.func, "os.system"):
                offenders.append(f"{rel}:{node.lineno}")
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    offenders.append(f"{rel}:{node.lineno}")

    assert offenders == []


def test_runtime_python_has_no_lookahead_shift_or_centered_rolling() -> None:
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = _relative(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr == "shift":
                first_arg = node.args[0] if node.args else None
                periods_arg = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "periods"
                    ),
                    None,
                )
                # 负向 shift = 用未来数据 ⇒ 运行时路径一律禁止；
                # 离线研究标签脚本走窄口径白名单豁免（见 ALLOWED_LOOKAHEAD_LABEL_SCRIPTS）。
                if (
                    rel not in ALLOWED_LOOKAHEAD_LABEL_SCRIPTS
                    and (_is_negative_expr(first_arg) or _is_negative_expr(periods_arg))
                ):
                    offenders.append(f"{rel}:{node.lineno}")
            if node.func.attr == "rolling":
                # ⚠️ center=True 连标签场景也是真前视 ⇒ 对**所有**文件（含豁免文件）生效
                for keyword in node.keywords:
                    if (
                        keyword.arg == "center"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        offenders.append(f"{rel}:{node.lineno}")

    assert offenders == []


def test_research_label_scripts_are_not_in_runtime_import_graph() -> None:
    """负向 shift 豁免的**成立前提**：豁免脚本不在运行时 import 图内。

    若这些离线研究脚本被 ``src/`` 或生产脚本 import，则它们的负向 shift
    就进入了运行时路径 ⇒ 豁免等于放前视进生产 ⇒ 必须中止。

    守卫只做「无人 import」的静态核对（不执行脚本）：扫 src/ 与 scripts/
    下所有 .py 的 import 语句，确认没有任何模块引用这些脚本。
    """
    import ast as _ast

    exempt = {p.name for p in ALLOWED_LOOKAHEAD_LABEL_SCRIPTS}
    assert exempt, "豁免名单为空（否则本守卫形同虚设）"

    offenders: list[str] = []
    for path in _python_files():
        if path.name in exempt:
            continue  # 豁免脚本彼此之间的直接引用不算运行时依赖
        tree = _ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _ast.walk(tree):
            names: list[str] = []
            if isinstance(node, _ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, _ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                leaf = name.rsplit(".", 1)[-1]
                if leaf in exempt:
                    offenders.append(f"{_relative(path)}:{node.lineno} -> {name}")

    assert offenders == [], (
        f"离线研究标签脚本被运行时路径 import：{offenders} ⇒ "
        "负向 shift 豁免已失效（等于放前视进生产）"
    )


def test_runtime_shell_scripts_do_not_use_shell_injection_helpers() -> None:
    offenders: list[str] = []

    for path in sorted(SHELL_RUNTIME_ROOT.rglob("*.sh")):
        rel = _relative(path)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "shell=True" in line or "os.system(" in line:
                offenders.append(f"{rel}:{lineno}")

    assert offenders == []


def test_strategy_evaluate_functions_do_not_access_io_or_network() -> None:
    banned_names = {
        "open",
        "Path",
        "pd.read_csv",
        "pd.read_excel",
        "pd.read_json",
        "requests.get",
        "requests.post",
        "requests.request",
        "subprocess.run",
        "subprocess.Popen",
    }
    banned_suffixes = (
        ".read_text",
        ".write_text",
        ".open",
        ".to_csv",
        ".to_excel",
        ".to_json",
    )
    offenders: list[str] = []

    for path in _strategy_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = _relative(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "evaluate":
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                call_name = _call_name(child.func)
                if call_name in banned_names or call_name.endswith(banned_suffixes):
                    offenders.append(f"{rel}:{child.lineno}:{call_name}")

    assert offenders == []
