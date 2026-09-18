"""CI 的触发路径必须覆盖「被代码和测试消费的配置目录」。

2026-09-18：CI 的 `paths` 过滤器漏了 `config/**`，而
`config/thresholds.yaml`（驱动策略阈值，AGENTS §3.5 要求改它必须升 version 并附说明）
与 `config/data_sources.yaml`（驱动 `source_catalog` 的校验）都被 **11 个测试文件**读取。

后果：**纯 config 改动能零 CI 直接合进 main** —— 测试就在仓库里，只是永远不会被触发。

这条测试守住那个缺口。加新的顶层目录时，如果它被代码消费，就必须同时加进这里。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"

# 被代码/测试消费、因此必须触发 CI 的目录
CONSUMED_PATHS = ("src/**", "scripts/**", "tests/**", "config/**")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _paths(workflow: dict, event: str) -> list[str]:
    # YAML 会把裸 `on:` 解析成布尔键 True，这里兼容两种写法
    triggers = workflow.get("on") or workflow.get(True)
    assert isinstance(triggers, dict), "ci.yml 缺少 on: 段"
    return list((triggers.get(event) or {}).get("paths") or [])


@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_ci_triggers_on_consumed_config_dirs(workflow: dict, event: str) -> None:
    paths = _paths(workflow, event)

    missing = [p for p in CONSUMED_PATHS if p not in paths]

    assert not missing, (
        f"ci.yml 的 {event}.paths 漏了 {missing} —— "
        "这些目录被代码/测试消费，漏掉会让纯配置改动能零 CI 合进 main"
    )


def test_ci_still_runs_the_full_verify_job(workflow: dict) -> None:
    """paths 过滤之外，verify 这个 job 本身不能被删掉或改名。"""
    jobs = workflow.get("jobs") or {}

    assert "verify" in jobs, "ci.yml 缺少 verify job"


def test_consumed_config_files_live_under_a_ci_triggered_dir() -> None:
    """被测试读取的配置文件必须落在 CI 会触发的目录下。"""
    consumed = (
        PROJECT_ROOT / "config" / "thresholds.yaml",
        PROJECT_ROOT / "config" / "data_sources.yaml",
    )

    for path in consumed:
        assert path.exists(), f"缺少被消费的配置文件: {path}"
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        assert any(
            relative.startswith(pattern.split("/**")[0] + "/")
            for pattern in CONSUMED_PATHS
        ), f"{relative} 不在任何 CI 触发目录下"
