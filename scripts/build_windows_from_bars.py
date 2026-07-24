#!/usr/bin/env python3
"""
Builds resolutions.csv straight from bars.csv, with no Polymarket data.

This unblocks README step 2 -- "does momentum on real BTC beat pure
random-walk math" -- which needs nothing but real BTC prices. Every window
gets a real open, a real close, and therefore a real UP/DOWN outcome;
`market_prob_up_at_decision` is left blank on purpose, which makes the
engine price each trade at the geometry-implied fair value estimated from
real volatility (see src/backtest/fair_value.py) instead of at a real
Polymarket price.

What that does and doesn't tell you:
  DOES     -- whether a $70+ move partway through a 5-minute window predicts
              the close BETTER than a driftless random walk implies. If it
              doesn't, there is no edge here to sell, and no amount of
              Polymarket data will create one.
  DOESN'T  -- whether any such edge survives Polymarket's actual prices,
              spreads, and fees. Real odds are usually *better* informed than
              the geometry estimate, so this is an upper bound on edge, not a
              forecast of P&L. Use scripts/build_resolutions.py once you have
              real captured odds.

Window alignment: windows are aligned to wall-clock 5-minute boundaries
(:00, :05, :10, ...), which is how the Polymarket 5-minute BTC markets are
cut. `--window-seconds` changes that if you're modelling a different market.

Coverage: a window is only emitted if every bar in it is present. A partial
window would give an open or close from the wrong minute -- silently wrong
ground truth, which is worse than a missing row.

Usage:
  python scripts/build_windows_from_bars.py --bars ./data/historical/bars.csv \
      --out ./data/historical/resolutions.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window-seconds", type=int, default=300)
    ap.add_argument("--bar-seconds", type=int, default=60)
    ap.add_argument("--allow-partial", action="store_true",
                    help="emit windows with missing bars (off by default -- a partial "
                         "window's open/close can come from the wrong minute)")
    args = ap.parse_args()

    window_ms = args.window_seconds * 1000
    bar_ms = args.bar_seconds * 1000
    expected_bars = window_ms // bar_ms
    if expected_bars < 1:
        raise SystemExit("--window-seconds must be at least one bar long")

    buckets: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    with open(args.bars, newline="") as f:
        for row in csv.DictReader(f):
            ts = int(row["timestamp_ms"])
            buckets[ts - (ts % window_ms)].append((ts, float(row["open"]), float(row["close"])))

    rows = []
    skipped_partial = 0
    ties = 0
    for w_start in sorted(buckets):
        bars = sorted(buckets[w_start])
        if len(bars) != expected_bars and not args.allow_partial:
            skipped_partial += 1
            continue
        open_price = bars[0][1]
        close_price = bars[-1][2]
        if close_price == open_price:
            ties += 1
        rows.append([w_start, w_start + window_ms, open_price, close_price, ""])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["window_start_ms", "window_end_ms", "open_price", "close_price",
                    "market_prob_up_at_decision"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} windows -> {out_path}")
    if skipped_partial:
        print(f"  skipped {skipped_partial} window(s) with incomplete bar coverage "
              f"(expected {expected_bars} bars each; pass --allow-partial to keep them)")
    if ties:
        pct = 100.0 * ties / len(rows) if rows else 0.0
        print(f"  {ties} window(s) closed exactly at their open ({pct:.2f}%). This harness's "
              f"ResolvedWindow.outcome counts a tie as UP -- confirm how the real market "
              f"resolves an unchanged price before trusting P&L on these.")
    print("\n  market_prob_up_at_decision is intentionally blank: the engine will price "
          "\n  trades at geometry-implied fair value. That answers 'is there edge at all', "
          "\n  not 'is there edge at Polymarket's prices'. See this script's docstring.")
    print(f"\nNow run:\n  python scripts/run_backtest.py --data-dir {out_path.parent} "
          f"--profile conservative --sensitivity")


if __name__ == "__main__":
    main()
