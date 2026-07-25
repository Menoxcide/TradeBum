#!/usr/bin/env python3
"""
Measures what it actually costs to execute on the Polymarket 5-minute BTC
markets, replacing the `assumed_polymarket_spread` guess with a number.

Why this matters more than it sounds: every "is there edge" conclusion in
this repo is compared against a friction budget of
`assumed_polymarket_spread + slippage_buffer` = 2.5 probability points,
and that 0.02 was a placeholder nobody had checked. If the real cost is
0.5pp, marginal edges become tradeable; if it's 4pp, nothing here is. The
answer decides the question, so it should not be a guess.

It also measures the thing the nominal spread hides. The quoted
bid-ask is the cost of an infinitesimal trade. What you actually pay is the
size-weighted price of walking the book, so this records the effective
price for several order sizes, not just the top-of-book spread.

Samples the live book on a schedule and records, per snapshot:
  seconds_to_close   where in the window this sample sits (the decision
                     point is T-120s, but the whole curve is informative --
                     these books thin out as expiry approaches)
  mid, best_bid, best_ask, nominal_spread
  eff_price_<N>      average fill price to buy $N of the UP token by
                     walking the ask side
  slippage_bps_<N>   that price vs the midpoint, in basis points
  ask_depth_usdc     total notional resting on the ask side

Usage:
  python scripts/measure_execution_cost.py --out ./data/live/exec_cost.csv --minutes 60
  python scripts/measure_execution_cost.py --analyze ./data/live/exec_cost.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"
SIZES = (50, 100, 250)


def current_window(ts: int | None = None, window_seconds: int = 300) -> tuple[int, int]:
    ts = ts if ts is not None else int(time.time())
    start = ts - (ts % window_seconds)
    return start, start + window_seconds


def resolve_up_token(session, window_start: int):
    r = session.get(f"{GAMMA_BASE}/events", params={"slug": f"btc-updown-5m-{window_start}"}, timeout=15)
    if r.status_code != 200:
        return None
    events = r.json()
    if not events or not events[0].get("markets"):
        return None
    m = events[0]["markets"][0]
    outcomes = m.get("outcomes")
    tokens = m.get("clobTokenIds")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(tokens, str):
        tokens = json.loads(tokens)
    if not outcomes or not tokens:
        return None
    upper = [str(o).upper() for o in outcomes]
    return tokens[upper.index("UP")] if "UP" in upper else None


def walk_book(levels, usdc: float):
    """Average price paid to spend `usdc` walking a sorted side of the book.
    Returns None if the book can't absorb it -- which is itself a finding,
    not something to paper over with the top-of-book price."""
    spent = 0.0
    shares = 0.0
    for lvl in levels:
        try:
            price = float(lvl["price"])
            size = float(lvl["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0:
            continue
        level_notional = price * size
        take = min(level_notional, usdc - spent)
        if take <= 0:
            break
        spent += take
        shares += take / price
        if spent >= usdc - 1e-9:
            break
    if shares <= 0 or spent < usdc - 1e-6:
        return None
    return spent / shares


def snapshot(session, token_id: str):
    r = session.get(f"{CLOB_BASE}/book", params={"token_id": token_id}, timeout=10)
    if r.status_code != 200:
        return None
    book = r.json()
    bids = sorted((book.get("bids") or []), key=lambda l: -float(l["price"]))
    asks = sorted((book.get("asks") or []), key=lambda l: float(l["price"]))
    if not bids or not asks:
        return None
    best_bid = float(bids[0]["price"])
    best_ask = float(asks[0]["price"])
    mid = (best_bid + best_ask) / 2
    row = {
        "mid": round(mid, 4),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "nominal_spread": round(best_ask - best_bid, 4),
        "ask_depth_usdc": round(sum(float(l["price"]) * float(l["size"]) for l in asks), 2),
    }
    for n in SIZES:
        eff = walk_book(asks, n)
        row[f"eff_price_{n}"] = round(eff, 4) if eff else ""
        row[f"slippage_bps_{n}"] = round(10_000 * (eff - mid) / mid, 1) if eff and mid > 0 else ""
    return row


def collect(out_path: Path, minutes: float, interval: float):
    session = requests.Session()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = (["timestamp", "window_start", "seconds_to_close", "mid", "best_bid", "best_ask",
               "nominal_spread", "ask_depth_usdc"]
              + [f"{k}_{n}" for n in SIZES for k in ("eff_price", "slippage_bps")])

    deadline = time.time() + minutes * 60
    token_cache: dict[int, str] = {}
    n = 0
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        while time.time() < deadline:
            now = int(time.time())
            ws, we = current_window(now)
            if ws not in token_cache:
                tok = resolve_up_token(session, ws)
                if tok:
                    token_cache[ws] = tok
                for old in [k for k in token_cache if k < ws - 900]:
                    del token_cache[old]
            token = token_cache.get(ws)
            if token:
                try:
                    row = snapshot(session, token)
                except requests.RequestException:
                    row = None
                if row:
                    row.update({"timestamp": now, "window_start": ws, "seconds_to_close": we - now})
                    w.writerow(row)
                    f.flush()
                    n += 1
                    if n % 20 == 0:
                        print(f"  {n} snapshots", flush=True)
            time.sleep(interval)
    print(f"Wrote {n} snapshots -> {out_path}")


def analyze(path: Path, decision_offset: int = 120, band: int = 20):
    rows = list(csv.DictReader(open(path, newline="")))
    if not rows:
        raise SystemExit("no snapshots")

    def fnum(r, k):
        v = r.get(k, "")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    at_decision = [r for r in rows
                   if abs(int(r["seconds_to_close"]) - decision_offset) <= band]
    print(f"{len(rows)} snapshots across {len({r['window_start'] for r in rows})} windows")
    print(f"{len(at_decision)} within +/-{band}s of the T-{decision_offset}s decision point\n")

    for label, subset in (("at the decision point", at_decision), ("all snapshots", rows)):
        if not subset:
            continue
        spreads = [x for x in (fnum(r, "nominal_spread") for r in subset) if x is not None]
        depths = [x for x in (fnum(r, "ask_depth_usdc") for r in subset) if x is not None]
        print(f"--- {label} (n={len(subset)}) ---")
        if spreads:
            spreads.sort()
            print(f"  nominal spread: median {spreads[len(spreads)//2]:.4f}  "
                  f"p90 {spreads[int(0.9*len(spreads))]:.4f}  max {spreads[-1]:.4f}")
        for n in SIZES:
            sl = [x for x in (fnum(r, f"slippage_bps_{n}") for r in subset) if x is not None]
            unfillable = sum(1 for r in subset if r.get(f"eff_price_{n}", "") == "")
            if sl:
                sl.sort()
                med_pp = sl[len(sl)//2] / 100.0
                print(f"  ${n:>3} order: median slippage vs mid {sl[len(sl)//2]:>6.1f} bps "
                      f"({med_pp:.2f}pp)  p90 {sl[int(0.9*len(sl))]:.1f} bps"
                      + (f"  [{unfillable} could not fill]" if unfillable else ""))
        if depths:
            depths.sort()
            print(f"  ask-side depth: median ${depths[len(depths)//2]:,.0f}  "
                  f"p10 ${depths[int(0.1*len(depths))]:,.0f}")
        print()

    if at_decision:
        spreads = sorted(x for x in (fnum(r, "nominal_spread") for r in at_decision) if x is not None)
        if spreads:
            half = spreads[len(spreads) // 2] / 2
            print(f"SUGGESTED CONFIG: a marketable buy crosses roughly half the spread, so\n"
                  f"  assumed_polymarket_spread ~ {spreads[len(spreads)//2]:.3f} "
                  f"(half-spread cost ~{half:.3f} = {half*100:.2f}pp)\n"
                  f"Compare against the 0.02 placeholder currently in the config.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="write snapshots here")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--interval", type=float, default=10, help="seconds between snapshots")
    ap.add_argument("--analyze", help="analyze an existing snapshot CSV instead of collecting")
    args = ap.parse_args()

    if args.analyze:
        analyze(Path(args.analyze))
    elif args.out:
        collect(Path(args.out), args.minutes, args.interval)
    else:
        ap.error("pass --out to collect or --analyze to summarize")


if __name__ == "__main__":
    main()
