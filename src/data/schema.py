"""
Data schema for the BTC 5m backtest harness.

These are the typed shapes that a DataProvider must produce and that the
signal/sizing modules consume. Keeping this as one file means the contract
between "data you plug in" and "code that scores it" is explicit and in one
place, instead of implied by whatever keys happen to show up in a dict.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional, TypedDict


@dataclass
class Bar:
    """One OHLCV bar. Use whatever granularity you have (1s/5s/1m); the
    momentum/volume/ATR modules just need a short recent history."""
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def infer_bar_duration_ms(bars: "list[Bar]") -> int:
    """Median spacing between consecutive bar opens.

    Inferred rather than assumed on purpose. A provider that hardcodes the
    duration silently becomes wrong the moment it is handed a different
    granularity -- and the failure is a look-ahead leak (see bars_as_of),
    not a crash, so nothing would tell you. Median rather than mean because
    a single gap in the history (an exchange outage, a missing file) would
    drag a mean far off the true interval.

    Returns 0 when there is not enough history to tell, which callers must
    treat as "unknown" rather than as a zero-length bar.
    """
    if len(bars) < 2:
        return 0
    deltas = [
        bars[i].timestamp_ms - bars[i - 1].timestamp_ms
        for i in range(1, len(bars))
        if bars[i].timestamp_ms > bars[i - 1].timestamp_ms
    ]
    if not deltas:
        return 0
    return int(statistics.median(deltas))


@dataclass
class OrderBookSnapshot:
    """Top-of-book snapshot. Only the first few levels matter for the
    imbalance signal — pass however many you have, 3+ levels is enough."""
    timestamp_ms: int
    bid_prices: list[float] = field(default_factory=list)
    bid_sizes: list[float] = field(default_factory=list)
    ask_prices: list[float] = field(default_factory=list)
    ask_sizes: list[float] = field(default_factory=list)

    def top_n_notional(self, n: int = 3) -> tuple[float, float]:
        bid_notional = sum(
            p * s for p, s in zip(self.bid_prices[:n], self.bid_sizes[:n])
        )
        ask_notional = sum(
            p * s for p, s in zip(self.ask_prices[:n], self.ask_sizes[:n])
        )
        return bid_notional, ask_notional

    @property
    def mid(self) -> Optional[float]:
        if self.bid_prices and self.ask_prices:
            return (self.bid_prices[0] + self.ask_prices[0]) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.bid_prices and self.ask_prices:
            return self.ask_prices[0] - self.bid_prices[0]
        return None


@dataclass
class FundingSnapshot:
    timestamp_ms: int
    funding_rate: float  # e.g. 0.0001 == 0.01%


@dataclass
class ResolvedWindow:
    """One resolved BTC 5-minute up/down window. This is the ground truth
    the backtester checks proposed trades against."""
    window_start_ms: int
    window_end_ms: int
    open_price: float
    close_price: float
    market_prob_up_at_decision: Optional[float] = None  # Polymarket implied
    # prob of "UP" at the moment the strategy would have decided, if you have it

    # How stale the quote above was at the decision moment. The historical
    # odds series is 1-minute fidelity, so the last print at or before the
    # nominal decision time is typically ~56s old. engine.py shifts the
    # decision back by this much: scoring a signal at a moment newer than
    # the price you could have paid is look-ahead, and it fabricated roughly
    # $2/trade of edge on the first real-odds run.
    prob_age_sec: Optional[float] = None

    # The venue's own settlement, when known. The market resolves on
    # Chainlink while the signals read Binance, so the two can legitimately
    # disagree on a window; when the real resolution is available it wins.
    resolved_outcome: Optional[str] = None

    @property
    def outcome(self) -> str:
        if self.resolved_outcome:
            return self.resolved_outcome
        return "UP" if self.close_price >= self.open_price else "DOWN"

    @property
    def move_usd(self) -> float:
        return self.close_price - self.open_price


class MarketState(TypedDict, total=False):
    """The dict passed into every filter module's evaluate(). Mirrors the
    'market_state' dict referenced throughout the original scaffolding doc,
    made explicit here so filters and data providers agree on field names."""
    timestamp_ms: int
    btc_price: float
    window_open_price: float
    seconds_to_close: int
    bars_recent: list[Bar]
    orderbook: OrderBookSnapshot
    funding_rate: float
    atr_5m: float
    atr_median: float
    volume_current: float
    volume_median: float
    market_prob_up: float
