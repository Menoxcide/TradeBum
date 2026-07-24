from __future__ import annotations

import math

from src.data.schema import MarketState
from src.signal_engine.base_filter import BaseFilter


class VolatilityRegimeFilter(BaseFilter):
    """Direction-agnostic. Peaks at a configurable 'sweet spot' ratio of
    current ATR to its median: too quiet -> moves are noise/likely to mean
    revert; too wild -> spreads widen and outcomes get less predictable."""

    name = "volatility_regime"

    def __init__(self, sweet_spot_ratio: float = 1.3, width: float = 0.6):
        self.sweet_spot_ratio = sweet_spot_ratio
        self.width = width  # larger = more forgiving falloff

    def evaluate(self, market_state: MarketState, candidate_direction: str) -> tuple[float, float]:
        atr = market_state.get("atr_5m", 0.0)
        atr_median = market_state.get("atr_median", 0.0)
        if atr_median <= 0:
            return 50.0, 0.0

        ratio = atr / atr_median
        # Gaussian bump centered on sweet_spot_ratio, scaled to land in [20, 100]
        z = (ratio - self.sweet_spot_ratio) / self.width
        bump = math.exp(-0.5 * z * z)  # 1.0 at the sweet spot, decays outward
        raw = 20.0 + 80.0 * bump

        confidence = 0.8  # this is a regime read, not a noisy per-tick signal
        return raw, confidence
