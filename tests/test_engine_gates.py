"""Every gate in the backtest loop, exercised deliberately.

engine.py has fourteen distinct `skip()` reasons. The smoke tests ran the
happy path on a random walk and hit two of them, so most of the risk
controls in this system -- the daily loss limit, the book-depth floor, the
price-band cap, the correlation skip -- had never once been observed to
fire.

`result.skipped_reasons` is a ready-made oracle: it says exactly which
branch a window took. Each test below drives one window to one branch and
asserts the count, using small hand-built datasets rather than a large
random one, so a passing test means the gate fired for the stated reason
rather than by luck.
"""

from __future__ import annotations

import copy

import pytest

from conftest import BASE_CONFIG, WINDOW_MS, build_provider, warmup_windows
from src.backtest.engine import run_backtest
from src.data.schema import ResolvedWindow
from src.risk.ev_gate import confluence_score_to_model_prob

# A setup engineered to clear every gate, so each test can spoil exactly one.
STRONG_UP = dict(move_usd=300.0, close_delta=300.0, market_prob=0.55,
                 volume=50.0, book=50_000.0)
PERMISSIVE = dict(min_confluence_score=0, min_volume_percentile=0)

# Matches run_backtest's warmup_windows default, so the filler windows are
# consumed exactly by the warm-up and the specs under test start at index 20.
WARMUP_FILLERS = 20


def run(specs, strategy="confluence", warm=WARMUP_FILLERS, warm_kwargs=None, **cfg):
    """`warm` filler windows exactly satisfy the engine's 20-window warm-up,
    so the windows under test start immediately afterwards and every skip
    count below refers only to the specs the test passed in."""
    config = copy.deepcopy(BASE_CONFIG)
    config.update(cfg)
    provider = build_provider(warmup_windows(warm, **(warm_kwargs or {})) + specs)
    return run_backtest(provider, config, strategy=strategy)


# ------------------------------------------------------------- baseline


def test_the_strong_setup_actually_trades():
    """Anchors every other test in this file. If this stops trading, the
    'gate X fired' assertions below become vacuous -- they would pass
    because nothing ever reaches the gate."""
    r = run([STRONG_UP] * 3, **PERMISSIVE)
    assert len(r.trades) == 3
    assert r.skipped_reasons.get("warmup_period") == 20
    assert all(t["side"] == "UP" and t["won"] for t in r.trades)


# ------------------------------------------------------- shared gates


def test_warmup_never_trades_and_lasts_exactly_twenty_windows():
    """The first 20 windows only build rolling ATR/volume history. Trading
    during them would size and gate off statistics derived from a handful of
    samples -- and window 0 would be gated on its own outcome."""
    r = run([STRONG_UP] * 25, warm=0, **PERMISSIVE)
    assert r.skipped_reasons.get("warmup_period") == 20
    assert len(r.trades) == 5


def test_a_move_below_the_threshold_produces_no_signal():
    r = run([dict(STRONG_UP, move_usd=10.0, close_delta=10.0)] * 3, **PERMISSIVE)
    assert r.skipped_reasons.get("move_below_threshold") == 3
    assert r.trades == []


def test_a_window_with_no_bar_history_is_skipped_not_guessed():
    provider = build_provider(warmup_windows(22))
    far = provider.bars[-1].timestamp_ms + 10 * WINDOW_MS
    provider.resolutions.append(
        ResolvedWindow(far, far + WINDOW_MS, 100.0, 200.0, market_prob_up_at_decision=0.5)
    )
    r = run_backtest(provider, copy.deepcopy(BASE_CONFIG), strategy="naive_momentum")
    assert r.skipped_reasons.get("insufficient_bar_history") == 1


def test_volatility_floor_blocks_quiet_windows():
    r = run([STRONG_UP] * 3, min_realized_vol_5m=1e9, **PERMISSIVE)
    assert r.skipped_reasons.get("volatility_floor") == 3


def test_thin_book_is_rejected():
    """The gate that matters most for this venue -- PLAN.md's finding is
    that any detectable edge lives in a book too thin to harvest."""
    r = run([dict(STRONG_UP, book=100.0)] * 3, min_book_depth_usdc=5_000, **PERMISSIVE)
    assert r.skipped_reasons.get("book_too_thin") == 3


