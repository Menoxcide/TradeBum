from __future__ import annotations


def should_trade(
    market_prob: float,
    model_prob: float,
    spread: float,
    fees: float,
    slippage_buffer: float = 0.005,
) -> tuple[bool, float]:
    """
    Only trade if edge survives friction. Returns (should_trade, ev), where
    ev is the expected value per unit staked NET OF ALL FRICTION -- the
    spread and slippage you pay to get in, as well as fees.

    market_prob / model_prob should both be the probability of the SAME
    side (e.g. both "prob UP"), not mixed.

    The returned ev used to subtract `fees` only, while the screen below
    charged spread and slippage as well. engine.py logs this number on every
    trade, so the logged expected value was overstated by exactly the costs
    the screen had just insisted the edge could pay -- on a 10pp edge against
    a 2pp spread and a 0.5pp slippage buffer, it read 0.10 instead of 0.075.
    Given that measured cost, not signal, is the binding constraint on this
    venue (see PLAN.md), an EV that ignores cost is the wrong number to
    reason with and the wrong number to log.
    """
    edge = abs(model_prob - market_prob)
    # Summed in this exact order, unchanged: the comparison below is decided
    # by floating-point noise at the boundary (a nominally break-even trade
    # can land either side of it), so re-associating the addition would
    # silently move which trades qualify. Kept identical, and entry_friction
    # is derived separately rather than by factoring this expression.
    friction = spread + fees + slippage_buffer
    entry_friction = spread + slippage_buffer

    # Fast screen. Strictly more conservative than the EV test below (which
    # charges fees only on the side that wins, so it prices them at
    # fees * p_win rather than fees), and much cheaper than computing EV.
    if edge <= friction:
        return False, 0.0

    if model_prob > market_prob:
        # Bet on the outcome the model favors more than the market does
        ev = (model_prob * (1 - market_prob - fees)) - ((1 - model_prob) * market_prob)
    else:
        ev = ((1 - model_prob) * (market_prob - fees)) - (model_prob * (1 - market_prob))

    # Spread and slippage are paid on entry whichever way the window
    # resolves, so they come off the expectation unconditionally. (Absent
    # fees the expression above reduces to exactly `edge`, which makes the
    # net figure below read as "edge minus what it costs to capture it".)
    ev -= entry_friction

    return ev > 0, ev


def confluence_score_to_model_prob(composite_score: float) -> float:
    """
    Maps the 0-100 confluence composite to a probability that the candidate
    direction resolves correctly. This mapping is a placeholder, not a
    fitted model -- composite_score=50 (neutral) -> 0.50, composite_score=100
    -> 0.50 + max_swing. Replace with a real fit once you have backtest
    trades to fit it against (e.g. logistic regression of outcome on
    composite_score) -- see README.md, "fitting model_prob instead of
    guessing it" is one of the most valuable upgrades to this harness.

    Clamped to [0, 1]. The mapping is linear and unbounded, so a composite
    outside 0-100 would become an impossible probability and flow straight
    into should_trade(), which would price it without complaint -- a
    composite of 1000 yields a "probability" of 7.15 and an EV above 1.0 per
    unit staked. ConfluenceScorer cannot currently emit such a score (every
    filter is contract-tested to return 0-100 and the composite is their
    weighted mean), so this is a second line of defence rather than a live
    fix: it is behaviour-neutral for every reachable input, and it stops a
    future module breaking that contract from turning into a position size.
    """
    max_swing = 0.35  # composite=100 implies at most 85% model-estimated win prob
    prob = 0.5 + max_swing * ((composite_score - 50.0) / 50.0)
    return min(1.0, max(0.0, prob))


# See src/backtest/fair_value.py. Because this placeholder is anchored at a
# flat 50%, it will rarely exceed the fair-value-implied probability for a
# qualifying momentum move (which can legitimately sit at 80-95%+ just from
# window geometry -- see fair_value.py's docstring). That means this
# placeholder alone will trigger few or no trades once compared against a
# properly fair-priced market, which is the correct, honest outcome for an
# un-fit model -- not a bug. A real model_prob should be built by starting
# from fair_value_prob_up(...) and adding a learned adjustment from the
# confluence score, so it can actually say "I think this is MORE likely to
# hold than pure geometry implies" -- that's the only kind of edge worth
# paying for here.
