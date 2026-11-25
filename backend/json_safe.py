"""JSON 响应边界的非有限浮点净化。

## 为什么需要这一层

starlette 的 ``JSONResponse.render`` 是 ``json.dumps(..., allow_nan=False)``。
payload 里只要出现**一个** ``NaN`` / ``inf`` / ``-inf``，它就抛
``ValueError: Out of range float values are not JSON compliant`` ⇒ **整个端点 500**。
一个板块的涨跌幅缺失不该让整页打不开（#648 就是在 ``_sectors()`` 上踩到的）。

## NaN 不是 bug，是常态

pandas / akshare 的缺失值就是 ``float('nan')``。而 ``float(v or 0)`` **兜不住 NaN**
—— NaN 是真值，``nan or 0`` 还是 ``nan``，``round(nan, 2)`` 还是 ``nan``。
所以「每一处都写对」靠人肉 review 是守不住的：后端有 93 个端点各自拼 payload，
任何一个新写的 ``round(float(x), 2)`` 都能把这条路径重新打开。

## 语义映射是诚实的

非有限浮点 = 「不是一个数」= **未知** ⇒ 收敛成 ``null``。
前端已有的 ``null → 「—」`` 渲染（``formatSignedPct`` / ``changeClass`` /
``asNullableNumber``）会把它如实呈现成「没取到」，而**不是**编一个 0 ——
这与「宁可不给，不要给假的」是同一条口径，也是 #648 定下的做法。

## 🔴 这是兜底网，不是免修许可证

漏进来的非有限浮点一律记 ``warning`` 并带上字段路径（如 ``data.sectors[3].pct``），
所以「本该是数字的字段变成了 NaN」在日志里仍然可见，不会被悄悄吃掉。
逐点的 ``_flow_num`` / ``asNullableNumber`` 之类**仍然要写** —— 它们能给出
「为什么没有」的语义（未披露 / 取数失败 / 结构残缺），这一层只能给 ``null``。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# 只在这几种容器里递归。payload 经过 FastAPI 的 jsonable_encoder 之后只会是
# dict / list / tuple / str / int / float / bool / None；其余类型 json.dumps 本来就
# 会 TypeError，不属于这一层要处理的范围。
_CONTAINERS = (dict, list, tuple)


def _is_non_finite(value: object) -> bool:
    """``float`` 的 NaN / ±inf。

    🔴 只看 ``float``：``bool`` 与 ``int`` 不可能是非有限数，放它们进来纯属浪费；
    而 ``numpy.float32`` 这类非 ``float`` 子类的数值 json.dumps 会直接 TypeError，
    那不是 NaN 问题，不在这里兜（混进来只会让这一层看起来比实际更强）。
    """
    return isinstance(value, float) and not math.isfinite(value)


def has_non_finite(value: object) -> bool:
    """快速探测：payload 里有没有非有限浮点。

    单独拆出来是为了**让正常路径不付代价**：绝大多数响应是干净的，
    这时 `scrub_non_finite` 直接原样返回同一个对象，既不复制容器也不拼路径字符串
    （路径字符串才是真正的开销 —— 每个叶子一次 f-string 会把大 payload 拖慢一个量级）。
    """
    if _is_non_finite(value):
        return True
    if isinstance(value, dict):
        return any(has_non_finite(item) for item in value.values())
    if isinstance(value, _CONTAINERS):
        return any(has_non_finite(item) for item in value)
    return False


def _scrub(value: Any, path: str, found: list[str]) -> Any:
    """递归重建：非有限浮点 → ``None``，其余原样；命中处记下字段路径。

    返回**新的**容器，不改动入参 —— payload 可能是被 SWR 缓存共享的对象，
    就地改写会让「只读快照」这件事不再成立。
    """
    if _is_non_finite(value):
        found.append(path or "<root>")
        return None
    if isinstance(value, dict):
        return {
            key: _scrub(item, f"{path}.{key}" if path else str(key), found)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_scrub(item, f"{path}[{index}]", found) for index, item in enumerate(value))
    if isinstance(value, list):
        return [_scrub(item, f"{path}[{index}]", found) for index, item in enumerate(value)]
    return value


def scrub_non_finite(value: Any) -> tuple[Any, list[str]]:
    """payload → (净化后的 payload, 命中的字段路径)。

    没有非有限浮点时**原样返回同一个对象**（零复制、零分配），调用方可以放心
    在热路径上用。
    """
    if not has_non_finite(value):
        return value, []
    found: list[str] = []
    return _scrub(value, "", found), found


def _format_paths(paths: list[str]) -> str:
    """日志里最多点名前 8 个，剩下的只报个数 —— 一条 warning 不该刷满整个日志文件。"""
    head = ", ".join(paths[:8])
    return head if len(paths) <= 8 else f"{head} 等 {len(paths)} 处"


class SafeJSONResponse(JSONResponse):
    """把 payload 里的 NaN / ±inf 收敛成 ``null``，而不是让整个端点 500。

    挂在 ``FastAPI(default_response_class=...)`` 上 —— 一处接线覆盖全部
    「返回 dict」的端点。显式 ``return JSONResponse(...)`` 的少数几处会绕过它，
    但那些只返回 ``{"detail": str}``，没有浮点。
    """

    def render(self, content: Any) -> bytes:
        cleaned, found = scrub_non_finite(content)
        if found:
            logger.warning(
                "JSON 响应含非有限浮点，已按「未知」收敛为 null：%s",
                _format_paths(found),
            )
        return super().render(cleaned)
