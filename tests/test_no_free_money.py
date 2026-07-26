"""The harness self-check, made non-vacuous.

Synthetic data is a driftless random walk priced at fair value, so it
contains no edge by construction. A backtest that reports confident profit
on it has a bug -- almost always look-ahead. This is the single most
valuable assertion in the repo: it is the one that would have caught the
60-second bar leak, the phantom 21%-per-trade edge, and the fair-value
mispricing, each of which PLAN.md records as real.

test_smoke.py already states this property, but it checks the `confluence`
strategy, which fires zero trades against a fairly-priced market -- so its
assertion is skipped and the test passes without testing anything. That is
the failure mode this file exists to close: the check runs on strategies
that actually trade, and refuses to pass if the sample is empty.
"""

from __future__ import annotations

import pytest

from conftest import BASE_CONFIG, synthetic_provider
from src.backtest.engine import run_backtest
from src.backtest.stats import summarize

# Enough windows for the interval to mean something without making the
# suite unpleasant to run.
N_WINDOWS = 8000
SEED = 99


@pytest.fixture(scope="module")
def results():
    provider = synthetic_provider(N_WINDOWS, SEED)
    return {
        strategy: run_backtest(provider, BASE_CONFIG, strategy=strategy)
        for strategy in ("coinflip", "naive_momentum")
    }


@pytest.mark.parametrize("strategy", ["coinflip", "naive_momentum"])
def test_the_check_is_not_vacuous(results, strategy):
    """Guard clause for every assertion below. A strategy that stops firing
    would make the no-edge checks pass trivially -- which is exactly what
    happens to the `confluence` variant in test_smoke.py today."""
    result = results[strategy]
    assert len(result.trades) >= 100, (
        f"{strategy} fired only {len(result.trades)} trades on {N_WINDOWS} windows; "
        f"the no-free-money check below cannot mean anything on a sample this small"
    )


@pytest.mark.parametrize("strategy", ["coinflip", "naive_momentum"])
def test_no_confident_edge_on_data_with_none_in_it(results, strategy):
    """The lower bound of the mean-P&L interval must not sit above zero.

    Mean P&L, not win rate: the synthetic market prices at fair value, so a
    strategy entering only likely winners shows a high win rate with zero
    edge because it pays more for those entries. Win rate above 50% here is
    correct and expected; a confidently positive mean is not."""
    result = results[strategy]
    summary = summarize(result.trades, result.equity_curve, strategy)
    lo, hi = summary.mean_pnl_ci
    assert lo <= 0.0, (
        f"{strategy} shows a mean-P&L-per-trade 95% CI of [${lo:,.4f}, ${hi:,.4f}] on "
        f"fair-priced random-walk data. Free money in noise is a bug, not a discovery "
        f"-- look-ahead first, then the fair-value pricing in synthetic.py."
    )


@pytest.mark.parametrize("strategy", ["coinflip", "naive_momentum"])
def test_summary_flags_the_result_as_indistinguishable_from_zero(results, strategy):
    """The note that stops a reader treating this as a negative finding: on
    data with no edge, the honest report is 'cannot tell', not 'no edge'."""
    result = results[strategy]
    summary = summarize(result.trades, result.equity_curve, strategy)
    assert any("distinguishable from zero" in n for n in summary.notes)


def test_every_trade_paid_a_fair_value_price(results):
    """Guards the generator rather than the engine. If synthetic.py ever
    goes back to pricing at a flat 0.5, late entries after a move would be
    systematically underpriced and the harness would manufacture an edge
    from window geometry alone -- the bug the smoke test was written for."""
    trades = results["naive_momentum"].trades
    assert trades
    assert all(t["market_prob_source"] == "real" for t in trades)
    assert all(0.01 <= t["entry_price"] <= 0.99 for t in trades)
    # a fair-priced market must quote a spread of prices, not a constant
    assert len({round(t["entry_price"], 2) for t in trades}) > 5
