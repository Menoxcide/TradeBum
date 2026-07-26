"""The correlation tracker's size adjustment.

Thirty lines that can zero a position, and they were entirely untested. The
rules are also genuinely non-obvious -- the two branches look at overlapping
but different slices of history -- so the behaviour is pinned case by case
rather than described.
"""

from __future__ import annotations

import pytest

from src.risk.correlation_tracker import CorrelationTracker


def tracker(history=()):
    t = CorrelationTracker()
    for side, pnl in history:
        t.record(side, pnl)
    return t


def test_no_history_means_no_adjustment():
    assert tracker().get_adjustment("UP") == 1.0


@pytest.mark.parametrize("history", [
    [("UP", 5.0)],
    [("UP", 5.0), ("UP", 5.0)],
])
def test_a_streak_shorter_than_three_wins_is_not_faded(history):
    assert tracker(history).get_adjustment("UP") == 1.0


def test_three_same_side_wins_halves_the_next_position():
    """The mean-reversion belief this module encodes: fade a hot streak."""
    assert tracker([("UP", 5.0)] * 3).get_adjustment("UP") == 0.5


def test_the_streak_rule_is_side_specific():
    """Three UP wins says nothing about a DOWN proposal."""
    t = tracker([("UP", 5.0)] * 3)
    assert t.get_adjustment("UP") == 0.5
    assert t.get_adjustment("DOWN") == 1.0


def test_a_mixed_streak_is_not_faded():
    assert tracker([("UP", 5.0), ("DOWN", 5.0), ("UP", 5.0)]).get_adjustment("UP") == 1.0
    assert tracker([("UP", 5.0), ("UP", -1.0), ("UP", 5.0)]).get_adjustment("UP") == 1.0


def test_two_same_side_losses_skip_the_trade_entirely():
    assert tracker([("UP", -1.0), ("UP", -2.0)]).get_adjustment("UP") == 0.0


def test_a_win_before_two_losses_does_not_rescue_the_trade():
    """Non-obvious, and worth stating explicitly.

    The three-win branch inspects all three recent results, but the
    two-loss branch inspects only the last two. So win/loss/loss still
    skips: the earlier win is outside the slice the loss rule reads. That
    is a defensible reading of 'two losing trades in a row on this side',
    but it is not what the shape of the code suggests at a glance."""
    assert tracker([("UP", 5.0), ("UP", -1.0), ("UP", -1.0)]).get_adjustment("UP") == 0.0


def test_an_intervening_opposite_side_trade_breaks_the_loss_streak():
    assert tracker([("UP", -1.0), ("DOWN", -1.0), ("UP", -1.0)]).get_adjustment("UP") == 1.0


def test_a_break_even_trade_counts_as_neither_win_nor_loss():
    """Both rules use strict inequalities, so pnl of exactly 0 breaks a
    streak in either direction."""
    assert tracker([("UP", 5.0), ("UP", 5.0), ("UP", 0.0)]).get_adjustment("UP") == 1.0
    assert tracker([("UP", -1.0), ("UP", 0.0)]).get_adjustment("UP") == 1.0


def test_only_the_three_most_recent_trades_are_consulted():
    """A long-past losing pair must not keep suppressing new trades."""
    t = tracker([("UP", -1.0), ("UP", -1.0), ("UP", 5.0), ("DOWN", 1.0), ("DOWN", 1.0)])
    assert t.get_adjustment("UP") == 1.0


def test_history_is_bounded_by_the_lookback():
    t = CorrelationTracker(lookback=10)
    for _ in range(50):
        t.record("UP", 1.0)
    assert len(t.trade_history) == 10


def test_adjustment_is_only_ever_one_of_the_three_documented_values():
    """Callers multiply a position size by this, and engine.py treats 0.0 as
    'skip'. Any other value would silently resize trades."""
    import itertools

    for combo in itertools.product([("UP", 5.0), ("UP", -1.0), ("DOWN", 5.0), ("DOWN", -1.0)], repeat=3):
        for side in ("UP", "DOWN"):
            assert tracker(list(combo)).get_adjustment(side) in (0.0, 0.5, 1.0)
