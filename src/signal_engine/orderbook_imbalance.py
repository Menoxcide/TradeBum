from __future__ import annotations

from src.data.schema import MarketState
from src.signal_engine.base_filter import BaseFilter


class OrderBookImbalance(BaseFilter):
    name = "orderbook_imbalance"

    def __init__(self, depth_levels: int = 3, min_depth_usdc_for_full_confidence: float = 2000.0):
        self.depth_levels = depth_levels
        self.min_depth_usdc = min_depth_usdc_for_full_confidence

    def evaluate(self, market_state: MarketState, candidate_direction: str) -> tuple[float, float]:
        book = market_state.get("orderbook")
        if book is None or not book.bid_prices or not book.ask_prices:
            return 50.0, 0.0

        bid_notional, ask_notional = book.top_n_notional(self.depth_levels)
        total = bid_notional + ask_notional
        if total <= 0:
            return 50.0, 0.0

        imbalance = (bid_notional - ask_notional) / total  # [-1, 1], + = bid-heavy

        # bid-heavy book supports UP, ask-heavy supports DOWN
        support_up = 50.0 + 50.0 * imbalance
        raw = support_up if candidate_direction == "UP" else (100.0 - support_up)

        confidence = min(1.0, total / self.min_depth_usdc)
        return raw, confidence
