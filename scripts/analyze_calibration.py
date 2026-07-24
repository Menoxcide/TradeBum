#!/usr/bin/env python3
"""
Calibration check: does the price a trade was entered at actually predict
how often that trade won?

This is the sharpest question the harness can ask, and mean-pnl alone
doesn't answer it. When trades are priced at geometry-implied fair value
(the no-real-Polymarket-odds path -- see build_windows_from_bars.py), the
entry price IS a probability forecast: "a driftless random walk that has
moved this far with this long left finishes on this side p% of the time".
So:

    realized win rate > predicted  ->  momentum has continuation edge;
                                       the move keeps going more often than
                                       geometry says it should
    realized win rate ~ predicted  ->  no edge; 5-minute BTC moves are
                                       behaving like a random walk, and the
                                       whole strategy is paying spread for
                                       a coin the market already priced
    realized win rate < predicted  ->  negative edge; moves mean-revert
                                       relative to geometry, and trading
                                       WITH momentum is the wrong side

Reported per probability bucket, because an aggregate number hides the
shape: a strategy can be well-calibrated at 60% and badly wrong at 95%,
and it's the 95% bucket where a momentum strategy actually places its money.

Wilson intervals throughout -- at 95%-ish win rates the normal
approximation is unusable, and a bucket with 30 trades in it tells you far
less than it appears to.

Usage:
  python scripts/run_backtest.py --data-dir ./data/historical --profile research \
      --trades-output ./data/trades.jsonl
  python scripts/analyze_calibration.py --trades ./data/trades.jsonl --strategy naive_momentum
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.stats import wilson_ci


def load_trades(path: Path, strategy: str) -> list[dict]:
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            if t.get("strategy") == strategy:
                trades.append(t)
    return trades


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", required=True, help="JSONL from run_backtest.py --trades-output")
    ap.add_argument("--strategy", default="naive_momentum",
                    choices=["coinflip", "naive_momentum", "confluence"])
    ap.add_argument("--buckets", default="0.5,0.7,0.8,0.9,0.95,0.98,1.01",
                    help="comma-separated bucket edges on entry price (=predicted win probability)")
    args = ap.parse_args()

    trades = load_trades(Path(args.trades), args.strategy)
    if not trades:
        raise SystemExit(f"no trades for strategy={args.strategy} in {args.trades}")

    edges = [float(x) for x in args.buckets.split(",")]
    sources = {t.get("market_prob_source", "unknown") for t in trades}

    print(f"=== calibration: {args.strategy} ({len(trades)} trades) ===")
    print(f"price source: {', '.join(sorted(sources))}")
    if sources == {"fair_value_fallback"}:
        print("  (entry prices are geometry-implied fair value, so 'predicted' below is\n"
              "   pure random-walk math -- beating it is exactly what 'momentum edge' means)")
    print()
    print(f"{'predicted':>16}  {'n':>6}  {'predicted':>10}  {'realized':>10}  "
          f"{'realized 95% CI':>20}  {'edge (pp)':>10}")

    total_pred = 0.0
    for lo, hi in zip(edges, edges[1:]):
        bucket = [t for t in trades if lo <= t["entry_price"] < hi]
        if not bucket:
            continue
        n = len(bucket)
        wins = sum(1 for t in bucket if t["won"])
        predicted = sum(t["entry_price"] for t in bucket) / n
        realized = wins / n
        ci = wilson_ci(wins, n)
        total_pred += predicted * n
        flag = ""
        if ci[0] > predicted:
            flag = "  <- realized ABOVE predicted (CI excludes it)"
        elif ci[1] < predicted:
            flag = "  <- realized BELOW predicted (CI excludes it)"
        print(f"{lo:>7.2f}-{hi:<7.2f}  {n:>6}  {predicted:>9.1%}  {realized:>9.1%}  "
              f"{ci[0]:>8.1%} - {ci[1]:<8.1%}  {100*(realized-predicted):>+9.1f}{flag}")

    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    predicted = total_pred / n
    realized = wins / n
    ci = wilson_ci(wins, n)
    print()
    print(f"{'ALL':>16}  {n:>6}  {predicted:>9.1%}  {realized:>9.1%}  "
          f"{ci[0]:>8.1%} - {ci[1]:<8.1%}  {100*(realized-predicted):>+9.1f}")
    print()
    if ci[0] > predicted:
        print("VERDICT: realized win rate is above the priced-in probability, and the 95%\n"
              "interval excludes it. That is a real continuation edge on this sample --\n"
              "next step is whether it survives real Polymarket prices, spread and fees.")
    elif ci[1] < predicted:
        print("VERDICT: realized win rate is BELOW the priced-in probability, with the 95%\n"
              "interval excluding it. On this sample, entering with momentum is worse than\n"
              "random-walk geometry -- the edge is negative, not merely absent. No amount of\n"
              "execution tuning fixes a signal pointed the wrong way.")
    else:
        print("VERDICT: realized win rate is statistically indistinguishable from the\n"
              "priced-in probability. No detectable edge either way on this sample -- the\n"
              "move-size signal is not telling you anything the window's geometry didn't\n"
              "already imply. Note 'no edge detected' is not 'no edge exists': check how\n"
              "wide that interval is before concluding anything.")


if __name__ == "__main__":
    main()
