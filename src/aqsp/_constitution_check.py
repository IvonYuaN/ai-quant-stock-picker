"""宪法启动门 — CONSTITUTION §8.1 落地。

声明已写但代码缺失：宪法 §8.1 要求 `aqsp` 任意子命令第一步跑
assert_constitution_invariants()，但 src/aqsp/_constitution_check.py 此前不存在。
本文件补上这道门。任一不变量不满足 → SystemExit（fail loud，对应 §1.3 #6）。
"""

from __future__ import annotations

import sys
import ast
from pathlib import Path


class ConstitutionViolation(SystemExit):
    """宪法不变量被破坏。继承 SystemExit，启动即终止。"""

    def __init__(self, clause: str, detail: str) -> None:
        super().__init__(
            f"[CONSTITUTION VIOLATION] {clause}: {detail}\n"
            f"  参见 docs/CONSTITUTION.md。启动门拒绝继续。"
        )


# ---------------------------------------------------------------------------
# #15: thresholds.yaml 元数据完整性
# ---------------------------------------------------------------------------
_REQUIRED_THRESHOLDS_META = ("version", "effective_from", "last_walkforward_run")


def _check_thresholds_metadata() -> None:
    """#15：thresholds.yaml 必须有 version / effective_from / last_walkforward_run，
    且三者非空。缺失或空 → 违宪。

    注意：本检查只读 yaml 原文，不经 load_thresholds() 的默认值填充，
    避免"文件里其实没写、但 dataclass 默认值补上了"的假阴性。
    """
    import yaml

    # 从 __file__ 计算项目根目录：src/aqsp/_constitution_check.py → src/aqsp → src → (项目根)
    project_root = Path(__file__).resolve().parent.parent.parent
    path = project_root / "config" / "thresholds.yaml"
    if not path.exists():
        # 如果失败，尝试从当前工作目录找
        path = Path("config") / "thresholds.yaml"
    if not path.exists():
        raise ConstitutionViolation(
            "§1.3 #15",
            f"config/thresholds.yaml 不存在（尝试了 {path}）",
        )

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    missing = [k for k in _REQUIRED_THRESHOLDS_META if not data.get(k)]
    if missing:
        raise ConstitutionViolation(
            "§1.3 #15",
            f"thresholds.yaml 缺少或为空的元数据字段: {missing}",
        )


# ---------------------------------------------------------------------------
# #4: 核心路径不允许顶层硬 import LLM SDK
# ---------------------------------------------------------------------------
_FORBIDDEN_TOP_LEVEL_LLM = ("anthropic", "openai")


def _check_no_top_level_llm_import() -> None:
    """#4：LLM 是增强不是必需。

    这里不再依赖 `sys.modules`，避免被外部运行环境或其他启动器污染。
    直接静态扫描 aqsp 源码，禁止在模块顶层 `import openai/anthropic`。
    函数体内惰性 import 允许通过。
    """
    project_root = Path(__file__).resolve().parent.parent
    violations: list[str] = []
    for path in project_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [str(node.module or "").split(".", 1)[0]]
            else:
                continue
            hit = [name for name in names if name in _FORBIDDEN_TOP_LEVEL_LLM]
            if hit:
                violations.append(f"{path.relative_to(project_root.parent)}:{','.join(hit)}")
    if violations:
        raise ConstitutionViolation(
            "§1.3 #4",
            "检测到顶层 LLM import: " + "; ".join(violations[:8]),
        )


# ---------------------------------------------------------------------------
# #10/#11: 阶段 1 仅 A 股，禁止引入港股/美股 module
# ---------------------------------------------------------------------------
_PHASE2_FORBIDDEN_MODULES = (
    "aqsp.data.hk",
    "aqsp.data.us",
    "aqsp.region",
)


def _check_phase1_scope() -> None:
    """#10/#11：阶段 1 仅 A 股。若检测到阶段 2 才允许的 region module 已被
    导入，违宪。本检查只看 sys.modules，不主动 import（不制造副作用）。
    """
    leaked = [m for m in _PHASE2_FORBIDDEN_MODULES if m in sys.modules]
    if leaked:
        raise ConstitutionViolation(
            "§1.3 #10/#11",
            f"阶段 1 仅 A 股，但检测到阶段 2 region module 已加载: {leaked}",
        )


def assert_constitution_invariants() -> None:
    """启动时强制检查宪法不变量。任意一条不满足 → SystemExit。

    调用点：cli.main() 解析 argv 后、分发子命令前。
    顺序：先元数据（最常见的漂移），再 import 约束，最后 scope。
    """
    _check_thresholds_metadata()
    _check_no_top_level_llm_import()
    _check_phase1_scope()


if __name__ == "__main__":
    # 直接跑：python -m aqsp._constitution_check → 不抛即通过
    assert_constitution_invariants()
    print(
        "✅ 宪法不变量检查通过（thresholds 元数据 / 无顶层 LLM import / 阶段1 scope）"
    )
