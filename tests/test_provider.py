"""DataProvider: the seam where real market data enters the system.

Reported 0% coverage before this file. Everything that had ever been tested
ran through InMemoryProvider, so the CSV loader and -- more importantly --
the look-ahead guarantee on the class that actually reads production data
were unverified.
"""

from __future__ import annotations

import pytest

from src.data.provider import DataProvider
from src.data.schema import Bar, ResolvedWindow, infer_bar_duration_ms

BAR_MS = 60_000


def write_csv(path, header, rows):
    path.write_text("\n".join([header] + [",".join(str(c) for c in r) for r in rows]) + "\n")


@pytest.fixture
def data_dir(tmp_path):
    """A minimal but complete dataset: 20 one-minute bars and two windows."""
    write_csv(
        tmp_path / "bars.csv",
        "timestamp_ms,open,high,low,close,volume",
        [(i * BAR_MS, 100 + i, 101 + i, 99 + i, 100.5 + i, 10 + i) for i in range(20)],
    )
    write_csv(
        tmp_path / "orderbook.csv",
        "timestamp_ms,bid_prices,bid_sizes,ask_prices,ask_sizes",
        [(i * BAR_MS, "100;99;98", "1;2;3", "101;102;103", "4;5;6") for i in range(20)],
    )
    write_csv(tmp_path / "funding.csv", "timestamp_ms,funding_rate",
              [(i * BAR_MS, 0.0001 * i) for i in range(20)])
    write_csv(
        tmp_path / "resolutions.csv",
        "window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision",
        [(0, 300_000, 100.0, 105.0, 0.6), (300_000, 600_000, 105.0, 102.0, 0.45)],
    )
    return tmp_path


# ------------------------------------------------------------- loading


def test_loads_every_file(data_dir):
    p = DataProvider(data_dir)
    assert len(p.bars) == 20
    assert len(p.orderbook_snapshots) == 20
    assert len(p.funding_snapshots) == 20
    assert len(p.resolutions) == 2


def test_missing_resolutions_fails_loudly(tmp_path):
    """Ground truth is not optional. Silently returning an empty list would
    produce a backtest with zero trades and no error -- indistinguishable
    from a strategy that never qualified."""
    with pytest.raises(FileNotFoundError, match="resolutions.csv"):
        DataProvider(tmp_path)


def test_optional_files_may_be_absent(tmp_path):
    write_csv(
        tmp_path / "resolutions.csv",
        "window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision",
        [(0, 300_000, 100.0, 105.0, 0.6)],
    )
    p = DataProvider(tmp_path)
    assert p.bars == [] and p.orderbook_snapshots == [] and p.funding_snapshots == []
    assert p.funding_as_of(0) == 0.0
    assert p.orderbook_as_of(0) is None
    assert p.bars_as_of(0) == []


def test_rows_are_sorted_even_when_the_file_is_not(tmp_path, data_dir):
    """The docstring promises sorting. Every as-of lookup is a bisect over
    these lists, so an unsorted file would silently return wrong answers
    rather than fail."""
    write_csv(
        data_dir / "bars.csv",
        "timestamp_ms,open,high,low,close,volume",
        [(i * BAR_MS, 100, 101, 99, 100, 1) for i in (5, 1, 9, 0, 3)],
    )
    p = DataProvider(data_dir)
    assert [b.timestamp_ms for b in p.bars] == sorted(b.timestamp_ms for b in p.bars)


def test_orderbook_levels_parse_from_semicolon_lists(data_dir):
    p = DataProvider(data_dir)
    book = p.orderbook_snapshots[0]
    assert book.bid_prices == [100.0, 99.0, 98.0]
    assert book.ask_sizes == [4.0, 5.0, 6.0]
    assert book.mid == pytest.approx(100.5)
    assert book.spread == pytest.approx(1.0)


