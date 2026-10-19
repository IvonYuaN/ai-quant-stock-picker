"""隔夜外围的两个脆弱性：上游整批抖空 + 部分缺失被当成有效结果锁 5 分钟。

prod 实测（2026-10-19 11:3x，连续三轮，每轮间隔 3s）：

| 轮次 | dji | spx | ndx | hsi | hstech |
|---|---|---|---|---|---|
| 0 | OK | OK | OK | OK | OK |
| 1 | **空** | **空** | **空** | OK | OK |
| 2 | OK | OK | OK | OK | **空** |

⇒ 东财 `stock/get` 会**整批**抖空，不是某一档坏了。

而 `get_global_indices` 原来用 `valid=bool`：只要返回了至少一条就算有效，于是
「2/5」这种部分缺失会被缓存满 300s，面板上只剩两个指数 —— 看起来像「源只有
这两个」，比全空更容易误导（全空至少会显示「未返回」）。
"""

from __future__ import annotations

import pytest

import gstock
import market

_EMPTY_KEYS = ("dji", "spx", "ndx", "hsi", "hstech")


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(gstock, "_INDEX_RETRY_SLEEP", 0)


def _quote(price: float = 100.0, f86: object = 1792180800) -> dict:
    return {"f43": price, "f59": 2, "f170": 125, "f86": f86}


def _patch(monkeypatch, empties_per_pass: list[set[str]]):
    """按轮次决定哪些 secid 返回空；记录每次请求的 key。

    第 0 轮 = 前 ``len(_INDICES)`` 次调用（首次全量），之后都属于第 1 轮（补取）。
    不用「调用数 % N」推断轮次 —— 补取数量恰好是 N 的倍数时会判错。
    """
    calls: list[str] = []
    plan = list(empties_per_pass)
    by_secid = {idx["secid"]: idx["key"] for idx in gstock._INDICES}
    n_indices = len(gstock._INDICES)

    def fake(secid: str, fields: str):
        key = by_secid[secid]
        calls.append(key)
        phase = 0 if len(calls) <= n_indices else 1
        empties = plan[min(phase, len(plan) - 1)]
        return None if key in empties else _quote()

    monkeypatch.setattr(gstock, "_push2_stock_get", fake)
    return calls


# --------------------------------------------------------------------------
# 补取：只补缺的那些
# --------------------------------------------------------------------------


def test_no_retry_when_all_present(monkeypatch, no_sleep) -> None:
    calls = _patch(monkeypatch, [set()])
    rows = gstock.global_indices()

    assert len(rows) == 5
    assert len(calls) == 5  # 齐全就不补取


def test_retries_only_missing_index(monkeypatch, no_sleep) -> None:
    """只补缺的那一档，不是整批重来（省上游请求）。"""
    calls = _patch(monkeypatch, [{"dji"}, set()])
    rows = gstock.global_indices()

    assert len(rows) == 5
    assert calls.count("dji") == 2  # 补了一次
    assert calls.count("spx") == 1  # 没白补


def test_order_preserved_after_retry(monkeypatch, no_sleep) -> None:
    """补取的档位会追加到尾部，必须按 _INDICES 顺序排回来。"""
    _patch(monkeypatch, [{"dji"}, set()])
    keys = [r["key"] for r in gstock.global_indices()]
    assert keys == list(_EMPTY_KEYS)


def test_gives_up_after_single_retry(monkeypatch, no_sleep) -> None:
    """两次都空就如实少一档，不做无限重试。"""
    calls = _patch(monkeypatch, [{"spx"}, {"spx"}])
    rows = gstock.global_indices()

    assert [r["key"] for r in rows] == ["dji", "ndx", "hsi", "hstech"]
    assert calls.count("spx") == 2  # 原始 + 补取，各一次


def test_retry_covers_batch_flake(monkeypatch, no_sleep) -> None:
    """整批抖空（三个美股指数同时空）也能靠一次补取救回来。"""
    _patch(monkeypatch, [{"dji", "spx", "ndx"}, set()])
    assert len(gstock.global_indices()) == 5


# --------------------------------------------------------------------------
# 完整性谓词
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rows", [[], None, "x", [{"key": "dji"}]])
def test_is_complete_false_for_partial(rows) -> None:
    assert gstock.is_complete(rows) is False


def test_is_complete_true_only_when_all_five() -> None:
    full = [{"key": k} for k in _EMPTY_KEYS]
    assert gstock.is_complete(full) is True
    assert gstock.is_complete(full[:4]) is False


def test_is_complete_counts_indices_not_hardcoded_five() -> None:
    """档位表增删时谓词要跟着变，不能写死 5。"""
    assert gstock.is_complete([{}] * len(gstock._INDICES)) is True
    assert gstock.is_complete([{}] * (len(gstock._INDICES) - 1)) is False


# --------------------------------------------------------------------------
# 缓存：部分缺失不能锁满 5 分钟
# --------------------------------------------------------------------------


@pytest.fixture()
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "_MARKET_CACHE_DIR", str(tmp_path / "mc"))
    return tmp_path / "mc"


def _age(key: str, seconds: float) -> None:
    """把落盘缓存的时间戳往前推，模拟已过 N 秒（不真睡）。"""
    import json

    path = market._cache_path(key)
    with open(path, encoding="utf-8") as fh:
        entry = json.load(fh)
    entry["ts"] -= seconds
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entry, fh, ensure_ascii=False, default=str)


def test_partial_result_expires_after_negative_ttl(monkeypatch, isolated_cache) -> None:
    """部分缺失（2/5）过了 60s 必须重新取数，不能等到 300s。"""
    calls: list[int] = []

    def partial():
        calls.append(1)
        return [{"key": "dji"}, {"key": "hsi"}]

    monkeypatch.setattr(gstock, "global_indices", partial)
    market.get_global_indices()
    assert len(calls) == 1

    _age("global_indices", market._NEG_TTL + 1)
    market.get_global_indices()
    assert len(calls) == 2


def test_complete_result_holds_full_ttl(monkeypatch, isolated_cache) -> None:
    """齐全（5/5）照旧吃满 300s，修部分缺失不能把正常路径也缩短。"""
    calls: list[int] = []

    def full():
        calls.append(1)
        return [{"key": k} for k in _EMPTY_KEYS]

    monkeypatch.setattr(gstock, "global_indices", full)
    market.get_global_indices()

    _age("global_indices", market._NEG_TTL + 1)
    market.get_global_indices()
    assert len(calls) == 1  # 仍在 300s 内


def test_negative_ttl_shorter_than_normal() -> None:
    """常量关系本身要守住，否则这次修复空转。"""
    assert market._NEG_TTL < market._TTL
