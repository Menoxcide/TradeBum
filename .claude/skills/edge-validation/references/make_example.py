#!/usr/bin/env python3
"""
Generates three example datasets so you can see what the validator says in
each of the three situations that matter. Run this, then run validate_edge.py
against each file and compare.

  null.csv    a well-calibrated market and features that carry nothing.
              The honest answer is "no edge", and the detection floor tells
              you whether that answer means anything.

  real.csv    a genuine, tradeable edge: `momentum` is a PERSISTENT signal
              (autocorrelated, observable before the outcome) that the market
              only partly prices in. The validator should find it.

              The persistence is the whole point. An earlier version of this
              generator drew momentum as fresh white noise each period and had
              the outcome depend on it -- which is a random number that
              predicts the future and that the market cannot see. That is
              operationally identical to a data leak, and correctly gets
              flagged as one: it dies under a lag test, because there is
              nothing there to survive. Real signals are built from
              information observable BEFORE the decision, so lagging them
              degrades the edge without erasing it.

  leaky.csv   `momentum` contains a sliver of the outcome, simulating
              look-ahead. The validator will report a large edge, because no
              statistical test can distinguish a leak from a discovery -- the
              data really does contain the future. This one is here to show
              you what that looks like, so you recognise it in your own work:
              an implausible effect size is a diagnostic, not a result.
              Chase it back to the code that built the feature.

Usage:
  python examples/make_example.py
  python validate_edge.py --data examples/null.csv --time-col ts \
      --outcome-col won --market-prob-col mkt_p \
      --features momentum,volume_ratio --friction 0.01
"""

import csv
import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER = ["ts", "won", "mkt_p", "momentum", "volume_ratio"]


def write(name, rows):
    path = HERE / name
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"  {name:<12} {len(rows):>6} rows")


def make_null(n=9000, seed=2):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        p = min(0.97, max(0.03, rng.gauss(0.5, 0.22)))
        rows.append([1700000000 + i * 300, 1 if rng.random() < p else 0,
                     round(p, 4), round(rng.gauss(0, 1), 4),
                     round(rng.gauss(1, 0.3), 4)])
    return rows


def make_real(n=20000, edge=0.15, persistence=0.85, seed=3):
    """A genuine edge: momentum follows an AR(1) process, so it is observable
    before the outcome and survives being lagged. The market prices in part of
    it (`priced_fraction`), leaving the remainder as real, harvestable edge --
    which is what an actual inefficiency looks like.

    The edge is deliberately sized to clear this sample's own detection floor,
    so the example demonstrates what a positive verdict looks like. Real
    inefficiencies are far smaller and need correspondingly more data -- which
    is the lesson the detection floor exists to deliver."""
    rng = random.Random(seed)
    rows = []
    mom = 0.0
    priced_fraction = 0.4
    for i in range(n):
        mom = persistence * mom + rng.gauss(0, 1) * (1 - persistence ** 2) ** 0.5
        tilt = edge * math.tanh(mom)
        base = min(0.94, max(0.06, rng.gauss(0.5, 0.20)))
        # the market sees and prices SOME of the signal, but not all of it
        mkt = min(0.97, max(0.03, base + priced_fraction * tilt))
        true_p = min(0.985, max(0.015, base + tilt))
        rows.append([1700000000 + i * 300, 1 if rng.random() < true_p else 0,
                     round(mkt, 4), round(mom, 4), round(rng.gauss(1, 0.3), 4)])
    return rows


def make_leaky(n=12000, seed=1):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        p = min(0.95, max(0.05, rng.gauss(0.5, 0.18)))
        y = 1 if rng.random() < p else 0
        mom = rng.gauss(0, 1) + (1.4 if y else -1.4)  # the leak
        rows.append([1700000000 + i * 300, y, round(p, 4), round(mom, 4),
                     round(rng.gauss(1, 0.3), 4)])
    return rows


if __name__ == "__main__":
    print("writing example datasets:")
    write("null.csv", make_null())
    write("real.csv", make_real())
    write("leaky.csv", make_leaky())
    print("\nNow run the validator against each and compare what it says.")