def test_blank_market_prob_becomes_none_not_zero(data_dir):
    """A blank parsed as 0.0 would mean 'the market is certain this resolves
    DOWN', which is a very different claim from 'we have no price'."""
    write_csv(
        data_dir / "resolutions.csv",
        "window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision",
        [(0, 300_000, 100.0, 105.0, "")],
    )
    assert DataProvider(data_dir).resolutions[0].market_prob_up_at_decision is None


def test_optional_resolution_columns_are_read_when_present(data_dir):
    write_csv(
        data_dir / "resolutions.csv",
        "window_start_ms,window_end_ms,open_price,close_price,"
        "market_prob_up_at_decision,prob_age_sec,resolved_outcome",
        [(0, 300_000, 100.0, 105.0, 0.6, 56.0, "DOWN"),
         (300_000, 600_000, 105.0, 102.0, 0.45, "", "")],
    )
    first, second = DataProvider(data_dir).resolutions
    assert first.prob_age_sec == pytest.approx(56.0)
    assert first.resolved_outcome == "DOWN"
    assert first.outcome == "DOWN", "venue resolution overrides the price-derived guess"
    assert second.prob_age_sec is None
    assert second.resolved_outcome is None
    assert second.outcome == "DOWN", "falls back to close < open"


def test_a_garbled_resolved_outcome_is_ignored_rather_than_trusted(data_dir):
    write_csv(
        data_dir / "resolutions.csv",
        "window_start_ms,window_end_ms,open_price,close_price,"
        "market_prob_up_at_decision,resolved_outcome",
        [(0, 300_000, 100.0, 105.0, 0.6, "yes")],
    )
    w = DataProvider(data_dir).resolutions[0]
    assert w.resolved_outcome is None
    assert w.outcome == "UP"


def test_lowercase_resolved_outcome_is_accepted(data_dir):
    write_csv(
        data_dir / "resolutions.csv",
        "window_start_ms,window_end_ms,open_price,close_price,"
        "market_prob_up_at_decision,resolved_outcome",
        [(0, 300_000, 100.0, 105.0, 0.6, " down ")],
    )
    assert DataProvider(data_dir).resolutions[0].outcome == "DOWN"


# --------------------------------------------------- look-ahead discipline


def test_bars_as_of_excludes_the_bar_still_forming(data_dir):
    """The regression that matters most in this file.

    A bar is stamped at its OPEN but its close is the price one interval
    later, so `timestamp <= ts` admits a bar carrying future information.
    On the 1-minute bars this provider reads, that is 60 seconds of
    unknowable price on a 300-second window."""
    p = DataProvider(data_dir)
    query = 10 * BAR_MS
    returned = p.bars_as_of(query, lookback=100)
    assert returned, "excluding in-progress bars must not empty the history"
    assert query not in [b.timestamp_ms for b in returned]
    assert all(b.timestamp_ms + p.bar_duration_ms <= query for b in returned)
    assert max(b.timestamp_ms for b in returned) == 9 * BAR_MS


def test_bars_as_of_never_returns_the_future(data_dir):
    p = DataProvider(data_dir)
    for query in (0, BAR_MS, 5 * BAR_MS, 19 * BAR_MS, 100 * BAR_MS):
        assert all(b.timestamp_ms <= query for b in p.bars_as_of(query, lookback=100))


def test_bars_as_of_respects_the_lookback_limit(data_dir):
    p = DataProvider(data_dir)
    assert len(p.bars_as_of(19 * BAR_MS, lookback=3)) == 3


def test_a_query_mid_bar_still_excludes_that_bar(data_dir):
    """Decision timestamps do not land on bar boundaries -- engine.py shifts
    them by the quote's staleness. A query 30s into a 60s bar must still not
    see that bar's close."""
    p = DataProvider(data_dir)
    returned = p.bars_as_of(10 * BAR_MS + 30_000, lookback=100)
    assert max(b.timestamp_ms for b in returned) == 9 * BAR_MS


