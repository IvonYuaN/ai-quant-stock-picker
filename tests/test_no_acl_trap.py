"""ACL 600 陷阱静态守卫。

``tempfile.mkstemp`` / ``tempfile.NamedTemporaryFile`` 默认建 0600，会把 Linux 的
ACL mask 压成 ``---``，令靠 ACL 授权的读者（如以 ``aqsp-vibe`` 运行的 API）读不到。
PR #172 修了共享写入器 ``atomic_write_text``；本测试确保所有其它临时文件写入器
也显式 ``chmod 0640``，防止下一个「21 文件 Errno 13」事故。

只扫会被部署/运行的代码（src/aqsp、backend、scripts）；测试夹具（tests/、outputs/）
不扫，避免误报。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_DIRS = ["src/aqsp", "backend", "scripts"]
# 匹配文件型临时写入器（mkdtemp 建目录、不在 ACL 文件陷阱范畴，故意不匹配）。
TRAP_PATTERNS = ("tempfile.mkstemp(", "tempfile.NamedTemporaryFile(", "NamedTemporaryFile(")


def _python_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if base.exists():
            yield from base.rglob("*.py")


def test_all_temp_file_writers_chmod_0640() -> None:
    offenders = []
    for f in _python_files():
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if any(p in line for p in TRAP_PATTERNS):
                # 该临时文件写入器之后 20 行内必须出现 os.chmod(..., 0640)，
                # 且模式必须是 0640（写 0o600 仍会触发 ACL 600 陷阱，等同没修）。
                # 窗口放宽到 20 行：真实修复常夹多行解释性注释（#172 就在 mkstemp 与
                # chmod 之间插了 5 行事故说明，间距 14 行），窄窗口会把合法修复误判。
                window = lines[i : i + 21]
                ok = any(
                    ("os.chmod(" in w) and ("0o640" in w or "0640" in w)
                    for w in window
                )
                if not ok:
                    offenders.append(f"{f.relative_to(ROOT)}:{i + 1}")
    assert not offenders, (
        "以下临时文件写入器未在 12 行内 chmod 0640（ACL 600 陷阱风险）：\n"
        + "\n".join(offenders)
    )
