"""
KellyVolatilitySizer, adapted from the scaffolding doc.

Two fixes from the original snippet:
  1. `b = avg_win / avg_loss` raised ZeroDivisionError whenever avg_loss
     was 0 (e.g. no losses yet). Now guarded.
  2. Added `min_trades_for_kelly`: below that sample size, win_rate/avg_win/
     avg_loss are too noisy to size off of (this is the small-sample
     fragility flagged in the review) -- falls back to a small fixed
     fraction instead of trusting an early, noisy Kelly estimate.
"""

from __future__ import annotations


class KellyVolatilitySizer:
    def __init__(self, profile: dict, min_trades_for_kelly: int = 50, fallback_pct: float = 0.01):
        self.allocation = profile["total_allocation_usdc"]
        self.max_pct = profile["max_position_pct"]
        self.kelly_frac = profile["kelly_fraction"]
        self.min_trades_for_kelly = min_trades_for_kelly
        self.fallback_pct = fallback_pct

    def calculate(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,  # pass as a positive number (magnitude of a loss)
        current_atr: float,
        median_atr: float,
        n_trades_so_far: int = 0,
    ) -> dict:

        if n_trades_so_far < self.min_trades_for_kelly or avg_win <= 0 or avg_loss <= 0:
            final_pct = min(self.fallback_pct, self.max_pct)
            return {
                "position_size_usdc": self.allocation * final_pct,
                "kelly_raw": None,
                "kelly_fractional": None,
                "vol_modifier": None,
                "final_pct": final_pct,
                "rationale": (
                    f"n_trades_so_far={n_trades_so_far} < min_trades_for_kelly="
                    f"{self.min_trades_for_kelly} (or no loss data yet) -- using "
                    f"fixed fallback size, not a Kelly estimate."
                ),
            }

        b = avg_win / avg_loss
        p = win_rate
        q = 1 - p
        kelly_pct = (b * p - q) / b if b != 0 else 0.0
        kelly_pct = max(0.0, kelly_pct)

        adjusted_pct = kelly_pct * self.kelly_frac

        vol_ratio = current_atr / median_atr if median_atr > 0 else 1.0
        vol_modifier = min(1.5, max(0.5, vol_ratio))

        final_pct = adjusted_pct * vol_modifier
        final_pct = min(final_pct, self.max_pct)

        return {
            "position_size_usdc": self.allocation * final_pct,
            "kelly_raw": kelly_pct,
            "kelly_fractional": adjusted_pct,
            "vol_modifier": vol_modifier,
            "final_pct": final_pct,
            "rationale": (
                f"Kelly={kelly_pct:.2%}, frac={self.kelly_frac}, "
                f"vol_mod={vol_modifier:.2f}, n_trades={n_trades_so_far}"
            ),
        }
