"""Contract tests for the signal filters.

These modules showed 90-100% line coverage before this file existed, because
the backtest engine calls every one of them on every window. Nothing
asserted anything about what they returned. Line coverage measures
execution, not verification, and for scoring functions the difference is the
whole point: a sign inversion executes exactly as many lines as the correct
version does.

Two invariants carry most of the weight:

  contract    raw is a 0-100 score, confidence a 0-1 weight. ConfluenceScorer
              divides by summed confidence and treats raw as a percentage,
              so a module returning 150 or -0.2 silently corrupts the
              composite for every other module too.

  symmetry    directional modules must score UP and DOWN as mirror images
              (raw_up + raw_down == 100); direction-agnostic ones must score
              them identically. This is the test that catches a flipped
              comparison, which is the failure mode this repo has already
              hit once (see PLAN.md on the sign-inverted solver).
"""

from __future__ import annotations

import pytest

from src.data.schema import Bar, OrderBookSnapshot
from src.signal_engine.confluence_scorer import ConfluenceScorer
from src.signal_engine.funding_rate_overlay import FundingRateOverlay
from src.signal_engine.orderbook_imbalance import OrderBookImbalance
from src.signal_engine.price_momentum import PriceMomentumFilter, propose_direction
from src.signal_engine.skew_edge import SkewEdgeCalculator
from src.signal_engine.volatility_regime import VolatilityRegimeFilter
from src.signal_engine.volume_profile import VolumeProfileFilter

# Modules whose score should mirror when the candidate direction flips.
DIRECTIONAL = [OrderBookImbalance(), SkewEdgeCalculator(), PriceMomentumFilter()]
# Modules that judge the setup, not the side.
AGNOSTIC = [VolumeProfileFilter(), VolatilityRegimeFilter()]
ALL_MODULES = DIRECTIONAL + AGNOSTIC + [FundingRateOverlay()]


def book(bid_notional: float, ask_notional: float, price: float = 100.0):
    return OrderBookSnapshot(
        timestamp_ms=0,
        bid_prices=[price] * 3,
        bid_sizes=[bid_notional / 3 / price] * 3,
        ask_prices=[price] * 3,
        ask_sizes=[ask_notional / 3 / price] * 3,
    )


def state(**overrides):
    s = {
        "timestamp_ms": 300_000,
        "btc_price": 100_100.0,
        "window_open_price": 100_000.0,
        "seconds_to_close": 120,
        "bars_recent": [Bar(i * 1000, 100.0, 101.0, 99.0, 100.0, 1.0) for i in range(10)],
        "orderbook": book(5_000, 5_000),
        "funding_rate": 0.0,
        "atr_5m": 30.0,
        "atr_median": 25.0,
        "volume_current": 100.0,
        "volume_median": 80.0,
        "market_prob_up": 0.6,
    }
    s.update(overrides)
    return s


# A spread of states chosen to hit the edges: empty book, one-sided book,
# zero denominators, extreme probabilities, huge and negative moves.
EDGE_STATES = [
    state(),
    state(orderbook=None),
    state(orderbook=book(0, 0)),
    state(orderbook=book(10_000, 0)),
    state(orderbook=book(0, 10_000)),
    state(orderbook=OrderBookSnapshot(timestamp_ms=0)),
    state(market_prob_up=None),
    state(market_prob_up=0.0),
    state(market_prob_up=1.0),
    state(volume_median=0.0),
    state(volume_current=0.0),
    state(atr_median=0.0),
    state(atr_5m=0.0),
    state(funding_rate=0.05),
    state(funding_rate=-0.05),
    state(btc_price=100_000.0),          # zero move
    state(btc_price=90_000.0),           # large down move
    state(btc_price=200_000.0),          # absurd up move
]


@pytest.mark.parametrize("module", ALL_MODULES, ids=lambda m: m.name)
@pytest.mark.parametrize("direction", ["UP", "DOWN"])
@pytest.mark.parametrize("st", EDGE_STATES, ids=range(len(EDGE_STATES)))
def test_evaluate_honours_the_score_contract(module, direction, st):
    raw, confidence = module.evaluate(st, direction)
    assert 0.0 <= raw <= 100.0, f"{module.name} returned raw={raw}, outside 0-100"
    assert 0.0 <= confidence <= 1.0, f"{module.name} returned confidence={confidence}, outside 0-1"


