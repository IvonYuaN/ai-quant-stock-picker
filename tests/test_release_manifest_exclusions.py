"""守护 release manifest 生成器与一致性检查器的「排除集」不漂移。

背景（2026-09-28 实测踩坑）：`deploy_immutable_release.sh` 先 `stamp_manifest`
（算 `file_count` / `content_digest`）**再**写 `RELEASE_SHA`。因此：

- **首次**部署：manifest 计算时 `RELEASE_SHA` 尚不存在 ⇒ 两端一致，检查通过；
- **重跑**部署（release 目录已存在，例如上一次部署失败后重试）：`RELEASE_SHA`
  已存在 ⇒ 生成器若把它算进去、而检查器又排除它，就相差 1 个文件 ⇒
  `Release consistency FAILED: file_count 16662 != 16661` + `content_digest_mismatch`
  ⇒ 部署中止（会自动回滚，但白跑一轮）。

根因是两个脚本各自维护同名常量、其中一个漏加。本测试把两份常量钉在一起，
任何一边改动而另一边没跟上都会在此失败。
"""

from __future__ import annotations

from scripts.deploy import check_release_consistency as checker
from scripts.deploy import write_release_manifest as writer


def test_generated_release_dirs_match() -> None:
    """两边对「生成目录」的排除必须一致（`frontend/node_modules/.vite*`）。"""
    assert writer.GENERATED_RELEASE_DIRS == checker.GENERATED_RELEASE_DIRS


def test_generated_release_files_match() -> None:
    """两边对「生成文件」的排除必须一致。"""
    assert writer.GENERATED_RELEASE_FILES == checker.GENERATED_RELEASE_FILES


def test_release_sha_excluded_by_both() -> None:
    """`RELEASE_SHA` 是 stamp 之后才写入的生成物，两边都必须排除。"""
    assert "RELEASE_SHA" in writer.GENERATED_RELEASE_FILES
    assert "RELEASE_SHA" in checker.GENERATED_RELEASE_FILES
