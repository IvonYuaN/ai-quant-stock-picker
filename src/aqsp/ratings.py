from __future__ import annotations

TRADABLE_RATINGS = frozenset({"buy_candidate", "strong_buy_candidate"})


def is_tradable_rating(rating: object) -> bool:
    return str(rating or "").strip() in TRADABLE_RATINGS