@pytest.mark.parametrize("module", DIRECTIONAL, ids=lambda m: m.name)
@pytest.mark.parametrize("st", EDGE_STATES, ids=range(len(EDGE_STATES)))
def test_directional_modules_mirror_when_the_side_flips(module, st):
    """raw(UP) + raw(DOWN) == 100. Evidence that supports UP by X must
    oppose DOWN by exactly X -- otherwise the composite would prefer one
    side on identical evidence, which is a systematic bias in the strategy
    and not a detectable one from aggregate backtest P&L."""
    up_raw, up_conf = module.evaluate(st, "UP")
    down_raw, down_conf = module.evaluate(st, "DOWN")
    assert up_raw + down_raw == pytest.approx(100.0), (
        f"{module.name} scored UP={up_raw} and DOWN={down_raw}; these must mirror"
    )
    assert up_conf == pytest.approx(down_conf), (
        f"{module.name} reported different confidence for the two sides"
    )


@pytest.mark.parametrize("module", AGNOSTIC, ids=lambda m: m.name)
@pytest.mark.parametrize("st", EDGE_STATES, ids=range(len(EDGE_STATES)))
def test_agnostic_modules_ignore_the_side(module, st):
    assert module.evaluate(st, "UP") == module.evaluate(st, "DOWN"), (
        f"{module.name} is documented as direction-agnostic but scored the two sides differently"
    )


def test_missing_or_empty_book_reports_zero_confidence_not_a_fake_neutral():
    """A neutral 50 with real confidence would drag the composite toward 50
    and count toward total_weight. Absent data has to report confidence 0 so
    it drops out of the weighted average entirely."""
    m = OrderBookImbalance()
    for st in (state(orderbook=None), state(orderbook=book(0, 0)), state(orderbook=OrderBookSnapshot(timestamp_ms=0))):
        raw, conf = m.evaluate(st, "UP")
        assert (raw, conf) == (50.0, 0.0)


def test_orderbook_imbalance_points_the_right_way():
    """Bid-heavy supports UP. A flipped comparison here would invert the
    signal's contribution while leaving every aggregate statistic looking
    plausible."""
    m = OrderBookImbalance()
    bid_heavy, _ = m.evaluate(state(orderbook=book(9_000, 1_000)), "UP")
    ask_heavy, _ = m.evaluate(state(orderbook=book(1_000, 9_000)), "UP")
    assert bid_heavy > 50.0 > ask_heavy
    balanced, _ = m.evaluate(state(orderbook=book(5_000, 5_000)), "UP")
    assert balanced == pytest.approx(50.0)


def test_orderbook_confidence_scales_with_depth_and_caps_at_one():
    m = OrderBookImbalance(min_depth_usdc_for_full_confidence=2_000.0)
    # confidence is total notional (both sides) / min_depth, capped at 1
    _, thin = m.evaluate(state(orderbook=book(250, 250)), "UP")      # total 500
    _, half = m.evaluate(state(orderbook=book(500, 500)), "UP")      # total 1000
    _, deep = m.evaluate(state(orderbook=book(50_000, 50_000)), "UP")
    assert thin == pytest.approx(0.25)
    assert half == pytest.approx(0.5)
    assert deep == 1.0, "confidence must cap at 1, not scale without bound"


def test_skew_edge_prefers_the_move_the_market_has_not_priced():
    """The module's whole claim: a move already marked to 90/10 has little
    left to capture, the same move at 55/45 has more."""
    m = SkewEdgeCalculator()
    unpriced, _ = m.evaluate(state(market_prob_up=0.55), "UP")
    priced_in, _ = m.evaluate(state(market_prob_up=0.90), "UP")
    assert unpriced > priced_in


