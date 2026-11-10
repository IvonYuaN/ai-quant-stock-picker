"""grid 档位的**路由与入口白名单必须同源**（防「新增档位漏网」复发）。

背景（2026-11-10 实测）
----------------------
档位路由的事实源在 ``aqsp.cli._walkforward_grid_variants``，但各 CLI 入口的
``--grid-profile`` 白名单是**手抄的 choices 元组** ⇒ **两处维护** ⇒
新增档位必然漏网。实证两个后果：

- ``htf_mr``（T3 方案 A）**长期不在任何入口白名单里** —— 只能靠
  ``scripts/runner/build_t3_gate_script.py`` 打补丁注入才能跑；
- ``holder_overlay``（股东户数筹码集中度验证档位）若照抄手抄列表，
  ``argparse`` 会直接拒绝 ⇒ 跑起来必失败，且报错信息（invalid choice）
  与真实原因（白名单没同步）严重错位。

根治：入口白名单改为**从 ``aqsp.cli.walkforward_grid_profiles()`` 派生**
⇒ 档位集合只有一个事实源；本文件把这条不变量锁死，防止有人再手抄一份回去。

另锁两条相邻不变量：
- 未知档位必须 **fail-closed**（旧实现静默回落 ``stable`` ⇒ 判读人以为
  跑的是 A 档、实跑的是 stable）；
- 每个入口脚本的源码里**不得残留**手抄的 choices 元组（第二份副本 = 病根）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from aqsp.cli import (
    _walkforward_grid_variants,
    walkforward_grid_profiles,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 所有带 ``--grid-profile`` 的入口脚本。
#: ``scripts/run_production_walkforward_gate.py`` 是
#: ``scripts/runner/run_production_walkforward_gate.py`` 的软链（同一份文件），
#: 故只取真身，避免同一文件被算两次。
GATE_ENTRY_SCRIPTS: tuple[Path, ...] = (
    REPO_ROOT / "scripts" / "runner" / "run_production_walkforward_gate.py",
    REPO_ROOT / "scripts" / "runner" / "run_dual_window_gate.py",
)

#: 手抄 choices 元组的特征（一旦出现即说明有人绕过单一事实源又抄了一份）。
_HAND_COPIED_CHOICES = re.compile(r'choices\s*=\s*\(\s*"stable"')


def _source_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_routable_profile_is_listed_by_the_single_source() -> None:
    """派生出的档位集合必须覆盖查表里的**全部**档位（不多不少）。"""
    from aqsp.cli import _WALKFORWARD_GRID_VARIANT_TABLE

    listed = set(walkforward_grid_profiles())
    assert listed == set(_WALKFORWARD_GRID_VARIANT_TABLE), (
        "walkforward_grid_profiles() 与档位查表不一致 —— 派生白名单会漏档位"
    )
    # 每个列出的档位都必须真能路由出变体（不是空集）
    for profile in listed:
        assert _walkforward_grid_variants(profile), f"{profile} 路由出空变体集"


def test_unknown_profile_fails_closed() -> None:
    """未知档位必须显式报错，不得静默回落 ``stable``。

    旧实现的 if 链末尾有 ``return _WALKFORWARD_STABLE_GRID_VARIANTS`` 兜底 ⇒
    拼错档位名会静默跑成 stable，而日志里写的却是错的那个档位名 ⇒
    取证结论错位且无人察觉。
    """
    with pytest.raises(ValueError, match="unknown grid profile"):
        _walkforward_grid_variants("definitely_not_a_profile")


@pytest.mark.parametrize("script", GATE_ENTRY_SCRIPTS, ids=lambda p: p.name)
def test_entry_script_derives_its_whitelist(script: Path) -> None:
    """入口脚本的白名单必须**派生**，不得手抄。"""
    source = _source_of(script)
    assert "choices=_grid_profile_choices()" in source, (
        f"{script.name}: --grid-profile 白名单未从单一事实源派生"
    )
    assert not _HAND_COPIED_CHOICES.search(source), (
        f"{script.name}: 源码里仍残留手抄的 choices 元组 —— "
        "存在第二份副本即「新增档位漏网」的风险"
    )


@pytest.mark.parametrize("script", GATE_ENTRY_SCRIPTS, ids=lambda p: p.name)
def test_entry_script_white_list_covers_all_profiles(script: Path) -> None:
    """入口脚本实际解析时必须接受**每一个**可路由档位（含历史漏网的 htf_mr）。"""
    source = _source_of(script)
    assert ast.parse(source) is not None  # 语法有效

    # 在脚本自己的命名空间里执行其 _grid_profile_choices()，取真实白名单。
    namespace: dict[str, object] = {}
    exec(  # noqa: S102 - 仅执行白名单派生函数，取其返回值
        compile(
            "\n".join(
                line
                for line in source.splitlines()
                if "def _grid_profile_choices" in line
                or line.strip().startswith("from aqsp.cli import walkforward_grid_profiles")
                or line.strip() == "return walkforward_grid_profiles()"
            ),
            str(script),
            "exec",
        ),
        namespace,
    )
    choices = namespace["_grid_profile_choices"]()  # type: ignore[operator]
    assert set(choices) == set(walkforward_grid_profiles()), (
        f"{script.name}: 入口白名单与单一事实源不一致"
    )
    # 关键回归点：这些档位历史上漏过，必须确保它们现在都被接受
    for profile in ("htf_mr", "holder_overlay", "stable_plus"):
        assert profile in choices, f"{script.name}: 档位 {profile} 仍被入口拒绝"
