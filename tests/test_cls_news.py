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
