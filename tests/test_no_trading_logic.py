"""红线守卫：本仓库 explicit **不下单**（AGENTS §5「把交易/下单逻辑塞进本仓库」）。

本仓是「只筛选、不下单」的选股工作台。任何把券商/交易 SDK 引入、或出现下单类调用的
改动，都必须在合并前被拒。此文件把该红线从「人工审查」变成 CI 守卫。

设计要点
--------
1. **AST 而非文本匹配**。`xtquant_qmt` / `vnpy` 等字样在本仓**合法出现**——它们要么是
   行情**数据源注册名**（`src/aqsp/data/registry.py`、`config/data_sources.yaml`），
   要么是「研究沙箱、不进入 runtime」的引用（`config/strategy_sources.yaml`），
   还有配置注释明写「adapter 不允许提供 place_order」。所以文本匹配会大量误报；
   本守卫**只看真实的 `import` 与函数调用**。
2. **可证伪（反空转）**。每个扫描器都配一个用 `tmp_path` 造违规样例的测试；且对仓库
   扫描断言「检查过的文件数」下限，避免文件列表意外为空时测试假装通过。
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 纳入扫描的 runtime 源码根（不含 gitignore 的 outputs/ 与 frontend/ 构建产物）
PYTHON_ROOTS = (
    PROJECT_ROOT / "src",
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "backend",
    PROJECT_ROOT / "tests",
    PROJECT_ROOT / "workbench",
)

# 至少应扫到这么多 .py 文件（防止文件列表意外为空导致守卫空转）
MIN_EXPECTED_PYTHON_FILES = 300

# 交易/券商 SDK 的顶层模块名（归一化后比较：``_`` 与 ``.`` 视同 ``-``）
BANNED_IMPORT_ROOTS = frozenset(
    {
        "easytrader",
        "xtquant",  # 迅投 QMT 交易端（行情侧以 xtquant_qmt 数据源名出现，属字符串）
        "xttrader",
        "miniqmt",
        "vnpy",
        "vnpy-ctp",
        "futu",
        "ib-insync",
        "ibapi",
    }
)

# 下单类函数名（按调用的**末段**匹配，如 ``broker.place_order(...)``）
BANNED_ORDER_CALLS = frozenset(
    {
        "place_order",
        "submit_order",
        "send_order",
        "insert_order",
        "cancel_order",
        "cancel_all_orders",
        "order_stock",
        "order_target",
        "order_value",
        "order_percent",
        "order_target_percent",
        "order_target_value",
    }
)


def _normalize(name: str) -> str:
    """剥离分隔符后比较：``easy_trader`` / ``easy-trader`` / ``Easy.Trader`` 视同同一名。

    用「删除」而非「统一成 `-`」：后者会把 ``easy_trader`` 变成 ``easy-trader``，
    与黑名单里的 ``easytrader`` 并不相等，**下划线变体反而漏检**。
    """
    for sep in ("_", "-", "."):
        name = name.replace(sep, "")
    return name.strip().lower()


def _python_files(roots: Iterable[Path] = PYTHON_ROOTS) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(root.rglob("*.py"))
    return sorted(set(files))


def _gitignored_top_level_names(
    gitignore: Path | None = None,
) -> set[str]:
    """从 ``.gitignore`` 推导「顶层名字」排除集。

    gitignored 的目录在 CI 的干净 checkout 上**根本不存在**，把它们纳入扫描会让
    「本地跑」与「CI 跑」结果不一致（本地多余文件导致假失败）。故显式排除。
    """
    excluded = {".venv", "node_modules", "__pycache__"}
    path = gitignore or (PROJECT_ROOT / ".gitignore")
    if not path.is_file():
        return excluded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().rstrip("/")
        if not line or line.startswith("#") or "/" in line:
            continue
        if any(ch in line for ch in "*?["):
            continue
        excluded.add(line)
    return excluded


def find_uncovered_python_dirs(
    root: Path,
    covered: Iterable[str],
    excluded: Iterable[str],
) -> list[str]:
    """返回「含 .py 但既未被扫描根覆盖、也不在排除集」的顶层目录名。"""
    covered_set, excluded_set = set(covered), set(excluded)
    uncovered: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        if child.name in covered_set or child.name in excluded_set:
            continue
        if next(child.rglob("*.py"), None) is not None:
            uncovered.append(child.name)
    return uncovered


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def scan_forbidden_imports(files: Iterable[Path]) -> list[str]:
    """返回 import 了禁用交易 SDK 的位置清单（空列表 = 合规）。"""
    offenders: list[str] = []
    banned = {_normalize(item) for item in BANNED_IMPORT_ROOTS}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = _normalize(alias.name.split(".")[0])
                    if top in banned:
                        offenders.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                top = _normalize((node.module or "").split(".")[0])
                if top in banned:
                    offenders.append(
                        f"{path}:{node.lineno}: from {node.module} import ..."
                    )
    return offenders


def scan_forbidden_order_calls(files: Iterable[Path]) -> list[str]:
    """返回调用下单类函数的位置清单（空列表 = 合规）。"""
    offenders: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name.split(".")[-1] in BANNED_ORDER_CALLS:
                offenders.append(f"{path}:{node.lineno}: {name}()")
    return offenders


def _requirement_name(spec: str) -> str:
    """``easytrader[extra]>=1.0 ; python_version>='3'`` → ``easytrader``（归一化）。"""
    return _normalize(re.split(r"[<>=!~\[;\s(]", spec.strip(), maxsplit=1)[0])


def scan_trading_dependencies(pyproject: Path) -> list[str]:
    """返回 pyproject 里声明的交易类依赖（空列表 = 合规）。"""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project") or {}
    declared: list[str] = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        declared.extend(group)
    banned = {_normalize(item) for item in BANNED_IMPORT_ROOTS}
    return [spec for spec in declared if _requirement_name(spec) in banned]


# ---------------------------------------------------------------------------
# 仓库现状：三条都必须为空
# ---------------------------------------------------------------------------


def test_repo_python_does_not_import_trading_sdks() -> None:
    files = _python_files()
    assert len(files) >= MIN_EXPECTED_PYTHON_FILES, (
        f"只扫到 {len(files)} 个 .py 文件（预期 >= {MIN_EXPECTED_PYTHON_FILES}）"
        "—— 文件列表疑似为空，守卫会空转"
    )
    assert scan_forbidden_imports(files) == []


def test_repo_python_does_not_call_order_placement_apis() -> None:
    files = _python_files()
    assert len(files) >= MIN_EXPECTED_PYTHON_FILES
    assert scan_forbidden_order_calls(files) == []


def test_project_does_not_declare_trading_dependencies() -> None:
    assert scan_trading_dependencies(PROJECT_ROOT / "pyproject.toml") == []


def test_scan_roots_cover_every_python_source_dir() -> None:
    """新增含 .py 的顶层目录必须显式纳入扫描根，否则红线守卫会**静默漏扫**。"""
    uncovered = find_uncovered_python_dirs(
        PROJECT_ROOT,
        covered=[r.name for r in PYTHON_ROOTS],
        excluded=_gitignored_top_level_names(),
    )
    assert uncovered == [], (
        f"这些顶层目录含 .py 却不在 PYTHON_ROOTS：{uncovered}。"
        "若属 runtime 源码请加入 PYTHON_ROOTS；若应跳过，请写入 .gitignore 或排除集。"
    )


# ---------------------------------------------------------------------------
# 反空转：扫描器必须能真的抓到违规（否则上面的 assert == [] 毫无意义）
# ---------------------------------------------------------------------------


def test_import_scanner_detects_violation(tmp_path: Path) -> None:
    bad = tmp_path / "broker.py"
    bad.write_text(
        "import easytrader\nfrom xtquant import xttrader\n",
        encoding="utf-8",
    )
    offenders = scan_forbidden_imports(_python_files((tmp_path,)))
    assert len(offenders) == 2, offenders


def test_order_call_scanner_detects_violation(tmp_path: Path) -> None:
    bad = tmp_path / "broker.py"
    bad.write_text(
        "def go(broker, code):\n"
        "    broker.place_order(code, 100)\n"
        "    submit_order(code)\n"
        "    broker.query_positions()\n",
        encoding="utf-8",
    )
    offenders = scan_forbidden_order_calls(_python_files((tmp_path,)))
    assert len(offenders) == 2, offenders
    # 合法调用不得被误伤
    assert all("query_positions" not in item for item in offenders)


def test_dependency_scanner_detects_violation(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "x"\n'
        'version = "0"\n'
        'dependencies = ["pandas>=2.0", "easy_trader[all]>=1.0"]\n'
        "[project.optional-dependencies]\n"
        'broker = ["xtquant"]\n',
        encoding="utf-8",
    )
    offenders = scan_trading_dependencies(pyproject)
    assert len(offenders) == 2, offenders


def test_dependency_scanner_ignores_legitimate_market_data_deps(tmp_path: Path) -> None:
    """行情/研究类依赖（本项目实际在用的）不得被误伤。"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "x"\n'
        'version = "0"\n'
        'dependencies = ["pandas>=2.0", "baostock>=0.9.1", "akshare>=1.16", "tushare"]\n'
        "[project.optional-dependencies]\n"
        'dev = ["pytest>=8.0", "ruff>=0.8"]\n',
        encoding="utf-8",
    )
    assert scan_trading_dependencies(pyproject) == []


def test_coverage_checker_detects_uncovered_dir(tmp_path: Path) -> None:
    """覆盖率检查器必须能真的报出漏扫目录（否则上一条断言毫无意义）。"""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "c.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()  # 无 .py → 不应报出

    uncovered = find_uncovered_python_dirs(
        tmp_path, covered=["src"], excluded=["outputs"]
    )

    assert uncovered == ["lib"], uncovered


def test_gitignored_names_exclude_data_dirs(tmp_path: Path) -> None:
    """``.gitignore`` 里的目录名要能被识别为排除项（本地存在、CI 不存在）。"""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        "# comment\n"
        ".venv\n"
        "outputs/\n"
        "A股量化分析数据\n"
        "*.log\n"
        "some/nested/path\n",
        encoding="utf-8",
    )
    names = _gitignored_top_level_names(gitignore)

    assert {"outputs", "A股量化分析数据"} <= names
    assert ".venv" in names  # 内建默认项
    assert "*.log" not in names
    assert "some" not in names  # 含 "/" 的嵌套规则不是顶层名