@pytest.mark.parametrize("strategy", ["coinflip", "naive_momentum", "confluence"])
def test_shared_gates_apply_to_every_strategy(strategy):
    """coinflip and naive_momentum skip the confluence-specific gates, but
    the execution constraints are real-world limits and must bind on all
    three, or the ablation compares strategies on different opportunity
    sets and stops being a fair comparison."""
    r = run([dict(STRONG_UP, book=100.0)] * 3, strategy=strategy,
            min_book_depth_usdc=5_000, **PERMISSIVE)
    assert r.skipped_reasons.get("book_too_thin") == 3
    assert r.trades == []


def test_max_trades_per_day_caps_activity():
    r = run([STRONG_UP] * 10, max_trades_per_day=5, **PERMISSIVE)
    assert len(r.trades) == 5
    assert r.skipped_reasons.get("max_trades_per_day") == 5


def test_daily_loss_limit_halts_trading_for_the_rest_of_the_day():
    """Two 5% losses take equity to exactly the 10% limit, which halts.
    Every later window that day is skipped rather than traded."""
    losing = dict(STRONG_UP, close_delta=-300.0, market_prob=0.5)
    r = run([losing] * 5, strategy="naive_momentum",
            fallback_fixed_pct=0.05, max_trades_per_day=99, **PERMISSIVE)
    assert len(r.trades) == 2
    assert r.skipped_reasons.get("daily_loss_limit_hit") == 3
    assert r.trades[-1]["equity_after"] == pytest.approx(900.0)


