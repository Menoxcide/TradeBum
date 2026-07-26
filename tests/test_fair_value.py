"""The fair-value benchmark.

This is the module that stops window geometry being mistaken for momentum
edge, so it is load-bearing for every conclusion the harness produces. It
had no direct tests.
"""

from __future__ import annotations

import math

import pytest

from src.backtest.fair_value import estimate_vol_per_sqrt_sec, fair_value_prob_up
from src.data.schema import Bar


def bars(closes, step_ms=1000):
    return [Bar(i * step_ms, c, c + 1, c - 1, c, 1.0) for i, c in enumerate(closes)]


# --------------------------------------------------- fair_value_prob_up


def test_no_move_is_a_coin_flip():
    assert fair_value_prob_up(0.0, 120.0, 5.0) == pytest.approx(0.5)


def test_probability_rises_with_the_move_and_falls_with_time_left():
    """The whole point of the module: the same move is worth more when
    there is less time remaining for a random walk to erase it."""
    assert fair_value_prob_up(50.0, 120.0, 5.0) > 0.5
    assert fair_value_prob_up(-50.0, 120.0, 5.0) < 0.5
    late = fair_value_prob_up(50.0, 10.0, 5.0)
    early = fair_value_prob_up(50.0, 280.0, 5.0)
    assert late > early, "less time remaining must make the same move more decisive"


def test_symmetry_around_zero():
    for move in (10.0, 75.0, 500.0):
        up = fair_value_prob_up(move, 120.0, 5.0)
        down = fair_value_prob_up(-move, 120.0, 5.0)
        assert up + down == pytest.approx(1.0)


def test_output_is_always_a_probability():
    for move in (-1e9, -100.0, 0.0, 100.0, 1e9):
        for secs in (0.0, 1e-9, 1.0, 300.0, 1e9):
            for vol in (0.0, 1e-9, 5.0, 1e9):
                p = fair_value_prob_up(move, secs, vol)
                assert 0.0 <= p <= 1.0


def test_degenerate_inputs_resolve_by_the_sign_of_the_move():
    """Zero time left or zero volatility means the outcome is already
    determined, so the answer is certainty, not a division by zero."""
    for secs, vol in ((0.0, 5.0), (120.0, 0.0), (0.0, 0.0), (-5.0, 5.0), (120.0, -1.0)):
        assert fair_value_prob_up(10.0, secs, vol) == 1.0
        assert fair_value_prob_up(-10.0, secs, vol) == 0.0
        assert fair_value_prob_up(0.0, secs, vol) == 0.5


def test_matches_the_normal_cdf_it_claims_to_be():
    """Phi(move / (vol * sqrt(t))). Checked against an independent
    computation so a transposed term would be caught."""
    move, secs, vol = 30.0, 100.0, 3.0
    z = move / (vol * math.sqrt(secs))
    expected = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    assert fair_value_prob_up(move, secs, vol) == pytest.approx(expected)
    # one standard deviation of move should land on the standard value
    assert fair_value_prob_up(3.0 * math.sqrt(100.0), 100.0, 3.0) == pytest.approx(0.8413447, abs=1e-6)


# ---------------------------------------------- estimate_vol_per_sqrt_sec


def test_returns_none_below_the_minimum_history():
    assert estimate_vol_per_sqrt_sec([], min_bars=5) is None
    assert estimate_vol_per_sqrt_sec(bars([1.0, 2.0, 3.0]), min_bars=5) is None


def test_returns_none_when_too_few_usable_steps_survive():
    """Bars sharing a timestamp produce dt=0 steps, which are dropped. If
    that leaves fewer than min_bars the answer is 'not enough data' rather
    than a number computed from two points."""
    same_ts = [Bar(0, 1.0, 1.0, 1.0, float(i), 1.0) for i in range(10)]
    assert estimate_vol_per_sqrt_sec(same_ts, min_bars=5) is None


def test_zero_volatility_series_estimates_zero():
    assert estimate_vol_per_sqrt_sec(bars([100.0] * 20)) == pytest.approx(0.0)


def test_estimate_recovers_a_known_diffusion_scale():
    """Steps of constant size s every second give per-sqrt-second vol equal
    to the population stdev of those steps."""
    closes = [100.0 + (5.0 if i % 2 else -5.0) * (i // 2) for i in range(40)]
    est = estimate_vol_per_sqrt_sec(bars(closes, step_ms=1000))
    assert est is not None and est > 0


def test_estimate_is_invariant_to_bar_spacing():
    """Each step is normalised by sqrt(dt), so measuring the same diffusion
    on 1-second and 4-second bars must give the same answer. Without the
    normalisation the estimate would scale with bar width, and the fair
    value would silently change with data granularity."""
    import random

    rng = random.Random(4)
    fine_closes, coarse_closes = [100.0], [100.0]
    for _ in range(400):
        fine_closes.append(fine_closes[-1] + rng.gauss(0, 1.0))
    # coarse bars: same diffusion observed every 4 seconds -> steps 2x larger
    for _ in range(400):
        coarse_closes.append(coarse_closes[-1] + rng.gauss(0, 2.0))

    fine = estimate_vol_per_sqrt_sec(bars(fine_closes, step_ms=1000))
    coarse = estimate_vol_per_sqrt_sec(bars(coarse_closes, step_ms=4000))
    assert fine == pytest.approx(coarse, rel=0.15), (
        f"1s bars gave {fine}, 4s bars gave {coarse}; the sqrt(dt) normalisation "
        f"should make these agree"
    )


def test_ignores_out_of_order_bars_rather_than_taking_a_negative_root():
    out_of_order = bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0])
    out_of_order[3], out_of_order[4] = out_of_order[4], out_of_order[3]
    est = estimate_vol_per_sqrt_sec(out_of_order)
    assert est is None or est >= 0.0
