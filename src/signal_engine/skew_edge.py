from __future__ import annotations

from src.data.schema import MarketState
from src.signal_engine.base_filter import BaseFilter


class SkewEdgeCalculator(BaseFilter):
    """Scores how much of the move is still 'unpriced' by the market.
    A big BTC move that the market has already marked to 90/10 odds has
    little edge left -- you'd be chasing. The same move at 55/45 odds means
    the market hasn't caught up yet, which is where an edge (if any) lives.

    This is a scoring input to the composite; risk/ev_gate.py does the
    final go/no-go EV check with real fees and spread."""

    name = "skew_edge"

    def evaluate(self, market_state: MarketState, candidate_direction: str) -> tuple[float, float]:
        market_prob_up = market_state.get("market_prob_up")
        if market_prob_up is None:
            return 50.0, 0.0

        raw = 100.0 * (1.0 - market_prob_up) if candidate_direction == "UP" else 100.0 * market_prob_up

        # near-0 or near-1 odds often mean the window is nearly resolved
        # already or the book is too thin to trust -- trust it less
        extremity = min(market_prob_up, 1.0 - market_prob_up)  # 0 at edges, 0.5 at center
        confidence = min(1.0, extremity / 0.15)
        return raw, confidence