def test_skew_edge_distrusts_near_resolved_odds():
    m = SkewEdgeCalculator()
    _, mid_conf = m.evaluate(state(market_prob_up=0.5), "UP")
    _, extreme_conf = m.evaluate(state(market_prob_up=0.99), "UP")
    assert mid_conf == 1.0
    assert extreme_conf < 0.1


def test_volume_profile_upper_clamp_binds_and_lower_bound_is_fifteen():
    """raw is 50 + 35*(ratio-1), which escapes 0-100 without the clamp: a
    10x volume spike would report 365.

    The lower clamp, by contrast, is unreachable. Volume cannot go below
    zero, so ratio bottoms out at 0 and raw bottoms out at 15 -- a dead
    market still contributes a mildly supportive score rather than a
    contradicting one. That is a live asymmetry in the composite, not a
    rounding detail, so it is pinned here rather than left implicit."""
    m = VolumeProfileFilter()
    huge, _ = m.evaluate(state(volume_current=1000.0, volume_median=100.0), "UP")
    dead, _ = m.evaluate(state(volume_current=0.0, volume_median=100.0), "UP")
    typical, _ = m.evaluate(state(volume_current=100.0, volume_median=100.0), "UP")
    assert huge == 100.0, "upper clamp must bind"
    assert dead == pytest.approx(15.0), "zero volume floors at 15, never 0"
    assert typical == pytest.approx(50.0), "volume at the median is neutral"


def test_volatility_regime_peaks_at_its_sweet_spot():
    m = VolatilityRegimeFilter(sweet_spot_ratio=1.3, width=0.6)
    at_sweet, _ = m.evaluate(state(atr_5m=13.0, atr_median=10.0), "UP")
    too_quiet, _ = m.evaluate(state(atr_5m=2.0, atr_median=10.0), "UP")
    too_wild, _ = m.evaluate(state(atr_5m=50.0, atr_median=10.0), "UP")
    assert at_sweet == pytest.approx(100.0)
    assert too_quiet < at_sweet and too_wild < at_sweet


def test_funding_overlay_penalises_but_never_rewards():
    """Deliberately NOT a mirroring module, which is why it is excluded from
    the symmetry test above.

    Positive funding means crowded longs, so it penalises UP -- but it does
    not reward DOWN by the same amount, it just declines to penalise it. raw
    is therefore confined to [35, 50]: this module can only ever drag the
    composite down. That matches its docstring, but it means funding can
    never be the reason a trade fires, only a reason one is damped. Pinned
    so the asymmetry is a decision on record rather than an accident."""
    m = FundingRateOverlay(crowded_threshold=0.0001, max_penalty=15.0)
    crowded_long, _ = m.evaluate(state(funding_rate=0.01), "UP")
    uncrowded_short, _ = m.evaluate(state(funding_rate=0.01), "DOWN")
    assert crowded_long == pytest.approx(35.0), "crowded side takes the full penalty"
    assert uncrowded_short == pytest.approx(50.0), "the other side is neutral, not rewarded"

    crowded_short, _ = m.evaluate(state(funding_rate=-0.01), "DOWN")
    uncrowded_long, _ = m.evaluate(state(funding_rate=-0.01), "UP")
    assert crowded_short == pytest.approx(35.0)
    assert uncrowded_long == pytest.approx(50.0)

    assert m.evaluate(state(funding_rate=0.0), "UP")[0] == 50.0
    # just inside the threshold -- no penalty yet
    assert m.evaluate(state(funding_rate=0.0001), "UP")[0] == 50.0


# ------------------------------------------------------- propose_direction


def test_propose_direction_threshold_is_inclusive_at_the_boundary():
    """`abs(move) < min_move` returns None, so a move of exactly the
    threshold DOES produce a signal. Pinned because the gate value is
    configurable and an off-by-one here changes which trades fire."""
    assert propose_direction(state(btc_price=100_070.0), 70.0) == "UP"
    assert propose_direction(state(btc_price=100_069.99), 70.0) is None
    assert propose_direction(state(btc_price=99_930.0), 70.0) == "DOWN"
    assert propose_direction(state(btc_price=99_930.01), 70.0) is None


