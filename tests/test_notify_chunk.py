"""`aqsp.notify_chunk` 单测：长文分段不丢字符 + 钉钉关键词。

核心不变量：``"".join(split_markdown(text, markers=False))`` 等于输入经过
「``\\r\\n`` → ``\\n`` + 去首尾空白」归一化后的结果。切分只搬字符、不改字符
——防止将来有人为了「好看」在切分里顺手 strip 每一段 / 替换标点。
"""

from __future__ import annotations

import pytest

from aqsp.notify_chunk import (
    DEFAULT_CHUNK_LIMIT,
    DEFAULT_MAX_CHUNKS,
    ensure_dingtalk_keyword,
    split_markdown,
)


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def _review_markdown(sections: int = 12, body_lines: int = 8) -> str:
    """造一份接近真实盘后复盘的正文（每节一个空行分隔）。"""
    parts = ["# 收盘总览\n"]
    for index in range(sections):
        parts.append(f"\n## 第 {index + 1} 节\n")
        for line in range(body_lines):
            parts.append(f"- 要点 {index + 1}.{line + 1}：这里是正文内容，用于撑长度。\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 短正文：行为与改造前完全一致
# ---------------------------------------------------------------------------


def test_short_text_is_returned_verbatim_as_single_chunk() -> None:
    text = "# 标题\n\n- 一行内容"

    assert split_markdown(text) == (text,)


def test_text_exactly_at_limit_is_not_split() -> None:
    text = "a" * DEFAULT_CHUNK_LIMIT

    assert split_markdown(text, limit=DEFAULT_CHUNK_LIMIT) == (text,)


def test_empty_input_yields_single_empty_chunk() -> None:
    assert split_markdown("") == ("",)
    assert split_markdown(None) == ("",)


def test_crlf_is_normalised_and_outer_whitespace_stripped() -> None:
    assert split_markdown("  a\r\nb  \n") == ("a\nb",)


def test_non_positive_limit_raises() -> None:
    with pytest.raises(ValueError, match="limit 必须为正数"):
        split_markdown("abc", limit=0)


# ---------------------------------------------------------------------------
# 分段：不丢字符、不超上限
# ---------------------------------------------------------------------------


def test_split_preserves_every_character() -> None:
    text = _review_markdown(sections=20, body_lines=12)
    assert len(text) > DEFAULT_CHUNK_LIMIT

    chunks = split_markdown(text, markers=False)

    assert len(chunks) > 1
    assert "".join(chunks) == _normalized(text)


def test_every_chunk_stays_within_limit() -> None:
    text = _review_markdown()

    for chunk in split_markdown(text):
        assert len(chunk) <= DEFAULT_CHUNK_LIMIT, f"段超长 {len(chunk)}"


def test_markers_number_continuation_segments() -> None:
    text = _review_markdown()

    chunks = split_markdown(text)

    assert not chunks[0].startswith("（1/")
    for index, chunk in enumerate(chunks[1:], start=2):
        assert chunk.startswith(f"（{index}/{len(chunks)}）\n"), chunk[:20]


def test_paragraph_boundary_preferred_over_mid_sentence_break() -> None:
    """每节以空行结束 ⇒ 正常情况下切点应落在空行后（``\\n\\n``）。"""
    text = _review_markdown()

    chunks = split_markdown(text, markers=False)

    for chunk in chunks[:-1]:
        assert chunk.endswith("\n\n"), f"切点不在段落边界：{chunk[-30:]!r}"


def test_oversized_single_line_is_hard_split_without_losing_characters() -> None:
    text = "x" * (DEFAULT_CHUNK_LIMIT * 2 + 100)

    chunks = split_markdown(text, markers=False)

    assert len(chunks) >= 2
    assert "".join(chunks) == _normalized(text)
    assert all(len(chunk) <= DEFAULT_CHUNK_LIMIT for chunk in chunks)


def test_max_chunks_cap_is_explicit_never_silent() -> None:
    """超出发送段数上限时必须写明「余下 N 段未发送」——静默截断是本次修复的靶子。"""
    text = _review_markdown(sections=40)

    chunks = split_markdown(text, max_chunks=2)

    assert len(chunks) == 2
    assert "余下" in chunks[-1]
    assert "段未发送" in chunks[-1]
    assert len(chunks[-1]) <= DEFAULT_CHUNK_LIMIT


def test_max_chunks_one_still_reports_dropped_content() -> None:
    text = _review_markdown(sections=40)

    chunks = split_markdown(text, max_chunks=1)

    assert len(chunks) == 1
    assert "余下" in chunks[0]
    assert len(chunks[0]) <= DEFAULT_CHUNK_LIMIT


def test_no_notice_when_content_fits_within_max_chunks() -> None:
    text = _review_markdown(sections=6)

    chunks = split_markdown(text, max_chunks=DEFAULT_MAX_CHUNKS)

    assert "未发送" not in "".join(chunks)


def test_small_limit_still_produces_valid_chunks() -> None:
    text = "段落一\n\n段落二\n\n段落三"

    chunks = split_markdown(text, limit=20, max_chunks=10, markers=False)

    assert "".join(chunks) == _normalized(text)
    assert all(len(chunk) <= 20 for chunk in chunks)


# ---------------------------------------------------------------------------
# 钉钉关键词
# ---------------------------------------------------------------------------


def test_keyword_is_prepended_when_missing() -> None:
    assert ensure_dingtalk_keyword("正文", "AQSP") == "AQSP\n正文"


def test_keyword_is_not_duplicated_when_present() -> None:
    assert ensure_dingtalk_keyword("AQSP 收盘总览", "AQSP") == "AQSP 收盘总览"


@pytest.mark.parametrize("keyword", [None, "", "   "])
def test_no_keyword_configured_leaves_text_untouched(keyword: object) -> None:
    assert ensure_dingtalk_keyword("正文", keyword) == "正文"
