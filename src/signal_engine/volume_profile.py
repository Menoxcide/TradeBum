from __future__ import annotations

from src.data.schema import MarketState
from src.signal_engine.base_filter import BaseFilter


class VolumeProfileFilter(BaseFilter):
    """Direction-agnostic: a volume spike says 'this move is real', which
    supports whatever direction momentum already proposed. It doesn't argue
    for a side on its own."""

    name = "volume_profile"

    def evaluate(self, market_state: MarketState, candidate_direction: str) -> tuple[float, float]:
        current = market_state.get("volume_current", 0.0)
        median = market_state.get("volume_median", 0.0)
        if median <= 0:
            return 50.0, 0.0

        ratio = current / median
        # ratio 1.0 (typical) -> 50, ratio 2.0+ -> approaches 100, ratio <0.5 -> approaches 0
        raw = max(0.0, min(100.0, 50.0 + 35.0 * (ratio - 1.0)))

        # low confidence in thin/quiet volume regimes -- a "confirming" spike
        # on a dead market doesn't mean much
        confidence = 0.3 if ratio < 0.7 else min(1.0, 0.5 + 0.25 * (ratio - 0.7))
        return raw, confidence
