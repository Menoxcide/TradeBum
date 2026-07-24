"""
Run with: python -m pytest tests/test_smoke.py -v
(or just: python tests/test_smoke.py)

These tests check the harness's PLUMBING (it runs, produces sane shapes,
doesn't leak future data). They cannot and do not check whether the real
strategy has edge -- only real historical data can tell you that.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import run_backtest
from src.backtest.stats import summarize
from src.data.synthetic import InMemoryProvider, generate_synthetic_dataset

CONFIG = {
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


def _provider(n_windows=1500, seed=42):
    bars, obs, funding, resolutions = generate_synthetic_dataset(n_windows=n_windows, seed=seed)
    return InMemoryProvider(bars, obs, funding, resolutions)


def test_runs_without_crashing():
    provider = _provider()
    for strategy in ("coinflip", "naive_momentum", "confluence"):
        res = run_backtest(provider, CONFIG, strategy=strategy)
        assert isinstance(res.trades, list)


def test_produces_some_trades():
    provider = _provider(n_windows=3000)
    res = run_backtest(provider, CONFIG, strategy="naive_momentum")
    assert len(res.trades) > 0, "naive_momentum should fire on some windows out of 3000"


def test_pnl_and_equity_curve_consistent():
    provider = _provider(n_windows=3000)
    res = run_backtest(provider, CONFIG, strategy="naive_momentum")
    running = CONFIG["total_allocation_usdc"]
    for t, eq in zip(res.trades, res.equity_curve):
        running += t["pnl_usdc"]
        assert abs(running - eq) < 1e-6, "equity curve should equal cumulative starting capital + pnl"
        assert eq == t["equity_after"]


def test_no_lookahead_in_bars_as_of():
    provider = _provider(n_windows=200)
    # pick a timestamp in the middle of the dataset
    mid_ts = provider.bars[len(provider.bars) // 2].timestamp_ms
    bars = provider.bars_as_of(mid_ts, lookback=1000)
    assert all(b.timestamp_ms <= mid_ts for b in bars), "bars_as_of leaked a future bar"


def test_bars_as_of_excludes_the_still_forming_bar():
    """Regression test for a look-ahead bug the check above does NOT catch.

    A bar is timestamped at its OPEN but its `close` is the price one whole
    interval later. So the bar starting exactly at decision_ts passes a
    `timestamp <= decision_ts` test while carrying a price from the future.
    On 5-second synthetic bars that leak is easy to miss; on the real
    1-minute bars this harness now runs on, it is 60 seconds of unknowable
    price on a 300-second window -- enough to manufacture an edge out of
    nothing. bars_as_of must return only bars that had CLOSED by ts."""
    provider = _provider(n_windows=200)
    ts = provider.bars[len(provider.bars) // 2].timestamp_ms
    returned = provider.bars_as_of(ts, lookback=1000)
    assert all(b.timestamp_ms + provider.bar_duration_ms <= ts for b in returned), (
        "bars_as_of returned a bar that had not finished forming at the query time -- "
        "its close is future information"
    )
    assert ts not in [b.timestamp_ms for b in returned], (
        "the bar opening exactly at the query time is still in-progress and must be excluded"
    )
    assert returned, "excluding in-progress bars should not empty the history"


def test_resolved_outcome_overrides_price_derived_outcome():
    """The market settles on Chainlink, the signals read Binance. When the
    real resolution is known it must win, including where the two disagree."""
    from src.data.schema import ResolvedWindow
    disagreeing = ResolvedWindow(0, 300_000, open_price=100.0, close_price=101.0,
                                 resolved_outcome="DOWN")
    assert disagreeing.outcome == "DOWN", "real resolution must override the price-derived guess"
    unchanged = ResolvedWindow(0, 300_000, open_price=100.0, close_price=100.0)
    assert unchanged.outcome == "UP", "an unchanged window resolves UP ('greater than or equal')"


def test_synthetic_data_shows_no_confident_edge():
    """The self-check described in synthetic.py's docstring. IMPORTANT: this
    checks MEAN PNL, not win rate. Win rate is the wrong metric here -- the
    synthetic market prices trades at fair value (see fair_value.py), so a
    strategy that only enters already-likely-to-win setups will legitimately
    show a high win rate with zero edge, because it also pays more for those
    setups. Mean pnl per trade is what should be indistinguishable from zero
    (modulo fees) on data with no real edge in it by construction. If this
    test fails with a confidently POSITIVE mean pnl, there's a look-ahead or
    mispricing bug somewhere in the pipeline."""
    provider = _provider(n_windows=8000, seed=99)
    res = run_backtest(provider, CONFIG, strategy="confluence")
    s = summarize(res.trades, res.equity_curve, "confluence")
    if s.n_trades >= 20:
        lo, hi = s.mean_pnl_ci
        assert lo <= 0.0, (
            f"confluence strategy shows a mean-pnl-per-trade 95% CI of "
            f"[${lo:,.2f}, ${hi:,.2f}] on PURE RANDOM synthetic data -- entirely "
            f"above zero would mean the harness found free money in fair-priced "
            f"noise, which points to a bug (most likely look-ahead), not a discovery."
        )
    else:
        print(f"  (only {s.n_trades} trades fired -- too few to check meaningfully; "
              f"this itself is a legitimate outcome once markets are fairly priced)")


def test_correlation_tracker_and_kelly_dont_crash_on_zero_losses():
    """Regression test for the original Kelly-calculator ZeroDivisionError
    bug: force a scenario with wins but (initially) no losses."""
    from src.sizing_engine.kelly_calculator import KellyVolatilitySizer
    sizer = KellyVolatilitySizer(CONFIG, min_trades_for_kelly=0)
    out = sizer.calculate(win_rate=1.0, avg_win=10.0, avg_loss=0.0, current_atr=1.0, median_atr=1.0, n_trades_so_far=100)
    assert out["position_size_usdc"] >= 0


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
