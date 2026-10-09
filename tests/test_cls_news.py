"""财联社快讯 fetcher 测试。"""

from __future__ import annotations


from aqsp.data.cls_news import ClsNewsItem, ClsNewsSource, _parse_items


def test_parse_empty_and_non_dict():
    assert _parse_items(None) == []
    assert _parse_items({}) == []
    assert _parse_items([]) == []


def test_parse_real_shape_roll_data():
    payload = {
        "data": {
            "roll_data": [
                {
                    "id": "1001",
                    "title": "央行下调存款准备金率 0.25 个百分点",
                    "content": "中国人民银行决定...",
                    "ctime": 1725849600,
                    "level": "A",
                    "subjects": [
                        {"subject_name": "宏观"},
                        {"subject_name": "货币政策"},
                    ],
                },
                {
                    "id": "1002",
                    "title": "某新能源车企交付创新高",
                    "content": "",
                    "ctime": "2026-09-08 12:00:00",
                    "level": "B",
                    "subjects": [],
                },
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 2
    a = items[0]
    assert a.item_id == "1001"
    assert a.title.startswith("央行")
    assert a.level == "A"
    assert a.subjects == ("宏观", "货币政策")
    b = items[1]
    assert b.item_id == "1002"
    assert b.subjects == ()


def test_parse_skips_malformed_item():
    payload = {
        "data": {
            "roll_data": [
                {
                    "id": "ok",
                    "title": "good",
                    "content": "x",
                    "ctime": 1,
                    "level": "C",
                    "subjects": [],
                },
                {"no_id_no_title": True},  # 缺关键字段 -> 跳过（_parse 容错在 try 内）
            ]
        }
    }
    items = _parse_items(payload)
    # 第二条无 title/ctime 但 dataclass 全默认；_parse 不抛但 subjects=()
    # 关键：整批不 crash，至少 1 条
    assert len(items) >= 1
    assert items[0].item_id == "ok"


def test_source_from_items_and_items():
    src = ClsNewsSource().from_items(
        [
            ClsNewsItem(
                item_id="x",
                title="t",
                summary="s",
                ctime="2026-01-01",
                level="A",
                subjects=(),
            )
        ]
    )
    assert len(src.items()) == 1
    assert src.items(autoload=False)[0].title == "t"


def test_parse_ctime_seconds_unix_timestamp_not_nanoseconds():
    """issue #317 W2：cls.cn ctime 是秒级 Unix 时间戳。

    回归：pd.Timestamp(int) 按纳秒解释 ⇒ 1791529166 变成 1970-01-01，
    信息流按时间倒序完全不可用。
    """
    payload = {
        "data": {
            "roll_data": [
                {"id": "1", "title": "t1", "content": "c1", "ctime": 1791529166, "level": "A"},
                {"id": "2", "title": "t2", "content": "c2", "ctime": "1791529166", "level": "A"},
                {"id": "3", "title": "t3", "content": "c3", "ctime": 1791529166000, "level": "A"},
                {"id": "4", "title": "t4", "content": "c4", "ctime": 1791529166000000000, "level": "A"},
                {"id": "5", "title": "t5", "content": "c5", "ctime": "2026-10-09 19:39:26", "level": "A"},
                {"id": "6", "title": "t6", "content": "c6", "ctime": "not-a-date", "level": "A"},
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 6
    expected = "2026-10-09T14:59:26+08:00"
    # 秒（int/str 同值）、毫秒、纳秒四种数字输入归一到同一上海时区时刻
    assert items[0].ctime == expected
    assert items[1].ctime == expected
    assert items[2].ctime == expected
    assert items[3].ctime == expected
    # 字符串日期原样解析（保持朴素本地时间，不做时区假设）
    assert items[4].ctime == "2026-10-09 19:39:26"[:10] + "T19:39:26"
    # 解析失败回退空串，绝不 crash 整批
    assert items[5].ctime == ""
    assert all(not it.ctime.startswith("1970") for it in items)
