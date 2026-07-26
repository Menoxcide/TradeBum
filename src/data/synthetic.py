"""
FAKE DATA GENERATOR — for testing the harness's plumbing only.

This produces a random walk with no injected predictability. It exists so
you can run the full pipeline end-to-end today and confirm nothing crashes,
and so the harness has a built-in self-check: because this data has no real
edge in it by construction, a correctly-built backtester should report a
win rate whose confidence interval straddles 50% and an EV that is not
distinguishable from zero.

If a run on THIS synthetic data ever shows a strong, confident edge, that
is a bug in the harness (most likely look-ahead bias), not a discovery.
Do not use output from this generator to make any decision about the real
strategy — swap in DataProvider with real CSVs for that (see README.md).
"""

from __future__ import annotations

import math
import random

from src.backtest.fair_value import fair_value_prob_up
from src.data.schema import (
    Bar,
    FundingSnapshot,
    OrderBookSnapshot,
    ResolvedWindow,
    infer_bar_duration_ms,
)


def generate_synthetic_dataset(
    n_windows: int = 3000,
    start_price: float = 100_000.0,
    seed: int = 42,
    bar_seconds: int = 5,
    entry_offset_sec: int = 120,
    step_sigma: float = 9.0,
):
    """Returns (bars, orderbook_snapshots, funding_snapshots, resolutions),
    all as plain lists of the dataclasses in schema.py, covering `n_windows`
    consecutive fake 5-minute windows."""
    rng = random.Random(seed)

    bars: list[Bar] = []
    obs: list[OrderBookSnapshot] = []
    funding: list[FundingSnapshot] = []
    resolutions: list[ResolvedWindow] = []

    price = start_price
    t_ms = 0
    bars_per_window = 300 // bar_seconds
    decision_elapsed_sec = 300 - entry_offset_sec
    vol_per_sqrt_sec = step_sigma / math.sqrt(bar_seconds)  # true generating vol, known exactly since we're generating it

    for w in range(n_windows):
        window_start_ms = t_ms
        window_open_price = price
        funding_rate = rng.gauss(0.00005, 0.00008)  # small, noisy, unbiased
        price_at_decision = None

        for bar_i in range(bars_per_window):
            elapsed_sec_in_window = bar_i * bar_seconds
            if price_at_decision is None and elapsed_sec_in_window >= decision_elapsed_sec:
                price_at_decision = price  # price as of just before this bar, i.e. at the decision timestamp

            # pure random walk, no drift, no memory -> nothing here should
            # be predictable from anything else generated in this file
            step = rng.gauss(0, step_sigma)
            new_price = max(1.0, price + step)
            o, c = price, new_price
            h = max(o, c) + abs(rng.gauss(0, 2.0))
            l = min(o, c) - abs(rng.gauss(0, 2.0))
            vol = abs(rng.gauss(1.2, 0.6)) + 0.05
            bars.append(Bar(t_ms, o, h, l, c, vol))

            # synthetic order book: symmetric noise around price, so
            # imbalance is mean-zero and uncorrelated with the next move
            mid = new_price
            spread = max(0.5, rng.gauss(1.5, 0.4))
            bid0 = mid - spread / 2
            ask0 = mid + spread / 2
            imbalance_noise = rng.gauss(0, 1.0)  # not correlated with outcome
            bid_sizes = [max(0.01, 2.0 + imbalance_noise + rng.gauss(0, 0.5)) for _ in range(3)]
            ask_sizes = [max(0.01, 2.0 - imbalance_noise + rng.gauss(0, 0.5)) for _ in range(3)]
            obs.append(
                OrderBookSnapshot(
                    timestamp_ms=t_ms,
                    bid_prices=[bid0, bid0 - 1, bid0 - 2],
                    bid_sizes=bid_sizes,
                    ask_prices=[ask0, ask0 + 1, ask0 + 2],
                    ask_sizes=ask_sizes,
                )
            )
            funding.append(FundingSnapshot(t_ms, funding_rate))

            price = new_price
            t_ms += bar_seconds * 1000

        window_end_ms = t_ms
        close_price = price
        if price_at_decision is None:  # only if entry_offset_sec >= window length
            price_at_decision = window_open_price

        # Price the synthetic market at FAIR VALUE given the move already
        # realized and the time remaining (see fair_value.py) -- this is
        # what an efficient market does, and is NOT a function of the
        # eventual outcome (close_price isn't used here, so this is not
        # look-ahead). A flat-50% market, by contrast, would hand a fake
        # "edge" to anyone entering late after a move, purely from window
        # geometry rather than real predictability -- that was the bug the
        # smoke test caught. Small noise on top represents minor real-world
        # market inefficiency/estimation error.
        move_at_decision = price_at_decision - window_open_price
        fair_prob = fair_value_prob_up(move_at_decision, entry_offset_sec, vol_per_sqrt_sec)
        market_prob_up_at_decision = min(0.98, max(0.02, fair_prob + rng.gauss(0, 0.02)))

        resolutions.append(
            ResolvedWindow(
                window_start_ms=window_start_ms,
                window_end_ms=window_end_ms,
                open_price=window_open_price,
                close_price=close_price,
                market_prob_up_at_decision=market_prob_up_at_decision,
            )
        )

    return bars, obs, funding, resolutions


class InMemoryProvider:
    """Same interface as DataProvider (provider.py) but backed by lists
    already in memory instead of CSVs -- used to feed synthetic data into
    the same backtest engine real data would go through."""

    def __init__(self, bars, orderbook_snapshots, funding_snapshots, resolutions):
        from bisect import bisect_right  # local import to keep this class standalone
        self._bisect_right = bisect_right
        self.bars = bars
        self.orderbook_snapshots = orderbook_snapshots
        self.funding_snapshots = funding_snapshots
        self.resolutions = resolutions
        self._bar_ts = [b.timestamp_ms for b in bars]
        self._ob_ts = [o.timestamp_ms for o in orderbook_snapshots]
        self._funding_ts = [f.timestamp_ms for f in funding_snapshots]
        self.bar_duration_ms = infer_bar_duration_ms(bars)

    def bars_as_of(self, ts_ms: int, lookback: int = 30):
        """Only bars that had CLOSED by ts_ms -- see DataProvider.bars_as_of
        for why the bar opening exactly at ts_ms is future information. The
        synthetic provider must enforce the identical rule, or the harness
        self-check runs on a laxer guarantee than production data does and
        stops being able to catch the bug it exists to catch."""
        if self.bar_duration_ms <= 0:
            idx = self._bisect_right(self._bar_ts, ts_ms)
        else:
            idx = self._bisect_right(self._bar_ts, ts_ms - self.bar_duration_ms)
        return self.bars[max(0, idx - lookback):idx]

    def orderbook_as_of(self, ts_ms: int):
        idx = self._bisect_right(self._ob_ts, ts_ms) - 1
        return self.orderbook_snapshots[idx] if idx >= 0 else None

    def funding_as_of(self, ts_ms: int) -> float:
        idx = self._bisect_right(self._funding_ts, ts_ms) - 1
        return self.funding_snapshots[idx].funding_rate if idx >= 0 else 0.0
