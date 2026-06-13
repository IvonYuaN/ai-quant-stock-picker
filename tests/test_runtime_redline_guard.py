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
                if _is_negative_expr(first_arg) or _is_negative_expr(periods_arg):
                    offenders.append(f"{rel}:{node.lineno}")
            if node.func.attr == "rolling":
                for keyword in node.keywords:
                    if (
                        keyword.arg == "center"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        offenders.append(f"{rel}:{node.lineno}")

    assert offenders == []


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
