"""astock 时区回归测（离线、确定性）。

锁住 AGENTS.md §3.4「禁止裸 datetime.now()」在 backend 数据层的落地：

生产机若以 UTC 运行，裸 ``datetime.now()`` / ``date.today()`` 在北京时间
00:00–08:00 会返回**前一天**的日期，导致龙虎榜、限售解禁、研报检索窗口整体
偏移一天。这是静默的数据错误（数据看起来正常，只是慢一天），因此必须有测试
把「服务器 UTC + 北京时间凌晨」这个组合钉死。
"""

from datetime import datetime, timedelta, timezone

import astock


def _fake_utc_now_class(fixed_utc: datetime):
    """构造一个 datetime 替身：``now(tz)`` 恒返回指定 UTC 瞬时。

    用来在测试中模拟「进程运行在 UTC、但业务时区是 Asia/Shanghai」的生产场景。
    """

    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz is not None else fixed_utc

    return _FakeDateTime


def test_now_shanghai_returns_aware_datetime_with_utc_plus_8():
    now = astock._now_shanghai()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=8)


def test_now_shanghai_returns_shanghai_date_when_server_clock_is_utc(monkeypatch):
    """UTC 服务器在 19:00Z（北京次日 03:00）必须得到北京次日日期，而非 UTC 当日。"""
    fake_utc = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(astock, "datetime", _fake_utc_now_class(fake_utc))

    # 裸 UTC 时钟给出 09-05；上海时区必须给出 09-06（差一天 = 真实 bug 面）。
    assert fake_utc.strftime("%Y-%m-%d") == "2026-09-05"
    assert astock._now_shanghai().strftime("%Y-%m-%d") == "2026-09-06"


def test_now_shanghai_agrees_with_utc_date_after_08_beijing(monkeypatch):
    """北京时间 08:00 之后两者同日——确保修复没有整体偏移一天（过冲检查）。"""
    fake_utc = datetime(2026, 9, 5, 0, 30, tzinfo=timezone.utc)  # 北京 08:30
    monkeypatch.setattr(astock, "datetime", _fake_utc_now_class(fake_utc))

    assert astock._now_shanghai().strftime("%Y-%m-%d") == "2026-09-05"
    assert astock._now_shanghai().date().isoformat() == "2026-09-05"


def test_dragon_tiger_board_defaults_to_shanghai_date(monkeypatch):
    """龙虎榜默认 trade_date 必须走上海时区，不能沿用进程本地（可能 UTC）时钟。"""
    fake_utc = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)  # 北京 09-06 03:00
    monkeypatch.setattr(astock, "datetime", _fake_utc_now_class(fake_utc))

    filters: list[str] = []

    def fake_datacenter(report: str, **kwargs):
        filters.append(kwargs.get("filter_str", ""))
        return []

    monkeypatch.setattr(astock, "eastmoney_datacenter", fake_datacenter)
    result = astock.dragon_tiger_board("600519")

    assert result == {
        "records": [],
        "seats": {"buy": [], "sell": []},
        "institution": {"buy_amt": 0.0, "sell_amt": 0.0, "net_amt": 0.0},
    }
    # records 为空时不会发起买卖席位请求，故 filters 只有首个榜单查询。
    assert len(filters) == 1
    assert "TRADE_DATE<='2026-09-06'" in filters[0]


def test_lockup_expiry_defaults_to_shanghai_date(monkeypatch):
    """限售解禁默认 trade_date 同样必须走上海时区。

    注意：首次 datacenter 调用是「历史解禁」查询（不含日期过滤），日期只出现在
    第二次「未来待解禁」调用的 filter 里，因此必须收集全部 filter 再断言。
    """
    fake_utc = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)  # 北京 09-06 03:00
    monkeypatch.setattr(astock, "datetime", _fake_utc_now_class(fake_utc))

    filters: list[str] = []

    def fake_datacenter(report: str, **kwargs):
        filters.append(kwargs.get("filter_str", ""))
        return []

    monkeypatch.setattr(astock, "eastmoney_datacenter", fake_datacenter)
    astock.lockup_expiry("600519")

    assert len(filters) == 2
    assert "(FREE_DATE>='2026-09-06')" in filters[1]
    # 上界 = trade_date + forward_days(90) = 2026-12-05
    assert "(FREE_DATE<='2026-12-05')" in filters[1]


def test_eastmoney_industry_reports_uses_shanghai_end_date(monkeypatch):
    """研报检索窗口的 end 必须是上海日期，而非进程本地 date.today()。"""
    fake_utc = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)  # 北京 09-06 03:00
    monkeypatch.setattr(astock, "datetime", _fake_utc_now_class(fake_utc))

    captured: dict[str, object] = {}

    def fake_session():
        class _Session:
            def get(self, url, params=None, timeout=None):
                captured.update(params or {})
                raise RuntimeError("stop after capturing params")

        return _Session()

    monkeypatch.setattr(astock, "_report_session", fake_session)
    try:
        astock.eastmoney_industry_reports(keywords=None, days=90, max_pages=1)
    except RuntimeError:
        pass

    assert captured.get("endTime") == "2026-09-06"
    assert captured.get("beginTime") == "2026-06-08"  # 90 天窗口起点
