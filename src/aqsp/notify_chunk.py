"""通知长文分段 + 钉钉关键词 —— 替代「静默截断」。

问题
----
`notifier.py` 里各渠道是这么发的::

    payload = {"msgtype": "markdown", "markdown": {"content": markdown[:3800]}}

也就是**超出上限就砍掉**，而且**不留任何痕迹**。盘后复盘正文（含板块资金面、
情绪周期、候选清单）很容易超过 3800 字，用户看到的是一份「看起来完整、其实从中间
断掉」的报告——这比报错更糟：报错会让人去查，截断不会。

本模块只做两件纯逻辑的事：

1. :func:`split_markdown` —— 按上限切成多段，**优先在段落边界断开**；
   内容超出发送段数上限时，在末段**明确写出「余下 N 段未发送」**，不静默丢。
2. :func:`ensure_dingtalk_keyword` —— 钉钉自定义机器人若配置了「自定义关键词」
   安全模式，正文里必须出现该关键词，否则被服务端拒收。缺失时前置关键词，
   已包含时原样返回。

不变量
------
``"".join(split_markdown(text, markers=False)) == text.replace("\\r\\n", "\\n").strip()``
（未触发段数上限时）。也就是说：切分**只搬字符、不改字符**——唯一的改写是把
``\\r\\n`` 归一成 ``\\n`` 并去掉首尾空白。测试直接断言这条，防止将来有人为了
「好看」在切分里顺手 strip 每一段 / 替换标点，把正文改掉。

为什么不在 ``notification_style.compact_notification_markdown`` 里做
--------------------------------------------------------------
那个函数的职责是**压缩**（去噪声行、合并空行、限每节条数），它已经在正常路径上
跑过一遍；再塞切分进去会让「压缩」与「传输限制」两件事纠缠，且各渠道上限不同
（微信 3800 / 钉钉 3800 / Telegram 3900 / Discord 2000）。上限属于渠道属性，
留在 :mod:`aqsp.notifier` 的发送函数里。
"""

from __future__ import annotations

import re

#: 默认单段上限（字符）。取 3500 而不是贴着 3800，是给分段前缀与各渠道
#: 服务端的隐式包装留余量。
DEFAULT_CHUNK_LIMIT = 3500

#: 默认最多发几段。超过则末段写明「余下 N 段未发送」。
#: 3 段 ≈ 10.5k 字符，已覆盖盘后复盘的实际长度；再多会触发渠道频率限制。
DEFAULT_MAX_CHUNKS = 3

#: 分段前缀（``（2/3）``）预留的字符数。
_CONTINUATION_RESERVE = 12

#: 段落边界优先断开的下限比例：缓冲区已用超过该比例且正好落在空行后，
#: 就提前断开，避免把一个小节劈成两半。
_PARAGRAPH_BREAK_RATIO = 0.6

_SEGMENT_PATTERN = re.compile(r"[^\n]*\n|[^\n]+")


def _segments(text: str) -> list[str]:
    """按行切段，每段保留行尾 ``\\n``；拼回去与原串**逐字符相同**。"""
    return _SEGMENT_PATTERN.findall(text)


def _hard_split(segment: str, limit: int) -> list[str]:
    """把超长单段按字符硬切（markdown 里极少见，但不切会死循环）。"""
    return [segment[index : index + limit] for index in range(0, len(segment), limit)]


def _pack(text: str, *, pack_limit: int) -> list[str]:
    """把 ``text`` 贪心装进不超过 ``pack_limit`` 的段里（保持字符顺序与内容）。"""
    chunks: list[str] = []
    buffer = ""
    for segment in _segments(text):
        if len(segment) > pack_limit:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_hard_split(segment, pack_limit))
            continue
        if len(buffer) + len(segment) > pack_limit:
            chunks.append(buffer)
            buffer = segment
            continue
        buffer += segment
        if buffer.endswith("\n\n") and len(buffer) >= pack_limit * _PARAGRAPH_BREAK_RATIO:
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def _dropped_notice(*, dropped: int, total: int, kept: int) -> str:
    return f"\n\n…（全文共 {total} 段，此处为第 {kept} 段；余下 {dropped} 段未发送）\n"


def split_markdown(
    text: object,
    *,
    limit: int = DEFAULT_CHUNK_LIMIT,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    markers: bool = True,
) -> tuple[str, ...]:
    """把 markdown 切成若干段，每段长度 ≤ ``limit``。

    :param text: 待发送正文。``\\r\\n`` 先归一成 ``\\n``，首尾空白去掉。
    :param limit: 单段上限（字符）。必须 > 0。
    :param max_chunks: 最多段数（≥1）。超出时**末段写明余下未发送段数**。
    :param markers: 是否给第 2 段起加 ``（i/N）`` 前缀。``False`` 用于断言
        「切分不改字符」的不变量。
    :return: 段元组。空输入 ⇒ ``("",)``（调用方照常发一条空消息，不特判）。

    短正文（``len(text) <= limit``）返回**与输入逐字符相同**的单段元组，
    因此对现有短消息的发送行为零影响。
    """
    if limit <= 0:
        raise ValueError(f"split_markdown: limit 必须为正数，收到 {limit!r}")
    max_chunks = max(int(max_chunks), 1)

    normalized = str(text if text is not None else "").replace("\r\n", "\n").strip()
    if not normalized:
        return ("",)
    if len(normalized) <= limit:
        return (normalized,)

    pack_limit = max(limit - _CONTINUATION_RESERVE, 1)
    chunks = _pack(normalized, pack_limit=pack_limit)
    total = len(chunks)

    if total > max_chunks:
        kept = chunks[:max_chunks]
        notice = _dropped_notice(dropped=total - max_chunks, total=total, kept=max_chunks)
        room = max(limit - len(notice), 1)
        kept[-1] = kept[-1][:room] + notice
        chunks = kept

    if not markers:
        return tuple(chunks)

    out: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        if index == 1:
            out.append(chunk)
        else:
            out.append(f"（{index}/{total}）\n{chunk}")
    return tuple(out)


def ensure_dingtalk_keyword(text: str, keyword: object) -> str:
    """钉钉「自定义关键词」安全模式：正文缺关键词时前置一行。

    关键词已包含（大小写敏感，与钉钉服务端一致）或未配置 ⇒ 原样返回。
    """
    word = str(keyword if keyword is not None else "").strip()
    if not word or word in text:
        return text
    return f"{word}\n{text}"


__all__ = [
    "DEFAULT_CHUNK_LIMIT",
    "DEFAULT_MAX_CHUNKS",
    "ensure_dingtalk_keyword",
    "split_markdown",
]
