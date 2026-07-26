"""Statistics used to judge whether a backtest found anything.

These functions decide whether a result gets called edge, so a bug here
does not crash anything -- it produces a confident wrong answer, which is
the most expensive kind. Degenerate inputs (n=0, n=1, all-identical values,
negative equity) are the realistic ones early in a backtest and were all
uncovered.
"""

from __future__ import annotations

import statistics

import pytest

from src.backtest.stats import (
    bootstrap_ci,
    max_drawdown,
    per_trade_sharpe,
    summarize,
    wilson_ci,
)


def trade(pnl: float, won: bool | None = None, equity: float = 0.0):
    return {"pnl_usdc": pnl, "won": pnl > 0 if won is None else won, "equity_after": equity}


# ------------------------------------------------------------ wilson_ci


def test_wilson_ci_of_no_data_is_maximally_uncertain():
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_wilson_ci_stays_inside_zero_one_at_the_extremes():
    """The reason for using Wilson over a normal approximation: at 0/n or
    n/n the normal interval runs outside [0, 1]."""
    for n in (1, 5, 50, 1000):
        for wins in (0, n):
            lo, hi = wilson_ci(wins, n)
            assert 0.0 <= lo <= hi <= 1.0


def test_wilson_ci_narrows_as_evidence_accumulates():
    widths = [wilson_ci(n // 2, n)[1] - wilson_ci(n // 2, n)[0] for n in (10, 100, 1000, 10_000)]
    assert widths == sorted(widths, reverse=True)


def test_wilson_ci_brackets_the_observed_rate():
    for wins, n in ((3, 10), (50, 100), (990, 1000)):
        lo, hi = wilson_ci(wins, n)
        assert lo <= wins / n <= hi


# --------------------------------------------------------- bootstrap_ci


def test_bootstrap_ci_of_nothing_is_zero_not_a_crash():
    assert bootstrap_ci([], statistics.mean) == (0.0, 0.0)


def test_bootstrap_ci_of_identical_values_has_zero_width():
    assert bootstrap_ci([7.0] * 20, statistics.mean) == (7.0, 7.0)


def test_bootstrap_ci_is_deterministic_for_a_given_seed():
    """It seeds its own RNG, so two runs on the same data must agree --
    otherwise a borderline result would flip verdicts between runs."""
    values = [1.0, -2.0, 3.5, -0.5, 8.0, -3.0, 0.25]
    assert bootstrap_ci(values, statistics.mean) == bootstrap_ci(values, statistics.mean)


def test_bootstrap_ci_brackets_the_sample_mean():
    values = [1.0, -2.0, 3.5, -0.5, 8.0, -3.0, 0.25, 4.0, -1.5, 2.0]
    lo, hi = bootstrap_ci(values, statistics.mean)
    assert lo <= statistics.mean(values) <= hi


def test_bootstrap_ci_ordering_holds():
    lo, hi = bootstrap_ci([1.0, 5.0, -3.0, 2.0, 9.0], statistics.mean)
    assert lo <= hi


# -------------------------------------------------------- max_drawdown


def test_max_drawdown_of_empty_curve_is_zero():
    assert max_drawdown([]) == 0.0


def test_max_drawdown_of_a_rising_curve_is_zero():
    assert max_drawdown([1000, 1100, 1200, 1300]) == 0.0


def test_max_drawdown_measures_from_the_running_peak():
    # peak 1200, trough 900 -> 25%
    assert max_drawdown([1000, 1200, 900, 1100]) == pytest.approx(0.25)


def test_max_drawdown_can_exceed_one_hundred_percent_when_equity_goes_negative():
    """DOCUMENTS A MISLEADING OUTPUT. print_summary formats this as a
    percentage, so a blown-up account reports '150.0%' drawdown. It is
    arithmetically what the formula says -- (peak - x)/peak with x negative
    -- but as a reported figure it is nonsense, and it means the number
    cannot be compared against a drawdown limit expressed in percent.

    Reachable in practice: engine.py halts on the DAILY loss limit, and
    nothing stops cumulative equity going negative across days."""
    assert max_drawdown([1000, -500]) == pytest.approx(1.5)


# ------------------------------------------------------ per_trade_sharpe


def test_sharpe_needs_at_least_two_trades():
    assert per_trade_sharpe([]) == 0.0
    assert per_trade_sharpe([5.0]) == 0.0


def test_sharpe_of_zero_variance_is_zero_not_infinite():
    """Every trade identical means std=0. Without the guard this is a
    division by zero, and a 'perfect' strategy would report inf."""
    assert per_trade_sharpe([3.0, 3.0, 3.0]) == 0.0


def test_sharpe_sign_follows_mean_pnl():
    assert per_trade_sharpe([1.0, 2.0, 3.0, 4.0]) > 0
    assert per_trade_sharpe([-1.0, -2.0, -3.0, -4.0]) < 0


# ------------------------------------------------------------ summarize


def test_summarize_of_no_trades_does_not_divide_by_zero():
    s = summarize([], [], "empty")
    assert s.n_trades == 0
    assert s.win_rate == 0.0
    assert s.total_pnl == 0.0
    assert s.mean_pnl_ci == (0.0, 0.0)


def test_summarize_counts_and_totals_correctly():
    trades = [trade(10.0), trade(-5.0), trade(7.5), trade(-2.5)]
    s = summarize(trades, [1000, 1010, 1005, 1012.5, 1010], "mixed")
    assert s.n_trades == 4
    assert s.wins == 2
    assert s.win_rate == pytest.approx(0.5)
    assert s.total_pnl == pytest.approx(10.0)
    assert s.mean_pnl_per_trade == pytest.approx(2.5)


def test_summarize_warns_on_a_small_sample():
    s = summarize([trade(1.0)] * 10, [1000] * 10, "small")
    assert any("only 10 trades" in n for n in s.notes)


def test_summarize_flags_a_ci_straddling_zero():
    """The note that stops a coin-flip result being read as edge."""
    trades = [trade(5.0), trade(-5.0)] * 60
    s = summarize(trades, [1000] * 120, "noise")
    assert any("not yet statistically distinguishable from zero" in n for n in s.notes)


def test_summarize_does_not_flag_a_clearly_positive_result():
    trades = [trade(10.0, won=True) for _ in range(200)]
    s = summarize(trades, list(range(200)), "certain")
    assert not any("distinguishable from zero" in n for n in s.notes)
    assert not any("only" in n for n in s.notes)


def test_win_rate_is_never_compared_against_fifty_percent():
    """Deliberate design decision, documented in stats.py: on a market that
    prices probability, a strategy entering only likely winners shows a high
    win rate with zero edge because it pays more for those positions. A note
    claiming otherwise would undo the point of the module."""
    trades = [trade(0.5, won=True)] * 90 + [trade(-9.0, won=False)] * 10
    s = summarize(trades, [1000] * 100, "high win rate, negative edge")
    assert s.win_rate == pytest.approx(0.9)
    assert s.total_pnl < 0
    assert not any("50%" in n or "coin" in n.lower() for n in s.notes)
