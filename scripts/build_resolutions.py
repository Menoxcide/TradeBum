#!/usr/bin/env python3
"""
Merges collect_live.py's odds_log.csv with fetch_binance_data.py's bars.csv
into the resolutions.csv the backtester needs.

odds_log.csv gives you the window bounds and the real Polymarket probability
at the decision moment. bars.csv gives you the actual open/close prices that
determine who won. This joins them.

Windows with no matching bar coverage are skipped and counted, not silently
dropped -- if that number is large, your collector and your kline fetch
don't cover the same period and any backtest on the result would be built
on a partial, possibly biased sample.

Usage:
  python scripts/build_resolutions.py \
      --odds-log ./data/live/odds_log.csv \
      --bars ./data/historical/bars.csv \
      --out ./data/historical/resolutions.csv
"""

from __future__ import annotations

import argparse
import csv
from bisect import bisect_left, bisect_right
from pathlib import Path


def load_bars(path: Path):
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append((int(row["timestamp_ms"]), float(row["open"]), float(row["close"])))
    bars.sort()
    return bars


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--odds-log", required=True)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    bars = load_bars(Path(args.bars))
    if not bars:
        raise SystemExit("bars.csv is empty -- run fetch_binance_data.py first.")
    bar_ts = [b[0] for b in bars]

    seen_windows = set()
    rows = []
    skipped_no_coverage = 0
    skipped_dupe = 0
    skipped_no_prob = 0

    with open(args.odds_log, newline="") as f:
        for rec in csv.DictReader(f):
            w_start = int(rec["window_start_ms"])
            w_end = int(rec["window_end_ms"])

            if w_start in seen_windows:
                skipped_dupe += 1
                continue

            prob_raw = rec.get("market_prob_up", "")
            if prob_raw in ("", None, "None"):
                skipped_no_prob += 1
                continue

            # first bar at/after window start, last bar strictly before window end
            i_open = bisect_left(bar_ts, w_start)
            i_close = bisect_right(bar_ts, w_end - 1) - 1
            if i_open >= len(bars) or i_close < i_open:
                skipped_no_coverage += 1
                continue

            open_price = bars[i_open][1]
            close_price = bars[i_close][2]
            seen_windows.add(w_start)
            rows.append([w_start, w_end, open_price, close_price, prob_raw])

    rows.sort()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["window_start_ms", "window_end_ms", "open_price", "close_price", "market_prob_up_at_decision"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} resolved windows -> {out_path}")
    if skipped_no_coverage:
        print(f"  skipped {skipped_no_coverage} window(s): no bar coverage "
              f"(your bars.csv and odds_log.csv don't overlap for these -- widen the kline fetch)")
    if skipped_no_prob:
        print(f"  skipped {skipped_no_prob} window(s): odds came back empty at capture time "
              f"(check raw/*.jsonl -- if this count is high the collector's parsing may be wrong)")
    if skipped_dupe:
        print(f"  skipped {skipped_dupe} duplicate window row(s)")
    if rows:
        print(f"\nNow run:\n  python scripts/run_backtest.py --data-dir {out_path.parent} --profile conservative --sensitivity")


if __name__ == "__main__":
    main()
