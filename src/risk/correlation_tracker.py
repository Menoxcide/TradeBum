from __future__ import annotations

from collections import deque


class CorrelationTracker:
    """From the scaffolding doc. Note this encodes a mean-reversion belief
    (fade a same-side win streak) that sits in tension with price_momentum's
    continuation belief -- see README.md, 'momentum vs. mean-reversion
    tension' for why that's worth watching in the sensitivity report."""

    def __init__(self, lookback: int = 10):
        self.trade_history: deque = deque(maxlen=lookback)

    def record(self, side: str, pnl: float) -> None:
        self.trade_history.append({"side": side, "pnl": pnl})

    def get_adjustment(self, proposed_side: str) -> float:
        recent = list(self.trade_history)[-3:]
        recent_sides = [t["side"] for t in recent]
        recent_results = [t["pnl"] for t in recent]

        if len(recent_sides) >= 3 and all(s == proposed_side for s in recent_sides) and all(p > 0 for p in recent_results):
            return 0.5  # halve size after a 3-win same-side streak

        if len(recent_sides) >= 2 and all(s == proposed_side for s in recent_sides[-2:]):
            if all(p < 0 for p in recent_results[-2:]):
                return 0.0  # two same-side losses in a row -- skip

        return 1.0
