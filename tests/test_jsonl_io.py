"""`atomic_write_text` 的**权限契约**：落盘必须是 0640。

历史事故（2026-09-22）：`tempfile.mkstemp` 默认建 0600 的文件，`os.replace` 保留该模式
⇒ 最终文件是 0600 ⇒ **ACL mask 被压成 `---`** ⇒ 所有靠 ACL 授权的读者（以 `aqsp-vibe`
运行的 API）都读不到。当时 `/opt/aqsp/data` 下 **21 个文件**中招
（`predictions.jsonl`、`walkforward_gate.json`、`paper_trades.jsonl` …），
绩效页直接报 `Errno 13 Permission denied`。

这条测试守住它，防止以后退化回 0600。
"""

from __future__ import annotations

from aqsp.utils.jsonl_io import atomic_write_text


def test_atomic_write_text_lands_0640(tmp_path) -> None:
    target = tmp_path / "sample.json"

    atomic_write_text(target, '{"a": 1}')

    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    assert target.stat().st_mode & 0o777 == 0o640, (
        "落盘模式必须是 0640：0600 会把 ACL mask 压成 ---，令 ACL 授权者读不到"
    )


def test_atomic_write_text_overwrites_keep_0640(tmp_path) -> None:
    target = tmp_path / "sample.json"

    atomic_write_text(target, "first")
    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert target.stat().st_mode & 0o777 == 0o640
