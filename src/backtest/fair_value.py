"""
fair_value_prob_up: the probability that a window finishes UP given the
move-so-far and time-remaining, IF there is zero true directional edge
(pure driftless random walk). This is the benchmark "model_prob" and
"market_prob" should be compared against -- not a flat 50%.

Why this matters: close to a window's expiry, being up $X with only a
little time left is mechanically likely to still be up at the close, simply
because there's less remaining time for the random walk to erase the move
-- NOT because of any real predictability. A backtest that benchmarks
against flat 50% will read this geometric effect as "momentum edge" even
when there is none. See README.md, "the fair-value benchmark" section.
"""

from __future__ import annotations

import math
import statistics


def fair_value_prob_up(move_usd: float, remaining_seconds: float, vol_per_sqrt_sec: float) -> float:
    """Phi(move_usd / (vol_per_sqrt_sec * sqrt(remaining_seconds))) --
    reflection-principle probability for a driftless random walk."""
    if vol_per_sqrt_sec <= 0 or remaining_seconds <= 0:
        if move_usd > 0:
            return 1.0
        if move_usd < 0:
            return 0.0
        return 0.5
    z = move_usd / (vol_per_sqrt_sec * math.sqrt(remaining_seconds))
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def estimate_vol_per_sqrt_sec(bars, min_bars: int = 5):
    """Estimates diffusion volatility (price units per sqrt(second)) from a
    list of Bar objects, normalizing each step by sqrt(its time delta) so it
    works even if bar spacing isn't perfectly uniform. Returns None if there
    isn't enough history yet."""
    if len(bars) < min_bars:
        return None
    normalized_steps = []
    for i in range(1, len(bars)):
        dt = (bars[i].timestamp_ms - bars[i - 1].timestamp_ms) / 1000.0
        if dt <= 0:
            continue
        normalized_steps.append((bars[i].close - bars[i - 1].close) / math.sqrt(dt))
    if len(normalized_steps) < min_bars:
        return None
    return statistics.pstdev(normalized_steps)
