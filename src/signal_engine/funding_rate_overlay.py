from __future__ import annotations

from src.data.schema import MarketState
from src.signal_engine.base_filter import BaseFilter


class FundingRateOverlay(BaseFilter):
    """Neutral (50) baseline. Penalizes the candidate direction when perp
    funding suggests that side is already crowded (overleveraged longs
    paying high positive funding, or overleveraged shorts paying very
    negative funding) -- per the scaffolding doc: modifier of +/-15."""

    name = "funding_rate_overlay"

    def __init__(self, crowded_threshold: float = 0.0001, max_penalty: float = 15.0):
        self.crowded_threshold = crowded_threshold  # 0.0001 == 0.01%
        self.max_penalty = max_penalty

    def evaluate(self, market_state: MarketState, candidate_direction: str) -> tuple[float, float]:
        funding = market_state.get("funding_rate", 0.0)
        raw = 50.0

        if candidate_direction == "UP" and funding > self.crowded_threshold:
            excess = (funding - self.crowded_threshold) / max(self.crowded_threshold, 1e-9)
            raw -= min(self.max_penalty, self.max_penalty * min(1.0, excess))
        elif candidate_direction == "DOWN" and funding < -self.crowded_threshold:
            excess = (abs(funding) - self.crowded_threshold) / max(self.crowded_threshold, 1e-9)
            raw -= min(self.max_penalty, self.max_penalty * min(1.0, excess))

        confidence = 0.5  # funding is a slower-moving, secondary confirmation signal
        return raw, confidence
