"""fetch_event_data.py 的单元契约（#193）。

验证：
- earnings_forecast 收到 notice_from、dividend_plan 收到 ex_dividend_from、suspend_resume 不带窗口；
- 截断 / 覆盖范围落产物侧 .meta.json（truncated 标记准确），下游无需翻日志即可判完整性。

零网络：三个源类被替换为内存假类。
"""
from __future__ import annotations

import importlib.util
import json


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "fetch_event_data_under_test", "scripts/fetch_event_data.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Fake:
    truncated = False

    def __init__(self, name: str) -> None:
        self.name = name

    def load(self, force: bool = False, **kw):  # noqa: ANN001
        return [1, 2, 3]

    def _default_cache_path(self):  # noqa: ANN202
        raise NotImplementedError


class Earnings(_Fake):
    def __init__(self) -> None:
        super().__init__("earnings_forecast")

    def _default_cache_path(self):  # noqa: ANN202
        return str(_TMP / "earnings_forecast.csv")


class Dividend(_Fake):
    def __init__(self) -> None:
        super().__init__("dividend_plan")

    def _default_cache_path(self):  # noqa: ANN202
        return str(_TMP / "dividend_plan.csv")


class Suspend(_Fake):
    def __init__(self) -> None:
        super().__init__("suspend_resume")

    def _default_cache_path(self):  # noqa: ANN202
        return str(_TMP / "suspend_resume.csv")


class Holder(_Fake):
    """股东户数源：main() 会带 quarters= 调 load，必须也 mock 掉，否则走真实东财抓取。"""

    def __init__(self) -> None:
        super().__init__("holder_num")

    def _default_cache_path(self):  # noqa: ANN202
        return str(_TMP / "holder_num.csv")


class Announcement(_Fake):
    """公告源：main() 会带 begin_time/end_time 调 load，必须也 mock 掉。"""

    def __init__(self) -> None:
        super().__init__("announcement")

    def _default_cache_path(self):  # noqa: ANN202
        return str(_TMP / "announcement.csv")


_TMP = None


def test_fetch_passes_window_and_writes_meta(tmp_path, monkeypatch):
    global _TMP
    _TMP = tmp_path
    mod = _load_module()

    captured: dict = {}

    class _Recorder(_Fake):
        def load(self, force: bool = False, **kw):  # noqa: ANN001
            captured[self.name] = dict(kw)
            return [1, 2, 3]

    class E(_Recorder):
        def __init__(self) -> None:
            super().__init__("earnings_forecast")

        def _default_cache_path(self):  # noqa: ANN202
            return str(tmp_path / "earnings_forecast.csv")

    class D(_Recorder):
        def __init__(self) -> None:
            super().__init__("dividend_plan")

        def _default_cache_path(self):  # noqa: ANN202
            return str(tmp_path / "dividend_plan.csv")

    class S(_Recorder):
        def __init__(self) -> None:
            super().__init__("suspend_resume")

        def _default_cache_path(self):  # noqa: ANN202
            return str(tmp_path / "suspend_resume.csv")

    monkeypatch.setattr(mod, "EarningsForecastSource", E)
    monkeypatch.setattr(mod, "DividendPlanSource", D)
    monkeypatch.setattr(mod, "SuspendResumeSource", S)
    # 这两个源没被 mock 时，CI（连不上东财）会真实抓取超时 → main() 返回 1。
    # 注释声称"零网络"，mock 必须覆盖 main() 里的全部 5 个源，缺一个就会破功。
    monkeypatch.setattr(mod, "HolderNumSource", Holder)
    monkeypatch.setattr(mod, "AnnouncementSource", Announcement)

    assert mod.main() == 0

    # 窗口正确下传：earnings→notice_from，dividend→ex_dividend_from，suspend→无窗口
    assert "notice_from" in captured["earnings_forecast"]
    assert "ex_dividend_from" in captured["dividend_plan"]
    assert captured["suspend_resume"] == {}

    # 覆盖范围落产物侧标记（截断与否都写，下游可判完整性）
    em = json.loads((tmp_path / "earnings_forecast.meta.json").read_text(encoding="utf-8"))
    assert em["truncated"] is False
    assert "覆盖" in em["coverage"]
    assert (tmp_path / "dividend_plan.meta.json").exists()
    assert (tmp_path / "suspend_resume.meta.json").exists()


def test_fetch_marks_truncated_in_meta(tmp_path, monkeypatch):
    global _TMP
    _TMP = tmp_path
    mod = _load_module()

    class E(_Fake):
        truncated = True

        def __init__(self) -> None:
            super().__init__("earnings_forecast")

        def _default_cache_path(self):  # noqa: ANN202
            return str(tmp_path / "earnings_forecast.csv")

    monkeypatch.setattr(mod, "EarningsForecastSource", E)
    # 只验证截断标记路径：直接用 _preload，跳过其余两个源
    assert mod._preload("earnings_forecast", E()) is True

    meta = json.loads((tmp_path / "earnings_forecast.meta.json").read_text(encoding="utf-8"))
    assert meta["truncated"] is True
    assert isinstance(meta["coverage"], str) and meta["coverage"]
