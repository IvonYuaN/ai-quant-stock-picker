"""守卫：`data/cn/` 两个模块的默认缓存路径必须落在 `runtime_data_root()/pit_cache/` 下。

背景（issue #336）：这两个文件位于 `src/aqsp/data/cn/`，却把缓存根写成
`Path(__file__).resolve().parents[3] / "data"` —— `parents[3]` 实为 `<repo>/src`，
于是缓存落到 **`<repo>/src/data/`（release 树内部）**：

  - prod 每次部署即被清空 ⇒ 缓存**永远冷启动**；
  - release 只读时 `mkdir` 失败 ⇒ **静默退化为「无缓存」**；
  - 同一错误被两处固化：`.gitignore` 的 `src/data/`、`config/strategy_sources.yaml`
    的 `absorbed_from: "src/data/margin_history.csv"`（均已在修复中清理）。

同族的 6 个兄弟模块（`longhubang` / `lockup` / `suspend_resume` / `holder_num` /
`announcement` / `concept_board`）全部使用 `runtime_data_root()/pit_cache/`，本守卫把
这条约定对 `cn/` 也变成可执行契约。

🔴 断言的是**行为（解析出的路径）**，不是「源码里有没有某个字符串」——
后者会被变量化静默打穿（项目已有教训，见 #307 / #338）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

CASES = (
    ("aqsp.data.cn.northbound", "northbound_history.csv"),
    ("aqsp.data.cn.margin_trading", "margin_history.csv"),
)


@pytest.mark.parametrize(("module_name", "filename"), CASES)
def test_cn_cache_default_path_lives_under_runtime_pit_cache(
    module_name: str, filename: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """默认缓存路径 = `runtime_data_root()/pit_cache/<filename>`，且路径里不含 `src` 段。"""
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(runtime_root))

    module = __import__(module_name, fromlist=["_default_cache_path"])
    resolved = Path(module._default_cache_path()).resolve()

    assert resolved.parent == (runtime_root / "pit_cache").resolve(), (
        f"{module_name} 的默认缓存目录不在 runtime root 的 pit_cache 下：{resolved}"
    )
    assert resolved.name == filename
    # 🔴 回归点：修复前落点是 <repo>/src/data/...，路径里必然带 "src" 段
    assert "src" not in resolved.parts, (
        f"{module_name} 的默认缓存路径仍落在 src/ 下（#336 回归）：{resolved}"
    )


@pytest.mark.parametrize(("module_name", "filename"), CASES)
def test_cn_cache_default_path_follows_env_at_call_time(
    module_name: str, filename: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """路径必须**调用时**解析 env，而不是 import 时固化。

    原实现是模块级常量 ⇒ 一旦有人改成 import 期解析，本用例会失败
    （import 期解析会让测试/长驻进程无法通过 env 重定向缓存位置）。
    """
    first = tmp_path / "first"
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(first))
    module = __import__(module_name, fromlist=["_default_cache_path"])
    assert Path(module._default_cache_path()).resolve().parent == (first / "pit_cache").resolve()

    second = tmp_path / "second"
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(second))
    assert Path(module._default_cache_path()).resolve().parent == (
        second / "pit_cache"
    ).resolve(), f"{module_name} 的缓存根在 import 期被固化，未随 env 变化"


def test_cn_cache_dir_is_created_on_demand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`pit_cache/` 目录应被自动创建（否则首次写入会失败）。"""
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(runtime_root))
    module = __import__("aqsp.data.cn.northbound", fromlist=["_default_cache_path"])
    path = module._default_cache_path()
    assert path.parent.is_dir(), "pit_cache 目录未被创建"
