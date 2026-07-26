"""Shared fixtures and dataset builders.

Two jobs here.

1. Cache the synthetic datasets. Generating 8,000 windows takes seconds and
   several tests want the same data; regenerating per test made the suite
   slow enough that people stop running it, which is the real failure mode.

2. Give tests a way to build SMALL, hand-specified datasets. Most of what
   needs testing in engine.py is a specific gate firing on a specific input,
   and reaching that through an 8,000-window random walk is both slow and
   accidental -- the test passes or fails depending on whether the generator
   happened to produce a qualifying window. `build_provider` below lets a
   test state the exact price path it wants and get a provider back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.schema import Bar, FundingSnapshot, OrderBookSnapshot, ResolvedWindow
from src.data.synthetic import InMemoryProvider, generate_synthetic_dataset

WINDOW_MS = 300_000
BAR_MS = 30_000

# The config the smoke tests use. Kept here so every test file shares one
# definition and a change to it cannot silently apply to only half of them.
BASE_CONFIG = {
    "total_allocation_usdc": 1000,
    "max_position_pct": 0.05,
    "daily_loss_limit_pct": 0.10,
    "max_trades_per_day": 5,
    "min_btc_move_usd": 70,
    "min_volume_percentile": 60,
    "min_realized_vol_5m": 0.0,
    "signal_weights": {
        "price_momentum": 25, "orderbook_imbalance": 20, "volume_profile": 20,
        "volatility_regime": 15, "funding_rate": 10, "skew_edge": 10,
    },
    "min_confluence_score": 65,
    "kelly_fraction": 0.25,
    "min_trades_for_kelly": 50,
    "fallback_pct": 0.01,
    "min_book_depth_usdc": 500,
    "taker_fee": 0.0,
    "assumed_polymarket_spread": 0.02,
    "slippage_buffer": 0.005,
    "fallback_fixed_pct": 0.01,
}


@pytest.fixture
def config():
    """A fresh copy per test, so a test that mutates it cannot leak into
    the next one."""
    import copy
    return copy.deepcopy(BASE_CONFIG)


# ------------------------------------------------------------ synthetic

_synthetic_cache: dict = {}


def synthetic_provider(n_windows: int = 1500, seed: int = 42) -> InMemoryProvider:
    key = (n_windows, seed)
    if key not in _synthetic_cache:
        _synthetic_cache[key] = generate_synthetic_dataset(n_windows=n_windows, seed=seed)
    bars, obs, funding, resolutions = _synthetic_cache[key]
    return InMemoryProvider(bars, obs, funding, resolutions)


@pytest.fixture(scope="session")
def small_synthetic():
    bars, obs, funding, resolutions = generate_synthetic_dataset(n_windows=200, seed=42)
    return InMemoryProvider(bars, obs, funding, resolutions)


# -------------------------------------------------------- hand-built data


def build_provider(
    window_specs,
    bar_ms: int = BAR_MS,
    book_notional: float = 10_000.0,
    funding_rate: float = 0.0,
    volume: float = 1.0,
):
    """Build an InMemoryProvider from an explicit list of window specs.

    Each spec is a dict:
      move_usd      price change from window open to the decision bar. This
                    is what propose_direction() reads, so it decides whether
                    the window produces a signal and in which direction.
      close_delta   price change from window open to the window close --
                    i.e. whether the trade actually wins. Defaults to
                    move_usd (momentum carries through).
      market_prob   market_prob_up_at_decision. None to exercise the
                    fair-value fallback path.
      volume        per-bar volume for this window, for the volume gates.
      book          top-of-book notional per side, for the depth gate.

    Prices move linearly across the window so the decision bar lands on
    exactly `move_usd`, which keeps the arithmetic in each test readable.
    """
    bars: list[Bar] = []
    obs: list[OrderBookSnapshot] = []
    funding: list[FundingSnapshot] = []
    resolutions: list[ResolvedWindow] = []

    bars_per_window = WINDOW_MS // bar_ms
    open_price = 100_000.0
    t = 0

    for spec in window_specs:
        move = spec.get("move_usd", 0.0)
        close_delta = spec.get("close_delta", move)
        win_volume = spec.get("volume", volume)
        win_book = spec.get("book", book_notional)
        window_start = t
        window_open = open_price

        # The engine reads bars_in_window[-1].close as btc_price, and
        # bars_as_of only returns bars CLOSED by the decision timestamp.
        # decision_ts = window_end - 120s, so with 30s bars the last usable
        # bar is the one opening at +150s (index 5).
        decision_bar_idx = (WINDOW_MS - 120_000) // bar_ms - 1

        price = window_open
        for i in range(bars_per_window):
            if i <= decision_bar_idx:
                frac = (i + 1) / (decision_bar_idx + 1)
                nxt = window_open + move * frac
            else:
                remaining = bars_per_window - 1 - decision_bar_idx
                step = (i - decision_bar_idx) / remaining if remaining else 1.0
                nxt = window_open + move + (close_delta - move) * step
            o, c = price, nxt
            bars.append(Bar(t, o, max(o, c) + 1.0, min(o, c) - 1.0, c, win_volume))
            obs.append(
                OrderBookSnapshot(
                    timestamp_ms=t,
                    bid_prices=[c - 0.5, c - 1.5, c - 2.5],
                    bid_sizes=[win_book / 3 / max(c, 1e-9)] * 3,
                    ask_prices=[c + 0.5, c + 1.5, c + 2.5],
                    ask_sizes=[win_book / 3 / max(c, 1e-9)] * 3,
                )
            )
            funding.append(FundingSnapshot(t, spec.get("funding_rate", funding_rate)))
            price = nxt
            t += bar_ms

        resolutions.append(
            ResolvedWindow(
                window_start_ms=window_start,
                window_end_ms=window_start + WINDOW_MS,
                open_price=window_open,
                close_price=window_open + close_delta,
                market_prob_up_at_decision=spec.get("market_prob"),
                prob_age_sec=spec.get("prob_age_sec"),
                resolved_outcome=spec.get("resolved_outcome"),
            )
        )
        open_price = window_open + close_delta

    return InMemoryProvider(bars, obs, funding, resolutions)


def warmup_windows(n: int = 25, **kwargs):
    """Filler windows so the engine's 20-window warm-up is satisfied before
    the window a test actually cares about. Deliberately below the move
    threshold so they never trade and never pollute the result."""
    return [dict(move_usd=0.0, close_delta=0.0, market_prob=0.5, **kwargs) for _ in range(n)]
