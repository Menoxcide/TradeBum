"""
price_momentum: this is the ONLY module that proposes a candidate direction
(the live repo's actual strategy: "confirm BTC has moved ~$70-100, enter
WITH momentum"). All other modules just vote on whether they agree.
"""

from __future__ import annotations

from typing import Optional

from src.data.schema import MarketState
from src.signal_engine.base_filter import BaseFilter


def propose_direction(market_state: MarketState, min_move_usd: float) -> Optional[str]:
    """Returns "UP", "DOWN", or None if the move so far is too small to
    treat this window as having a signal at all."""
    move = market_state["btc_price"] - market_state["window_open_price"]
    if abs(move) < min_move_usd:
        return None
    return "UP" if move > 0 else "DOWN"


class PriceMomentumFilter(BaseFilter):
    name = "price_momentum"

    def __init__(self, min_move_usd: float = 70.0, strong_move_usd: float = 150.0):
        self.min_move_usd = min_move_usd
        self.strong_move_usd = strong_move_usd

    def evaluate(self, market_state: MarketState, candidate_direction: str) -> tuple[float, float]:
        move = market_state["btc_price"] - market_state["window_open_price"]
        abs_move = abs(move)
        actual_direction = "UP" if move > 0 else "DOWN"

        if abs_move < self.min_move_usd:
            return 50.0, 0.1  # below the gate -- caller should have skipped already

        # scales 50 (barely past threshold) -> 100 (at/above strong_move_usd)
        span = max(1.0, self.strong_move_usd - self.min_move_usd)
        strength = min(1.0, (abs_move - self.min_move_usd) / span)
        raw = 50.0 + 50.0 * strength

        # if candidate_direction somehow disagrees with the move's own sign
        # (shouldn't happen if the caller used propose_direction()), flip
        if actual_direction != candidate_direction:
            raw = 100.0 - raw

        confidence = min(1.0, 0.4 + 0.6 * strength)
        return raw, confidence
