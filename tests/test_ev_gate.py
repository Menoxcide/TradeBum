"""The go/no-go gate. Previously untested end to end.

This is the last thing standing between a signal and real money, and it is
13 statements, so there is no excuse for it being uncovered.
"""

from __future__ import annotations

import pytest

from src.risk.ev_gate import confluence_score_to_model_prob, should_trade


# ------------------------------------------------------- friction screen


def test_no_trade_when_edge_does_not_clear_friction():
    ok, ev = should_trade(market_prob=0.50, model_prob=0.52,
                          spread=0.02, fees=0.0, slippage_buffer=0.005)
    assert (ok, ev) == (False, 0.0), "0.02 of edge cannot pay 0.025 of friction"


def test_edge_exactly_equal_to_friction_is_rejected():
    """The comparison is `edge <= friction`, so breaking even is a no-trade.
    Pinned because flipping it to `<` would open a class of trades whose
    expected value is exactly zero before variance.

    Values chosen to be exactly representable in binary floating point --
    see test_boundary_is_decided_by_floating_point_noise below for why that
    matters more than it should."""
    ok, ev = should_trade(0.50, 0.75, spread=0.25, fees=0.0, slippage_buffer=0.0)
    assert (ok, ev) == (False, 0.0)


def test_boundary_is_decided_by_floating_point_noise():
    """DOCUMENTS A SHARP EDGE. `edge` is a subtraction of two floats and
    `friction` a sum of three, so a case that is exactly break-even on paper
    can land either side of the comparison.

    Here 0.525 - 0.50 evaluates to 0.025000000000000022 while
    0.02 + 0.0 + 0.005 evaluates to exactly 0.025, so a trade with precisely
    zero net edge is accepted. The financial impact is negligible -- these
    are zero-EV trades at the margin -- but it means the gate is not
    reproducible across platforms at the boundary, and any future test
    asserting on it must use representable values."""
    edge = 0.525 - 0.50
    friction = 0.02 + 0.0 + 0.005
    assert edge > friction, "the two are equal in decimal but not in binary"
    ok, _ = should_trade(0.50, 0.525, spread=0.02, fees=0.0, slippage_buffer=0.005)
    assert ok, "a nominally break-even trade slips through on float noise"


def test_friction_is_the_sum_of_all_three_components():
    """Raising any one component alone must be able to close the gate."""
    args = dict(market_prob=0.50, model_prob=0.75)  # edge of exactly 0.25
    assert should_trade(**args, spread=0.0, fees=0.0, slippage_buffer=0.0)[0]
    assert not should_trade(**args, spread=0.5, fees=0.0, slippage_buffer=0.0)[0]
    assert not should_trade(**args, spread=0.0, fees=0.5, slippage_buffer=0.0)[0]
    assert not should_trade(**args, spread=0.0, fees=0.0, slippage_buffer=0.5)[0]


# ------------------------------------------------------------ EV maths


def test_ev_is_positive_when_the_model_favours_the_yes_side():
    ok, ev = should_trade(market_prob=0.50, model_prob=0.70,
                          spread=0.0, fees=0.0, slippage_buffer=0.0)
    # buy at 0.50: win 0.50 with p=0.70, lose 0.50 with p=0.30
    assert ok
    assert ev == pytest.approx(0.70 * 0.50 - 0.30 * 0.50)


def test_ev_is_positive_when_the_model_favours_the_no_side():
    """The under-exercised branch. model < market means buy NO at
    (1 - market_prob) = 0.30; it wins with probability 1 - model = 0.70."""
    ok, ev = should_trade(market_prob=0.70, model_prob=0.30,
                          spread=0.0, fees=0.0, slippage_buffer=0.0)
    assert ok
    assert ev == pytest.approx(0.70 * 0.70 - 0.30 * 0.30)


def test_the_two_branches_are_symmetric():
    """Mirroring both probabilities describes the same bet from the other
    side of the book, so the EV must be identical."""
    _, yes_ev = should_trade(0.40, 0.60, 0.0, 0.0, 0.0)
    _, no_ev = should_trade(0.60, 0.40, 0.0, 0.0, 0.0)
    assert yes_ev == pytest.approx(no_ev)


def test_fees_reduce_ev():
    _, free = should_trade(0.50, 0.70, spread=0.0, fees=0.0, slippage_buffer=0.0)
    _, charged = should_trade(0.50, 0.70, spread=0.0, fees=0.05, slippage_buffer=0.0)
    assert charged < free


def test_reported_ev_excludes_spread_and_slippage():
    """DOCUMENTS A DISCREPANCY -- read before trusting the `ev` field.

    should_trade() screens on edge > spread + fees + slippage, but the EV it
    returns subtracts `fees` only. engine.py stores that number on the trade
    record as "ev", so the logged expected value is overstated by the spread
    and slippage the screen just charged against.

    Below: 10pp of edge against 2.5pp of friction reports ev=0.10, the same
    value it would report with zero spread and zero slippage. This test
    pins current behaviour rather than changing it, because subtracting
    friction here would change which trades fire and by how much -- a
    strategy change that needs its own validation run, not a drive-by edit.
    """
    _, with_friction = should_trade(0.50, 0.60, spread=0.02, fees=0.0, slippage_buffer=0.005)
    _, without_friction = should_trade(0.50, 0.60, spread=0.0, fees=0.0, slippage_buffer=0.0)
    assert with_friction == pytest.approx(without_friction)
    assert with_friction == pytest.approx(0.10)


def test_gate_can_pass_on_the_side_opposite_the_candidate():
    """edge_ok=True only means SOME side clears friction. When the model is
    below the market the favoured side is NO -- engine.py relies on this and
    skips those windows as `ev_favors_fade_not_momentum`."""
    ok, ev = should_trade(market_prob=0.80, model_prob=0.50,
                          spread=0.0, fees=0.0, slippage_buffer=0.0)
    assert ok and ev > 0


# ------------------------------------------- score -> probability mapping


def test_score_to_prob_anchors_at_the_documented_points():
    assert confluence_score_to_model_prob(50.0) == pytest.approx(0.50)
    assert confluence_score_to_model_prob(100.0) == pytest.approx(0.85)
    assert confluence_score_to_model_prob(0.0) == pytest.approx(0.15)


def test_score_to_prob_is_monotonic():
    scores = [0, 10, 25, 50, 65, 80, 100]
    probs = [confluence_score_to_model_prob(s) for s in scores]
    assert probs == sorted(probs)


def test_score_to_prob_does_not_clamp_out_of_range_input():
    """DOCUMENTS AN UNGUARDED EDGE. The mapping is linear and unclamped, so
    a composite outside 0-100 becomes a probability outside 0-1, which
    should_trade() then accepts without complaint.

    ConfluenceScorer currently cannot emit such a score -- every module is
    contract-tested to return 0-100 in test_signal_modules.py, and the
    composite is a weighted mean of those. So this is guarded by that
    invariant rather than by anything here. If a future module breaks the
    contract, THIS is where the nonsense becomes a position size."""
    assert confluence_score_to_model_prob(1000.0) > 1.0
    assert confluence_score_to_model_prob(-1000.0) < 0.0
    ok, ev = should_trade(market_prob=0.5, model_prob=5.0,
                          spread=0.0, fees=0.0, slippage_buffer=0.0)
    assert ok and ev > 1.0, "an impossible probability produces an impossible EV"