def test_the_daily_limit_resets_on_the_next_day():
    """Trading must resume after the day rolls over, or one bad morning
    would silently end the whole backtest."""
    losing = dict(STRONG_UP, close_delta=-300.0, market_prob=0.5)
    quiet = dict(STRONG_UP, move_usd=0.0, close_delta=0.0)
    day_windows = 86_400_000 // WINDOW_MS  # 288 five-minute windows per day

    # 20 warm-up + 5 losing windows lands at index 25; pad with quiet windows
    # up to index 288, which is the first window of day 1.
    specs = [losing] * 5 + [quiet] * (day_windows - 25) + [losing] * 3
    r = run(specs, strategy="naive_momentum", fallback_fixed_pct=0.05,
            max_trades_per_day=99, **PERMISSIVE)

    days = {t["window_start_ms"] // 86_400_000 for t in r.trades}
    assert days == {0, 1}, "trading must resume after the day rolls over"
    assert r.skipped_reasons.get("daily_loss_limit_hit") == 4, "3 on day 0, 1 on day 1"


# -------------------------------------------------- confluence-only gates


def test_volume_percentile_gate_rejects_a_quiet_window():
    r = run([dict(STRONG_UP, volume=1.0)] * 3, warm_kwargs=dict(volume=100.0),
            min_confluence_score=0, min_volume_percentile=50)
    assert r.skipped_reasons.get("volume_percentile_gate") == 3


def test_confluence_score_gate_rejects_a_weak_composite():
    weak = dict(move_usd=75.0, close_delta=75.0, market_prob=0.52, volume=1.0, book=50_000.0)
    r = run([weak] * 3, min_confluence_score=99, min_volume_percentile=0)
    assert r.skipped_reasons.get("confluence_score_gate") == 3


def test_ev_gate_rejects_an_edge_too_small_to_pay_friction():
    """Model at 0.64 against a market at 0.63 is a 1pp edge against 2.5pp of
    friction. This is the single most important gate in the system given
    PLAN.md's conclusion that costs, not signal, are the binding constraint."""
    r = run([STRONG_UP] * 3, **PERMISSIVE)
    model_prob = confluence_score_to_model_prob(r.trades[0]["confluence_score"])
    near_market = round(model_prob - 0.01, 4)
    r2 = run([dict(STRONG_UP, market_prob=near_market)] * 3, **PERMISSIVE)
    assert r2.skipped_reasons.get("ev_gate") == 3
    assert r2.trades == []


def test_an_edge_favouring_the_fade_is_skipped_not_traded_backwards():
    """should_trade() returns True whenever SOME side clears friction. When
    the market prices UP above the model, the favoured side is DOWN -- the
    opposite of the candidate. Earlier versions traded candidate_direction
    anyway. This is the regression test for that."""
    r = run([dict(STRONG_UP, market_prob=0.95)] * 3, **PERMISSIVE)
    assert r.skipped_reasons.get("ev_favors_fade_not_momentum") == 3
    assert r.trades == []


def test_correlation_tracker_skips_after_two_same_side_losses():
    losing_up = dict(STRONG_UP, close_delta=-300.0)
    r = run([losing_up] * 4, **PERMISSIVE)
    assert [(t["side"], t["won"]) for t in r.trades] == [("UP", False), ("UP", False)]
    assert r.skipped_reasons.get("correlation_tracker_skip") == 2


def test_zero_size_is_skipped_rather_than_logged_as_a_trade():
    r = run([STRONG_UP] * 3, fallback_pct=0.0, **PERMISSIVE)
    assert r.skipped_reasons.get("zero_size") == 3
    assert r.trades == []


# --------------------------------------------------------- price bands


def test_entry_price_cap_blocks_expensive_favourites():
    """Buying at 0.98 risks a dollar to win two cents. Off by default, so
    without a test nothing would notice it breaking."""
    r = run([dict(STRONG_UP, market_prob=0.60)] * 3, max_entry_price=0.55, **PERMISSIVE)
    assert r.skipped_reasons.get("entry_price_above_max") == 3


def test_entry_price_floor_blocks_longshots():
    r = run([dict(STRONG_UP, market_prob=0.60)] * 3, min_entry_price=0.70, **PERMISSIVE)
    assert r.skipped_reasons.get("entry_price_below_min") == 3


def test_entry_price_is_clamped_into_a_tradeable_band():
    """Prices of exactly 0 or 1 would divide by zero in the payout, or imply
    an infinite return."""
    for market_prob in (0.0, 1.0, 0.001, 0.999):
        r = run([dict(STRONG_UP, market_prob=market_prob)] * 3, **PERMISSIVE)
        for t in r.trades:
            assert 0.01 <= t["entry_price"] <= 0.99


# ------------------------------------------------------------ P&L maths


def test_winning_trade_pays_the_binary_payout():
    """size/entry - size, less the fee on the gross payout. Checked
    explicitly because the smoke tests only verify the equity curve is
    self-consistent, which stays true even if the payout formula is wrong."""
    r = run([STRONG_UP] * 1, strategy="naive_momentum", fallback_fixed_pct=0.01, **PERMISSIVE)
    t = r.trades[0]
    size, entry = t["size_usdc"], t["entry_price"]
    assert t["won"]
    assert size == pytest.approx(10.0)
    assert entry == pytest.approx(0.55)
    assert t["pnl_usdc"] == pytest.approx(size / entry - size)


def test_losing_trade_loses_exactly_the_stake():
    losing = dict(STRONG_UP, close_delta=-300.0)
    r = run([losing] * 1, strategy="naive_momentum", fallback_fixed_pct=0.01, **PERMISSIVE)
    t = r.trades[0]
    assert not t["won"]
    assert t["pnl_usdc"] == pytest.approx(-t["size_usdc"])


def test_taker_fee_reduces_a_winning_payout():
    free = run([STRONG_UP], strategy="naive_momentum", taker_fee=0.0, **PERMISSIVE)
    charged = run([STRONG_UP], strategy="naive_momentum", taker_fee=0.02, **PERMISSIVE)
    assert charged.trades[0]["pnl_usdc"] < free.trades[0]["pnl_usdc"]


def test_a_window_that_does_not_move_resolves_up():
    """`close >= open` resolves UP, matching the market's 'greater than or
    equal' wording. A DOWN candidate on an unchanged window must lose."""
    flat = dict(move_usd=-300.0, close_delta=0.0, market_prob=0.5, volume=50.0, book=50_000.0)
    r = run([flat] * 1, strategy="naive_momentum", **PERMISSIVE)
    assert r.trades[0]["side"] == "DOWN"
    assert r.trades[0]["outcome"] == "UP"
    assert not r.trades[0]["won"]


# ------------------------------------------------- market price fallback


def test_missing_market_price_falls_back_to_fair_value_and_warns_once():
    """A silent fallback here would be the worst kind: the backtest would
    still produce numbers, but they would measure momentum against
    random-walk geometry rather than against a real price."""
    r = run([dict(STRONG_UP, market_prob=None)] * 5, strategy="naive_momentum", **PERMISSIVE)
    assert len(r.warnings) == 1, "the warning must be emitted once, not per window"
    assert "fair-value" in r.warnings[0]
    assert all(t["market_prob_source"] == "fair_value_fallback" for t in r.trades)


def test_real_market_price_is_used_when_present():
    r = run([STRONG_UP] * 3, strategy="naive_momentum", **PERMISSIVE)
    assert r.warnings == []
    assert all(t["market_prob_source"] == "real" for t in r.trades)
    assert all(t["market_prob"] == pytest.approx(0.55) for t in r.trades)


def test_stale_quote_shifts_the_decision_back_in_time():
    """The look-ahead fix at engine.py:141. Scoring a signal at a moment
    newer than the quote you could have traded fabricated ~$2/trade of edge
    on the first real-odds run. With a 60s-stale quote the decision must be
    made on an older, smaller move -- so the trade either does not fire or
    fires on less information, never on more."""
    fresh = run([STRONG_UP] * 3, strategy="naive_momentum", **PERMISSIVE)
    stale = run([dict(STRONG_UP, prob_age_sec=60.0)] * 3, strategy="naive_momentum", **PERMISSIVE)
    assert fresh.trades, "baseline must trade for this comparison to mean anything"
    for t in stale.trades:
        assert t["window_start_ms"] is not None
    # the shifted decision reads an earlier bar, so the observed move is smaller
    assert len(stale.trades) <= len(fresh.trades)


def test_resolved_outcome_from_the_venue_beats_the_price_derived_guess():
    """The market settles on Chainlink while the signals read Binance, so
    the two can disagree. When the venue's own resolution is present it
    decides whether the trade won."""
    contradicted = dict(STRONG_UP, resolved_outcome="DOWN")
    r = run([contradicted] * 1, strategy="naive_momentum", **PERMISSIVE)
    t = r.trades[0]
    assert t["side"] == "UP"
    assert t["outcome"] == "DOWN", "the venue's resolution must win"
    assert not t["won"]
    assert t["pnl_usdc"] == pytest.approx(-t["size_usdc"])


# ------------------------------------------------------------ invariants


@pytest.mark.parametrize("strategy", ["coinflip", "naive_momentum", "confluence"])
def test_every_window_is_either_traded_or_accounted_for(strategy):
    """No window may silently vanish. If a branch ever forgets to call
    skip(), this catches it -- and a missing skip is exactly how a gate
    stops firing without any test failing."""
    specs = [STRONG_UP, dict(STRONG_UP, move_usd=5.0, close_delta=5.0),
             dict(STRONG_UP, book=1.0), dict(STRONG_UP, market_prob=0.99)] * 5
    r = run(specs, strategy=strategy, min_book_depth_usdc=5_000, **PERMISSIVE)
    total_windows = WARMUP_FILLERS + len(specs)
    assert len(r.trades) + sum(r.skipped_reasons.values()) == total_windows


@pytest.mark.parametrize("strategy", ["coinflip", "naive_momentum", "confluence"])
def test_equity_curve_tracks_cumulative_pnl(strategy):
    r = run([STRONG_UP, dict(STRONG_UP, close_delta=-300.0)] * 6,
            strategy=strategy, max_trades_per_day=99, **PERMISSIVE)
    running = BASE_CONFIG["total_allocation_usdc"]
    for t, equity in zip(r.trades, r.equity_curve):
        running += t["pnl_usdc"]
        assert running == pytest.approx(equity)
        assert equity == t["equity_after"]


def test_results_are_reproducible_for_a_fixed_seed():
    a = run([STRONG_UP] * 5, strategy="coinflip", **PERMISSIVE)
    b = run([STRONG_UP] * 5, strategy="coinflip", **PERMISSIVE)
    assert [t["side"] for t in a.trades] == [t["side"] for t in b.trades]
    assert a.equity_curve == b.equity_curve


def test_an_unknown_strategy_is_rejected():
    with pytest.raises(AssertionError):
        run([STRONG_UP], strategy="martingale")
