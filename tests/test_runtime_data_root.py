"""`aqsp.core.runtime.runtime_data_root` 单一来源解析的单测。

核心不变量：未设 env 时**绝不**回落 ``/tmp``（09-26 排查 13 处 ``or tempfile.gettempdir()``
同类隐患的统一收口）；设了绝对路径 env 严格用它。
"""
from __future__ import annotations

from pathlib import Path

from aqsp.core import runtime as rt


def test_env_set_absolute_returns_it(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    assert rt.runtime_data_root() == tmp_path.resolve(strict=False)


def test_env_set_relative_falls_back_to_repo_root(monkeypatch) -> None:
    # 非绝对路径的 env 值被忽略 ⇒ 回落 repo 根（不是 /tmp）
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", "relative/path")
    root = rt.runtime_data_root()
    assert root.is_absolute()
    assert root != Path("/tmp")


def test_env_unset_falls_back_to_repo_root(monkeypatch) -> None:
    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)
    root = rt.runtime_data_root()
    assert root.is_absolute()
    assert root != Path("/tmp")
    # 回落基准 = core/runtime.py 上溯 4 层的 repo/release 根
    expected = Path(rt.__file__).resolve().parents[3]
    assert root == expected


def test_custom_env_name(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MY_CUSTOM_ROOT", str(tmp_path))
    assert rt.runtime_data_root(env_name="MY_CUSTOM_ROOT") == tmp_path.resolve(strict=False)


def test_no_tempfile_fallback_anywhere(monkeypatch) -> None:
    """回归：任何配置下解析结果都不是系统临时目录。"""
    import tempfile

    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)
    assert rt.runtime_data_root() != Path(tempfile.gettempdir())
    assert not str(rt.runtime_data_root()).startswith("/tmp")