def test_zero_move_has_no_direction():
    assert propose_direction(state(btc_price=100_000.0), 70.0) is None


def test_price_momentum_and_propose_direction_agree_on_the_gate():
    """Both read the same threshold with the same comparison. If they ever
    diverge, the scorer would ask a module to rate a move it considers
    below-threshold, and the module's own below-threshold branch (raw 50,
    confidence 0.1) would quietly damp the composite."""
    f = PriceMomentumFilter(min_move_usd=70.0)
    for move in (0.0, 69.99, 70.0, 70.01, 500.0, -70.0, -500.0):
        st = state(btc_price=100_000.0 + move)
        direction = propose_direction(st, 70.0)
        raw, conf = f.evaluate(st, direction or "UP")
        below_gate = (raw, conf) == (50.0, 0.1)
        assert below_gate == (direction is None), (
            f"move={move}: propose_direction said {direction} but the filter's "
            f"below-gate branch was {'taken' if below_gate else 'not taken'}"
        )


def test_price_momentum_strength_saturates_at_the_strong_move():
    f = PriceMomentumFilter(min_move_usd=70.0, strong_move_usd=150.0)
    barely, _ = f.evaluate(state(btc_price=100_070.0), "UP")
    strong, _ = f.evaluate(state(btc_price=100_150.0), "UP")
    beyond, _ = f.evaluate(state(btc_price=101_000.0), "UP")
    assert barely == pytest.approx(50.0)
    assert strong == pytest.approx(100.0)
    assert beyond == pytest.approx(100.0), "score must saturate, not keep climbing"


def test_price_momentum_flips_when_asked_about_the_opposing_side():
    f = PriceMomentumFilter(min_move_usd=70.0, strong_move_usd=150.0)
    with_move, _ = f.evaluate(state(btc_price=100_150.0), "UP")
    against_move, _ = f.evaluate(state(btc_price=100_150.0), "DOWN")
    assert with_move == pytest.approx(100.0)
    assert against_move == pytest.approx(0.0)


# ------------------------------------------------------ ConfluenceScorer


def test_scorer_returns_no_signal_below_the_move_gate(config):
    scorer = ConfluenceScorer(weights=config["signal_weights"], min_move_usd=70.0)
    composite, direction, breakdown = scorer.score(state(btc_price=100_010.0))
    assert (composite, direction, breakdown) == (None, None, {})


@pytest.mark.parametrize("st", EDGE_STATES, ids=range(len(EDGE_STATES)))
def test_composite_stays_in_range_on_every_edge_state(config, st):
    """The composite feeds confluence_score_to_model_prob(), which maps
    0-100 onto a probability by linear interpolation and does NOT clamp. A
    composite outside 0-100 therefore becomes a probability outside 0-1 and
    flows straight into the EV gate."""
    scorer = ConfluenceScorer(weights=config["signal_weights"], min_move_usd=70.0)
    composite, direction, _ = scorer.score(st)
    if composite is None:
        return
    assert 0.0 <= composite <= 100.0, f"composite {composite} escaped 0-100"
    assert direction in ("UP", "DOWN")


def test_scorer_falls_back_to_neutral_when_every_module_abstains(config):
    """total_weight is the sum of weight*confidence. If every module reports
    confidence 0 the denominator is 0, and the composite must fall back to
    50 rather than divide by zero."""
    weights = {k: 0 for k in config["signal_weights"]}
    scorer = ConfluenceScorer(weights=weights, min_move_usd=70.0)
    composite, direction, _ = scorer.score(state(btc_price=100_500.0))
    assert composite == 50.0
    assert direction == "UP"


def test_breakdown_is_json_serialisable(config):
    """The breakdown goes into the trade log. It is documented as plain
    floats specifically so it round-trips through JSON."""
    import json

    scorer = ConfluenceScorer(weights=config["signal_weights"], min_move_usd=70.0)
    _, _, breakdown = scorer.score(state(btc_price=100_500.0))
    assert set(breakdown) == set(config["signal_weights"])
    json.dumps(breakdown)  # raises if anything is not a plain float
