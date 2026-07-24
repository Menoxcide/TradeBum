from __future__ import annotations


def should_trade(
    market_prob: float,
    model_prob: float,
    spread: float,
    fees: float,
    slippage_buffer: float = 0.005,
) -> tuple[bool, float]:
    """
    Only trade if edge > friction. Straight from the scaffolding doc.

    market_prob / model_prob should both be the probability of the SAME
    side (e.g. both "prob UP"), not mixed.
    """
    edge = abs(model_prob - market_prob)
    friction = spread + fees + slippage_buffer

    if edge <= friction:
        return False, 0.0

    if model_prob > market_prob:
        # Bet on the outcome the model favors more than the market does
        ev = (model_prob * (1 - market_prob - fees)) - ((1 - model_prob) * market_prob)
    else:
        ev = ((1 - model_prob) * (market_prob - fees)) - (model_prob * (1 - market_prob))

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
    """
    max_swing = 0.35  # composite=100 implies at most 85% model-estimated win prob
    return 0.5 + max_swing * ((composite_score - 50.0) / 50.0)


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
