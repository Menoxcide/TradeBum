"""
DataProvider: turns historical files into the objects backtest/engine.py needs.

This is the seam where you plug in real data. Nothing in this file invents
market behavior — it only reads what you give it. See README.md for exactly
what CSVs to produce and where the raw data can come from.
"""

from __future__ import annotations

import csv
from bisect import bisect_right
from pathlib import Path

from src.data.schema import (
    Bar,
    FundingSnapshot,
    OrderBookSnapshot,
    ResolvedWindow,
    infer_bar_duration_ms,
)


class DataProvider:
    """
    Loads four CSVs from a directory:

      bars.csv        timestamp_ms,open,high,low,close,volume
      orderbook.csv    timestamp_ms,bid_prices,bid_sizes,ask_prices,ask_sizes
                       (bid_prices etc. are ';'-separated floats, best-to-worst)
      funding.csv      timestamp_ms,funding_rate
      resolutions.csv  window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision
                       (market_prob_up_at_decision may be blank if you don't have it)

    All timestamps are unix milliseconds, UTC. Rows must be sorted ascending
    by timestamp within each file (the loader will sort them anyway, but
    pre-sorted files load faster on large datasets).
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.bars: list[Bar] = self._load_bars()
        self.orderbook_snapshots: list[OrderBookSnapshot] = self._load_orderbook()
        self.funding_snapshots: list[FundingSnapshot] = self._load_funding()
        self.resolutions: list[ResolvedWindow] = self._load_resolutions()
        self._bar_ts = [b.timestamp_ms for b in self.bars]
        self._ob_ts = [o.timestamp_ms for o in self.orderbook_snapshots]
        self._funding_ts = [f.timestamp_ms for f in self.funding_snapshots]
        self.bar_duration_ms = infer_bar_duration_ms(self.bars)

    # ---- loaders -----------------------------------------------------

    def _load_bars(self) -> list[Bar]:
        path = self.data_dir / "bars.csv"
        if not path.exists():
            return []
        rows = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows.append(
                    Bar(
                        timestamp_ms=int(row["timestamp_ms"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                )
        rows.sort(key=lambda b: b.timestamp_ms)
        return rows

    def _load_orderbook(self) -> list[OrderBookSnapshot]:
        path = self.data_dir / "orderbook.csv"
        if not path.exists():
            return []
        rows = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                def parse_list(s: str) -> list[float]:
                    return [float(x) for x in s.split(";") if x.strip() != ""]

                rows.append(
                    OrderBookSnapshot(
                        timestamp_ms=int(row["timestamp_ms"]),
                        bid_prices=parse_list(row.get("bid_prices", "")),
                        bid_sizes=parse_list(row.get("bid_sizes", "")),
                        ask_prices=parse_list(row.get("ask_prices", "")),
                        ask_sizes=parse_list(row.get("ask_sizes", "")),
                    )
                )
        rows.sort(key=lambda o: o.timestamp_ms)
        return rows

    def _load_funding(self) -> list[FundingSnapshot]:
        path = self.data_dir / "funding.csv"
        if not path.exists():
            return []
        rows = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows.append(
                    FundingSnapshot(
                        timestamp_ms=int(row["timestamp_ms"]),
                        funding_rate=float(row["funding_rate"]),
                    )
                )
        rows.sort(key=lambda x: x.timestamp_ms)
        return rows

    def _load_resolutions(self) -> list[ResolvedWindow]:
        path = self.data_dir / "resolutions.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. resolutions.csv (one row per 5m window, "
                "with the actual open/close price) is required — it's the "
                "ground truth the backtester grades trades against."
            )
        rows = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                prob = row.get("market_prob_up_at_decision", "")
                age = row.get("prob_age_sec", "")
                resolved = (row.get("resolved_outcome", "") or "").strip().upper()
                if resolved not in ("UP", "DOWN"):
                    resolved = None
                rows.append(
                    ResolvedWindow(
                        window_start_ms=int(row["window_start_ms"]),
                        window_end_ms=int(row["window_end_ms"]),
                        open_price=float(row["open_price"]),
                        close_price=float(row["close_price"]),
                        market_prob_up_at_decision=float(prob) if prob not in ("", None) else None,
                        prob_age_sec=float(age) if age not in ("", None) else None,
                        resolved_outcome=resolved,
                    )
                )
        rows.sort(key=lambda r: r.window_start_ms)
        return rows

    # ---- point-in-time lookups (as-of, never look-ahead) --------------

    def bars_as_of(self, ts_ms: int, lookback: int = 30) -> list[Bar]:
        """Most recent `lookback` bars that had CLOSED by ts_ms.

        A bar is stamped at its OPEN, but its `close` is the price one whole
        interval later. So the bar opening at exactly ts_ms passes a naive
        `timestamp <= ts_ms` filter while carrying a price from the future --
        on 1-minute bars that is 60 seconds of unknowable price on a
        300-second window, which is more than enough to manufacture an edge
        out of nothing. Only bars satisfying
        `timestamp_ms + bar_duration_ms <= ts_ms` are safe to read.
        """
        if self.bar_duration_ms <= 0:
            # Duration unknown (fewer than two bars). Fall back to the
            # weaker guarantee rather than silently returning nothing.
            idx = bisect_right(self._bar_ts, ts_ms)
        else:
            idx = bisect_right(self._bar_ts, ts_ms - self.bar_duration_ms)
        return self.bars[max(0, idx - lookback):idx]

    def orderbook_as_of(self, ts_ms: int) -> OrderBookSnapshot | None:
        idx = bisect_right(self._ob_ts, ts_ms) - 1
        return self.orderbook_snapshots[idx] if idx >= 0 else None

    def funding_as_of(self, ts_ms: int) -> float:
        idx = bisect_right(self._funding_ts, ts_ms) - 1
        return self.funding_snapshots[idx].funding_rate if idx >= 0 else 0.0
