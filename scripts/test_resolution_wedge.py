#!/usr/bin/env python3
"""
Tests a STRUCTURAL edge hypothesis, not a statistical one.

The setup: these markets settle on the Chainlink BTC/USD stream -- an
aggregate across venues, and the market description is explicit that it is
"not according to other sources or spot markets". But the price everyone
watches, and the price this harness's signals read, is Binance. Over 8,640
windows, the Binance-implied outcome and the actual settlement disagree on
3.4% of them, concentrated in near-flat windows (median move $2 vs $34
overall).

The hypothesis: when Binance has moved MORE than the cross-venue consensus
by the decision moment, traders anchored to Binance overprice that
direction, while Chainlink will settle nearer the consensus. If so,
`move_disagreement` (Binance move minus consensus move, since the window
opened) predicts the settlement *beyond* what the market price already
knows, and its coefficient should be NEGATIVE.

Unlike the momentum signal, this is a claim about market microstructure --
a wedge between the feed being traded and the feed being settled on -- so
it is not obviously arbitraged away by people who are all watching the same
Binance chart.

Same discipline as fit_model.py: the market's logit enters as a fixed
offset, so coefficients measure departure from the market price; the split
is by time; and the holdout is touched once.

Usage:
  python scripts/fetch_alt_exchange.py --venue okx      --start 2026-06-24 --end 2026-07-23 --out data/alt/okx_bars.csv
  python scripts/fetch_alt_exchange.py --venue coinbase --start 2026-06-24 --end 2026-07-23 --out data/alt/coinbase_bars.csv
  python scripts/test_resolution_wedge.py --data-dir data/real --alt data/alt/okx_bars.csv data/alt/coinbase_bars.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from bisect import bisect_right
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.stats import bootstrap_ci, wilson_ci
from src.data.provider import DataProvider
from scripts.fit_model import (fit_logistic, logit, predict, reroll_outcomes,
                               simulate, summarize)


def load_alt(path: Path):
    ts, op, close = [], {}, {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            t = int(row["timestamp_ms"])
            ts.append(t)
            op[t] = float(row["open"])
            close[t] = float(row["close"])
    ts.sort()
    return {"ts": ts, "open": op, "close": close}


def price_at_open(venue, window_start_ms):
    """Open of the bar that starts the window. None if that minute is missing
    -- never silently substitute a neighbouring minute, since the whole
    measurement is about sub-dollar differences between venues."""
    return venue["open"].get(window_start_ms)


def close_as_of(venue, ts_ms, bar_ms=60_000):
    """Close of the last bar that had COMPLETED by ts_ms (same look-ahead
    rule as DataProvider.bars_as_of)."""
    idx = bisect_right(venue["ts"], ts_ms - bar_ms) - 1
    if idx < 0:
        return None
    return venue["close"][venue["ts"][idx]]


def build(provider, venues, entry_offset_sec=120):
    rows = []
    missing = 0
    for w in provider.resolutions:
        if w.market_prob_up_at_decision is None or not w.resolved_outcome:
            continue
        decision_ts = w.window_end_ms - entry_offset_sec * 1000
        if w.prob_age_sec:
            decision_ts -= int(w.prob_age_sec * 1000)
        if decision_ts < w.window_start_ms:
            decision_ts = w.window_start_ms

        binance_bars = provider.bars_as_of(decision_ts, lookback=5)
        in_win = [b for b in binance_bars if b.timestamp_ms >= w.window_start_ms]
        if not in_win:
            continue
        binance_move = in_win[-1].close - w.open_price

        alt_moves = []
        for v in venues:
            o = price_at_open(v, w.window_start_ms)
            c = close_as_of(v, decision_ts)
            if o is None or c is None:
                continue
            alt_moves.append(c - o)
        if len(alt_moves) < len(venues):
            missing += 1
            if not alt_moves:
                continue

        consensus_move = statistics.median(alt_moves)
        rows.append({
            "window_start_ms": w.window_start_ms,
            "outcome_up": 1 if w.outcome == "UP" else 0,
            "market_prob_up": w.market_prob_up_at_decision,
            "binance_move": binance_move,
            "consensus_move": consensus_move,
            "features": {
                # the hypothesis: positive = Binance ran ahead of the pack,
                # so the market (watching Binance) overprices UP
                "move_disagreement": binance_move - consensus_move,
                "binance_move_scaled": binance_move / 100.0,
            },
        })
    if missing:
        print(f"  note: {missing} window(s) missing at least one alt venue")
    return rows


FEATURES = ["move_disagreement", "binance_move_scaled"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--alt", nargs="+", required=True, help="alt-venue bar CSVs")
    ap.add_argument("--friction", type=float, default=0.010,
                    help="price concession per entry (default 0.010 = measured half-spread + slippage)")
    args = ap.parse_args()

    provider = DataProvider(args.data_dir)
    venues = [load_alt(Path(p)) for p in args.alt]
    print(f"loaded {len(venues)} alt venue(s): " + ", ".join(Path(p).stem for p in args.alt))

    rows = build(provider, venues)
    rows.sort(key=lambda r: r["window_start_ms"])
    print(f"{len(rows)} windows with real odds, real resolution and alt-venue coverage\n")
    if len(rows) < 300:
        raise SystemExit("too few windows")

    # --- descriptive: does the wedge exist at all? ---
    disagree = [r for r in rows
                if (r["binance_move"] >= 0) != (r["outcome_up"] == 1)]
    print(f"Binance-implied direction disagrees with the settlement on "
          f"{len(disagree)} / {len(rows)} windows ({100*len(disagree)/len(rows):.2f}%)")

    both = [r for r in rows if abs(r["features"]["move_disagreement"]) > 0]
    if both:
        md = sorted(abs(r["features"]["move_disagreement"]) for r in both)
        print(f"|Binance move - consensus move|: median ${md[len(md)//2]:.2f}, "
              f"p90 ${md[int(0.9*len(md))]:.2f}, max ${md[-1]:.2f}")

    # does the wedge point the way the hypothesis says?
    ahead = [r for r in rows if r["features"]["move_disagreement"] > 0]
    behind = [r for r in rows if r["features"]["move_disagreement"] < 0]
    for label, subset in (("Binance ahead of consensus", ahead),
                          ("Binance behind consensus", behind)):
        if len(subset) < 30:
            continue
        up = sum(r["outcome_up"] for r in subset)
        pred = sum(r["market_prob_up"] for r in subset) / len(subset)
        lo, hi = wilson_ci(up, len(subset))
        flag = ""
        if hi < pred:
            flag = "  <- settles UP LESS often than priced"
        elif lo > pred:
            flag = "  <- settles UP MORE often than priced"
        print(f"  {label:<28} n={len(subset):>5}  priced {pred:.1%}  settled UP {up/len(subset):.1%}  "
              f"[{lo:.1%}-{hi:.1%}]{flag}")

    # --- the actual test, with the usual discipline ---
    n = len(rows)
    train, validate, holdout = rows[:n//2], rows[n//2:3*n//4], rows[3*n//4:]
    print(f"\ntrain {len(train)} / validate {len(validate)} / holdout {len(holdout)}")
    print(f"friction: {args.friction:.4f} ({args.friction*100:.2f}pp)\n")

    beta = fit_logistic(train, FEATURES)
    print("coefficients (departure from the MARKET price):")
    print(f"  {'intercept':<22} {beta[0]:+.5f}")
    for name, b in zip(FEATURES, beta[1:]):
        note = "   <- hypothesis says this should be NEGATIVE" if name == "move_disagreement" else ""
        print(f"  {name:<22} {b:+.5f}{note}")

    devs = sorted(abs(predict(beta, r, FEATURES) - r["market_prob_up"]) for r in validate)
    print(f"\n|model - market| on validate: median {devs[len(devs)//2]:.4f}, p90 {devs[int(0.9*len(devs))]:.4f}")

    print("\n--- validate ---")
    best = None
    for thr in (0.005, 0.01, 0.02, 0.03, 0.05):
        res = summarize(simulate(validate, beta, FEATURES, thr, args.friction), f"threshold {thr:.3f}")
        if res and res["n"] >= 100 and res["ci"][0] > 0 and (best is None or res["ci"][0] > best[1]["ci"][0]):
            best = (thr, res)

    print("\n--- holdout (once) ---")
    if best is None:
        print("  nothing passed validate; holdout left untouched")
    else:
        summarize(simulate(holdout, beta, FEATURES, best[0], args.friction),
                  f"HOLDOUT thr {best[0]:.3f}")

    print("\n--- zero-friction (is there anything there before costs?) ---")
    summarize(simulate(validate, beta, FEATURES, 0.005, 0.0), "validate thr 0.005, no friction")


if __name__ == "__main__":
    main()
