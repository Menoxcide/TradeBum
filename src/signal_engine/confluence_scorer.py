"""
ConfluenceScorer: composite 0-100 score, adapted from the scaffolding doc.

Two changes from the doc's version:
  1. It now needs a `candidate_direction` (from price_momentum.propose_direction)
     because a magnitude score alone can't tell you which side to trade --
     see base_filter.py's design note.
  2. score() returns per-module raw/confidence/weighted_contribution as
     dicts of plain floats (not objects) so it round-trips cleanly through
     the JSON trade log.
"""

from __future__ import annotations

from typing import Optional

from src.data.schema import MarketState
from src.signal_engine.funding_rate_overlay import FundingRateOverlay
from src.signal_engine.orderbook_imbalance import OrderBookImbalance
from src.signal_engine.price_momentum import PriceMomentumFilter, propose_direction
from src.signal_engine.skew_edge import SkewEdgeCalculator
from src.signal_engine.volatility_regime import VolatilityRegimeFilter
from src.signal_engine.volume_profile import VolumeProfileFilter


class ConfluenceScorer:
    def __init__(self, weights: dict, min_move_usd: float = 70.0):
        self.price_momentum = PriceMomentumFilter(min_move_usd=min_move_usd)
        self.modules = {
            "price_momentum": self.price_momentum,
            "orderbook_imbalance": OrderBookImbalance(),
            "volume_profile": VolumeProfileFilter(),
            "volatility_regime": VolatilityRegimeFilter(),
            "funding_rate": FundingRateOverlay(),
            "skew_edge": SkewEdgeCalculator(),
        }
        self.weights = weights
        self.min_move_usd = min_move_usd

    def score(self, market_state: MarketState) -> tuple[Optional[float], Optional[str], dict]:
        """
        Returns (composite_score, candidate_direction, debug_breakdown).
        composite_score/candidate_direction are None if the move is too
        small to have a signal at all (caller should skip the window).
        """
        candidate_direction = propose_direction(market_state, self.min_move_usd)
        if candidate_direction is None:
            return None, None, {}

        breakdown = {}
        total_weight = 0.0
        weighted_sum = 0.0

        for name, module in self.modules.items():
            raw, confidence = module.evaluate(market_state, candidate_direction)
            weight = self.weights.get(name, 0)

            breakdown[name] = {
                "raw": raw,
                "confidence": confidence,
                "weight": weight,
                "weighted_contribution": raw * confidence * weight / 100.0,
            }
            weighted_sum += raw * confidence * weight
            total_weight += weight * confidence

        composite = weighted_sum / total_weight if total_weight > 0 else 50.0
        return composite, candidate_direction, breakdown
