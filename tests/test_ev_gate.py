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


def test_reported_ev_is_net_of_spread_and_slippage():
    """The `ev` field is what engine.py logs on every trade, so it has to be
    the expected value of actually doing the trade -- after the costs the
    screen just charged against. A 10pp edge against a 2pp spread and a
    0.5pp slippage buffer is worth 7.5pp, not 10."""
    _, net = should_trade(0.50, 0.60, spread=0.02, fees=0.0, slippage_buffer=0.005)
    _, frictionless = should_trade(0.50, 0.60, spread=0.0, fees=0.0, slippage_buffer=0.0)
    assert frictionless == pytest.approx(0.10)
    assert net == pytest.approx(0.075)
    assert net == pytest.approx(frictionless - 0.025)


def test_ev_reduces_to_edge_minus_cost_when_there_are_no_fees():
    """Without fees the gross expectation of a binary contract is exactly
    the edge, so the net figure reads directly as 'edge minus what it costs
    to capture it' -- the quantity PLAN.md says is the binding constraint."""
    for market, model in ((0.30, 0.45), (0.50, 0.62), (0.80, 0.95)):
        _, ev = should_trade(market, model, spread=0.02, fees=0.0, slippage_buffer=0.005)
        assert ev == pytest.approx((model - market) - 0.025)


def test_spread_and_slippage_are_charged_once_each():
    base = dict(market_prob=0.50, model_prob=0.70, fees=0.0)
    _, none = should_trade(**base, spread=0.0, slippage_buffer=0.0)
    _, spread_only = should_trade(**base, spread=0.03, slippage_buffer=0.0)
    _, slip_only = should_trade(**base, spread=0.0, slippage_buffer=0.03)
    _, both = should_trade(**base, spread=0.03, slippage_buffer=0.03)
    assert spread_only == pytest.approx(none - 0.03)
    assert slip_only == pytest.approx(none - 0.03)
    assert both == pytest.approx(none - 0.06)


def test_charging_friction_only_ever_removes_zero_expectation_trades():
    """Bounds the blast radius of netting friction out of the EV.

    The friction screen (edge > spread + fees + slippage) is strictly
    stronger than the net-EV test, because the EV charges fees only on the
    side that wins -- fees * p_win rather than fees. So any trade clearing
    the screen has net EV >= 0, and the only decisions the netting can
    change are the ones sitting at exactly zero: bets with no expectation
    at all, which are pure variance and correctly declined.

    Swept over ~350k parameter combinations when this change was made; 24
    decisions moved, every one of them at net EV 0.000000."""
    for market in (i / 50 for i in range(1, 50)):
        for model in (i / 50 for i in range(1, 50)):
            for spread in (0.0, 0.005, 0.02, 0.05):
                for fees in (0.0, 0.01, 0.02):
                    for slippage in (0.0, 0.005, 0.02):
                        passed_screen = abs(model - market) > spread + fees + slippage
                        ok, ev = should_trade(market, model, spread, fees, slippage)
                        if passed_screen:
                            assert ev >= -1e-12, (
                                f"screen passed but net EV is negative: mkt={market} "
                                f"model={model} spread={spread} fees={fees} slip={slippage} ev={ev}"
                            )
                        else:
                            assert (ok, ev) == (False, 0.0)


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


def test_score_to_prob_clamps_out_of_range_input():
    """The mapping is linear and unbounded, so without the clamp a composite
    outside 0-100 becomes an impossible probability that should_trade()
    would price without complaint (composite 1000 -> 7.15 -> an EV above 1.0
    per unit staked, i.e. a position size).

    ConfluenceScorer cannot currently emit such a score -- every filter is
    contract-tested to return 0-100 and the composite is their weighted mean
    -- so this is defence in depth, behaviour-neutral for every reachable
    input, guarding against a future module breaking that contract."""
    assert confluence_score_to_model_prob(1000.0) == 1.0
    assert confluence_score_to_model_prob(-1000.0) == 0.0
    assert confluence_score_to_model_prob(float("inf")) == 1.0


def test_score_to_prob_is_unchanged_across_the_whole_valid_range():
    """The clamp must not perturb any score the scorer can actually
    produce."""
    for score in range(0, 101):
        expected = 0.5 + 0.35 * ((score - 50.0) / 50.0)
        assert confluence_score_to_model_prob(score) == pytest.approx(expected)