# ------------------------------------------------------ bar_duration_ms


def test_duration_is_inferred_from_the_data(data_dir):
    assert DataProvider(data_dir).bar_duration_ms == BAR_MS


def test_duration_survives_a_gap_in_the_history():
    """Median, not mean: one exchange outage must not stretch the inferred
    interval and start admitting in-progress bars."""
    bars = [Bar(i * 60_000, 1, 1, 1, 1, 1) for i in range(10)]
    bars.append(Bar(bars[-1].timestamp_ms + 86_400_000, 1, 1, 1, 1, 1))
    assert infer_bar_duration_ms(bars) == 60_000


def test_duration_of_insufficient_history_is_zero():
    assert infer_bar_duration_ms([]) == 0
    assert infer_bar_duration_ms([Bar(0, 1, 1, 1, 1, 1)]) == 0


def test_duplicate_timestamps_do_not_yield_a_zero_duration():
    """A zero duration would disable the in-progress-bar exclusion, silently
    reopening the look-ahead hole."""
    bars = [Bar(0, 1, 1, 1, 1, 1), Bar(0, 1, 1, 1, 1, 1)] + [
        Bar(i * 30_000, 1, 1, 1, 1, 1) for i in range(1, 10)
    ]
    assert infer_bar_duration_ms(bars) == 30_000


def test_unknown_duration_falls_back_to_the_weaker_guarantee(tmp_path):
    """With a single bar the interval cannot be inferred. Returning nothing
    would be safe but useless; the loader keeps the timestamp<=ts behaviour
    and the engine's own `len(bars) < 5` check catches it."""
    write_csv(tmp_path / "bars.csv", "timestamp_ms,open,high,low,close,volume",
              [(0, 100, 101, 99, 100, 1)])
    write_csv(tmp_path / "resolutions.csv",
              "window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision",
              [(0, 300_000, 100.0, 105.0, 0.6)])
    p = DataProvider(tmp_path)
    assert p.bar_duration_ms == 0
    assert len(p.bars_as_of(0, lookback=10)) == 1


# ------------------------------------------------- other as-of lookups


def test_as_of_lookups_return_the_most_recent_at_or_before(data_dir):
    p = DataProvider(data_dir)
    assert p.orderbook_as_of(5 * BAR_MS + 1).timestamp_ms == 5 * BAR_MS
    assert p.orderbook_as_of(5 * BAR_MS).timestamp_ms == 5 * BAR_MS
    assert p.funding_as_of(5 * BAR_MS) == pytest.approx(0.0005)
    assert p.funding_as_of(5 * BAR_MS - 1) == pytest.approx(0.0004)


def test_as_of_lookups_before_any_data_are_neutral(data_dir):
    p = DataProvider(data_dir)
    assert p.orderbook_as_of(-1) is None
    assert p.funding_as_of(-1) == 0.0


def test_as_of_lookups_past_the_end_return_the_last_known(data_dir):
    p = DataProvider(data_dir)
    assert p.orderbook_as_of(10 ** 12).timestamp_ms == 19 * BAR_MS
    assert p.funding_as_of(10 ** 12) == pytest.approx(0.0019)


# ---------------------------------------------------- ResolvedWindow


@pytest.mark.parametrize("open_price, close_price, expected", [
    (100.0, 101.0, "UP"),
    (100.0, 99.0, "DOWN"),
    (100.0, 100.0, "UP"),  # 'greater than or equal' resolves UP
])
def test_price_derived_outcome(open_price, close_price, expected):
    assert ResolvedWindow(0, 300_000, open_price, close_price).outcome == expected


def test_move_usd_is_signed():
    assert ResolvedWindow(0, 300_000, 100.0, 105.0).move_usd == pytest.approx(5.0)
    assert ResolvedWindow(0, 300_000, 100.0, 95.0).move_usd == pytest.approx(-5.0)
