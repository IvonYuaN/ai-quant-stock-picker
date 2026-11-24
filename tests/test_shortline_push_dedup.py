"""推送去重的守卫。

🔴 背景：2026-11-24 老大点名「一直在推送重复的内容」。
实测 09:45 / 09:48 / 09:51 / 09:54 四张卡片正文**逐字相同**，只有首行时间戳在变。
根因是去重只在单次进程内有效（cron 每 3 分钟起新进程，`new_hits` 每轮从零重算）。

这组测试守两件事：
  1. 指纹必须**忽略时间戳**，否则去重永远不生效（这条是最容易写错、也最容易「测试通过但线上仍刷屏」的）。
  2. 真·新信息（新代码 / 内容变化 / force）**必须**照推，不能把去重做成静音。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    p = ROOT / "research" / "shortline_cloud.py"
    spec = importlib.util.spec_from_file_location("scloud", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["scloud"] = m
    spec.loader.exec_module(m)
    return m


sc = _load()


CARD_0945 = """## 今日推荐 09:45
P1· 601599 浙文影业 4.02 2板·封单5万 / 603103 横店影视 18.26 2板·封单6206万
P2· 002872 天圣制药 6.45 1板·封单3157万·跳空+9.2%"""

# 与 CARD_0945 正文完全相同，只有时间戳不同 —— 就是线上刷屏的那四张
CARD_0954 = CARD_0945.replace("09:45", "09:54")


def _state(codes=(), hashes=None):
    return {"codes": list(codes), "hashes": dict(hashes or {})}


def test_fingerprint_ignores_timestamp_only():
    """只有时间戳不同 ⇒ 指纹必须相同。这是去重能生效的前提。"""
    assert sc.card_fingerprint(CARD_0945) == sc.card_fingerprint(CARD_0954)


def test_fingerprint_changes_when_body_changes():
    """正文加一行新票 ⇒ 指纹必须变，否则新信息会被静音。"""
    more = CARD_0945 + "\nP6· 603021 *ST华 3.52 2板·封单1万"
    assert sc.card_fingerprint(more) != sc.card_fingerprint(CARD_0945)


def test_fingerprint_keeps_non_timestamp_header():
    """只剥首行时间戳；正文里的 `##` 小标题是结构信息，不能被剥掉。"""
    a = "## 今日推荐 09:45\n## 打板\nP1· x"
    b = "## 今日推荐 09:45\n## 竞价\nP1· x"
    assert sc.card_fingerprint(a) != sc.card_fingerprint(b)


def test_skip_identical_card_with_same_codes():
    """线上刷屏的复现：同一批代码 + 同一张卡 ⇒ 第二轮必须跳过。

    这是本 PR 的核心断言。若它变红，说明去重没生效、仍会每 3 分钟推一遍。
    """
    codes = ["601599", "603103", "002872"]
    st = _state(codes, {"intraday": sc.card_fingerprint(CARD_0945)})
    go, why = sc.decide_push(st, "intraday", CARD_0954, codes)
    assert go is False, "正文相同、代码相同却仍要推 —— 就是老大说的重复推送"
    assert "相同" in why


def test_push_when_new_code_appears():
    """出现今天没推过的代码 ⇒ 必须推（去重不能变成静音）。"""
    st = _state(["601599", "603103"], {"intraday": sc.card_fingerprint(CARD_0945)})
    card = CARD_0945 + "\nP6· 603021 *ST华 3.52 2板·封单1万"
    go, why = sc.decide_push(st, "intraday", card, ["601599", "603103", "603021"])
    assert go is True and "新增" in why


def test_push_when_content_changes_without_new_code():
    """代码集合没变但卡片内容变了（如备注更新）⇒ 仍应推，避免把真变化吞掉。"""
    codes = ["601599"]
    st = _state(codes, {"intraday": sc.card_fingerprint(CARD_0945)})
    changed = CARD_0945.replace("封单5万", "封单8万")
    go, why = sc.decide_push(st, "intraday", changed, codes)
    assert go is True and why == "内容变化"


def test_force_always_pushes():
    """手工补跑必须能突破去重，否则出事时没法重发。"""
    codes = ["601599"]
    st = _state(codes, {"intraday": sc.card_fingerprint(CARD_0945)})
    go, why = sc.decide_push(st, "intraday", CARD_0945, codes, force=True)
    assert go is True and why == "force"


def test_intraday_and_recap_do_not_share_fingerprint():
    """盘中卡与收盘复盘是两类卡片，不能互相把对方判成「已推过」。"""
    codes = ["601599"]
    st = _state(codes, {"intraday": sc.card_fingerprint(CARD_0945)})
    recap = "## 收盘复盘 15:10\nP1· 601599 浙文影业 4.02 已平"
    go, _ = sc.decide_push(st, "recap", recap, codes)
    assert go is True, "同一批代码、不同 kind 的卡片不应被判为重复"


def test_push_state_roundtrip(tmp_path, monkeypatch):
    """落盘/读取要能往返；文件损坏时必须降级为空状态而不是崩掉整轮推送。"""
    monkeypatch.setattr(sc, "PUSH_STATE", tmp_path)
    sc.save_push_state("2026-11-24", {"codes": ["601599"], "hashes": {"intraday": "x"}})
    assert sc.load_push_state("2026-11-24")["codes"] == ["601599"]
    sc.push_state_path("2026-11-24").write_text("{ 坏 json", encoding="utf-8")
    assert sc.load_push_state("2026-11-24") == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
