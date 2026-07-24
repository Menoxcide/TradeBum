"""
Abstract base for signal modules.

Design note (this fills a gap in the original scaffolding doc): the doc's
ConfluenceScorer combines module scores into one 0-100 composite, but a
composite magnitude alone can't tell you which side to trade. To match how
the strategy actually runs today (momentum sets the candidate direction,
other signals confirm or deny it -- see the live repo's README), every
filter here scores "how much does this evidence support the CANDIDATE
direction", not an absolute up/down score. price_momentum is what proposes
the candidate direction in the first place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.data.schema import MarketState


class BaseFilter(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, market_state: MarketState, candidate_direction: str) -> tuple[float, float]:
        """
        candidate_direction: "UP" or "DOWN" -- the side momentum has already
        proposed for this window.

        Returns (raw, confidence):
          raw        0-100, how much this signal supports candidate_direction
                     (50 = neutral/no opinion, 100 = strongly supports,
                     0 = strongly contradicts)
          confidence 0-1, how much weight this reading deserves right now
                     (e.g. a thin order book should report low confidence
                     even if the imbalance number itself looks extreme)
        """
        raise NotImplementedError
