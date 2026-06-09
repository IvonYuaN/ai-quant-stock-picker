from __future__ import annotations

TRADABLE_RATINGS = frozenset({"buy_candidate", "strong_buy_candidate"})
RATING_LABELS = {
    "strong_buy_candidate": "纸面重点复核",
    "buy_candidate": "观察候选",
    "watch": "候选观察池",
    "avoid": "仅观察",
}
PORTFOLIO_ACTION_LABELS = {
    "promote": "上调优先级",
    "downgrade": "降级观察",
    "keep": "维持原排序",
}


def is_tradable_rating(rating: object) -> bool:
    return str(rating or "").strip() in TRADABLE_RATINGS


def rating_label(rating: object) -> str:
    key = str(rating or "").strip()
    return RATING_LABELS.get(key, key or "仅观察")


def portfolio_action_label(action: object) -> str:
    key = str(action or "").strip()
    return PORTFOLIO_ACTION_LABELS.get(key, key or "维持原排序")
