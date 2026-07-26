"""Position sizing.

Before this file the sizer had exactly one test, covering the guard clause.
The Kelly branch itself -- the arithmetic that decides how much money goes
on each trade -- was never executed by any test.
"""

from __future__ import annotations

import pytest

from src.sizing_engine.kelly_calculator import KellyVolatilitySizer

PROFILE = {
    "total_allocation_usdc": 1000.0,
    "max_position_pct": 0.05,
    "kelly_fraction": 0.25,
}


def sizer(**overrides):
    profile = {**PROFILE, **overrides.pop("profile", {})}
    return KellyVolatilitySizer(profile, **overrides)


# ------------------------------------------------------ the fallback path


@pytest.mark.parametrize(
    "n_trades, avg_win, avg_loss, why",
    [
        (0, 10.0, 5.0, "no history at all"),
        (49, 10.0, 5.0, "just below min_trades_for_kelly"),
        (100, 0.0, 5.0, "no wins yet, so b is undefined"),
        (100, 10.0, 0.0, "no losses yet -- the original ZeroDivisionError"),
        (100, -1.0, 5.0, "negative avg_win is nonsense input"),
        (100, 10.0, -5.0, "avg_loss must be a positive magnitude"),
    ],
)
def test_falls_back_rather_than_trusting_a_noisy_estimate(n_trades, avg_win, avg_loss, why):
    s = sizer(min_trades_for_kelly=50)
    out = s.calculate(0.6, avg_win, avg_loss, current_atr=1.0, median_atr=1.0,
                      n_trades_so_far=n_trades)
    assert out["kelly_raw"] is None, f"should not have computed Kelly: {why}"
    assert out["final_pct"] == pytest.approx(0.01)
    assert out["position_size_usdc"] == pytest.approx(10.0)
    assert "fallback" in out["rationale"]


def test_config_fallback_pct_is_actually_used():
    """Regression test. engine.py builds the sizer as
    `KellyVolatilitySizer(config, min_trades_for_kelly=...)` and never passes
    fallback_pct, so when this value was argument-only the config key was
    dead -- setting it to 0.25 still sized at 0.01. Every shipped profile
    sets 0.01, which is also the default, so nothing looked wrong."""
    s = KellyVolatilitySizer({**PROFILE, "fallback_pct": 0.04, "max_position_pct": 0.10},
                             min_trades_for_kelly=50)
    out = s.calculate(0.6, 10.0, 5.0, 1.0, 1.0, n_trades_so_far=0)
    assert out["final_pct"] == pytest.approx(0.04)
    assert out["position_size_usdc"] == pytest.approx(40.0)


def test_explicit_argument_still_overrides_the_profile():
    s = KellyVolatilitySizer({**PROFILE, "fallback_pct": 0.04}, fallback_pct=0.02)
    assert s.fallback_pct == pytest.approx(0.02)


def test_fallback_is_still_capped_by_max_position_pct():
    s = KellyVolatilitySizer({**PROFILE, "fallback_pct": 0.9, "max_position_pct": 0.05})
    out = s.calculate(0.6, 10.0, 5.0, 1.0, 1.0, n_trades_so_far=0)
    assert out["final_pct"] == pytest.approx(0.05), "cap must bind even on the fallback path"


# --------------------------------------------------------- the Kelly path


def test_kelly_formula_matches_the_textbook_value():
    """b = avg_win/avg_loss = 2, p = 0.6, q = 0.4
    kelly = (b*p - q)/b = (1.2 - 0.4)/2 = 0.40
    fractional = 0.40 * 0.25 = 0.10, then capped at max_position_pct."""
    s = sizer(min_trades_for_kelly=0)
    out = s.calculate(0.6, 10.0, 5.0, current_atr=1.0, median_atr=1.0, n_trades_so_far=100)
    assert out["kelly_raw"] == pytest.approx(0.40)
    assert out["kelly_fractional"] == pytest.approx(0.10)
    assert out["vol_modifier"] == pytest.approx(1.0)
    assert out["final_pct"] == pytest.approx(0.05), "0.10 must be capped to max_position_pct"


def test_negative_edge_sizes_to_zero_not_to_a_short():
    """(b*p - q) goes negative when the strategy is losing. Kelly says bet
    the other side; this system cannot, so it must floor at zero rather than
    return a negative size that a caller might multiply into something."""
    s = sizer(min_trades_for_kelly=0)
    out = s.calculate(0.2, 10.0, 10.0, 1.0, 1.0, n_trades_so_far=100)
    assert out["kelly_raw"] == 0.0
    assert out["final_pct"] == 0.0
    assert out["position_size_usdc"] == 0.0


def test_vol_modifier_is_clamped_both_ways():
    """current_atr/median_atr is unbounded, but the modifier must stay in
    [0.5, 1.5] so one freak-volatility window cannot scale a position up
    arbitrarily."""
    s = sizer(min_trades_for_kelly=0)
    calm = s.calculate(0.6, 10.0, 5.0, current_atr=0.01, median_atr=100.0, n_trades_so_far=100)
    wild = s.calculate(0.6, 10.0, 5.0, current_atr=1000.0, median_atr=1.0, n_trades_so_far=100)
    assert calm["vol_modifier"] == pytest.approx(0.5)
    assert wild["vol_modifier"] == pytest.approx(1.5)


def test_zero_median_atr_does_not_divide_by_zero():
    s = sizer(min_trades_for_kelly=0)
    out = s.calculate(0.6, 10.0, 5.0, current_atr=5.0, median_atr=0.0, n_trades_so_far=100)
    assert out["vol_modifier"] == pytest.approx(1.0), "unknown vol regime is neutral, not fatal"


def test_size_never_exceeds_the_cap_across_a_wide_input_sweep():
    """The property that actually protects the account: whatever the inputs,
    a single position cannot exceed max_position_pct of the allocation."""
    s = sizer(min_trades_for_kelly=0)
    for win_rate in (0.0, 0.25, 0.5, 0.75, 0.99, 1.0):
        for avg_win in (0.01, 1.0, 1_000.0):
            for avg_loss in (0.01, 1.0, 1_000.0):
                for atr, med in ((1.0, 1.0), (100.0, 1.0), (1.0, 100.0)):
                    out = s.calculate(win_rate, avg_win, avg_loss, atr, med, n_trades_so_far=100)
                    assert 0.0 <= out["final_pct"] <= PROFILE["max_position_pct"] + 1e-12
                    assert 0.0 <= out["position_size_usdc"] <= 50.0 + 1e-9


def test_win_rate_of_one_does_not_produce_an_unbounded_bet():
    """p=1 makes textbook Kelly say 'bet everything'. The cap is the only
    thing standing between that and the whole account."""
    s = sizer(min_trades_for_kelly=0)
    out = s.calculate(1.0, 10.0, 5.0, 1.0, 1.0, n_trades_so_far=100)
    assert out["kelly_raw"] == pytest.approx(1.0)
    assert out["final_pct"] == pytest.approx(0.05)
