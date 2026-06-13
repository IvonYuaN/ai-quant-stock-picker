from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_RUNTIME_ROOTS = (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts")
SHELL_RUNTIME_ROOT = PROJECT_ROOT / "scripts"

ALLOWED_DATETIME_NOW = {
    Path("src/aqsp/core/time.py"),
}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_RUNTIME_ROOTS:
        files.extend(root.rglob("*.py"))
    return sorted(files)


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
