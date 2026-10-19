"""pytest 配置：把 backend 目录加进 sys.path，注册 live 标记，隔离用户数据目录。"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))

# 用户数据隔离：portfolio / myreports 在 import 时按 VR_DATA_DIR / VR_REPORTS_DIR 固化路径，
# 必须赶在任何测试模块 import app 之前指到临时目录——否则持仓 CRUD 类测试会增删真实
# ~/.vibe-research/ 里的用户数据（比如把用户真实持有的 600519 合并后删掉）。
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="vr-test-data-")
os.environ["VR_DATA_DIR"] = _TEST_DATA_DIR
os.environ["VR_REPORTS_DIR"] = os.path.join(_TEST_DATA_DIR, "myreports")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: 打真实数据源的网络冒烟测（会联网、可能受上游/限流影响；默认可 -m 'not live' 跳过）",
    )


@pytest.fixture(autouse=True)
def _reset_breadth_memo():
    """market._breadth_memo 是进程内 30s 记忆（模块全局）：用例之间必须清。

    不清的后果：上一个用例的假数据会被下一个用例「借」到——假绿/假红取决于
    用例顺序，排查时完全看不出因果（#417 实测踩到）。
    """
    import market

    market._breadth_memo = None
    yield
    market._breadth_memo = None
