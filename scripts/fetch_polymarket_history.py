#!/usr/bin/env python3
"""
Fetches REAL historical Polymarket odds and REAL resolutions for past
5-minute BTC windows, and merges them into an existing resolutions.csv.

This is README step 4 -- the test that actually matters -- and it does NOT
require waiting weeks for a prospective capture. The original plan assumed
Polymarket odds had to be collected going forward because free history was
"limited". That turns out not to hold for these markets, for two reasons
found by poking at the live API:

  1. The 5-minute markets use a DETERMINISTIC event slug:
        btc-updown-5m-<window_start_unix_seconds>
     so any past window is directly addressable, no search or guessing.
     Gamma serves closed events, including their resolution.
  2. CLOB /prices-history serves the traded price series for a closed
     market's token, at 1-minute fidelity, well after settlement.

So you can reconstruct months of decision-moment odds today.

WHAT THIS WRITES, beyond the input columns:
  market_prob_up_at_decision  the UP token's price at the decision moment
  prob_age_sec                how stale that price was (see below)
  resolved_outcome            UP/DOWN as the market actually settled

ON prob_age_sec: /prices-history is 1-minute fidelity, so the last print at
or before the decision moment is typically 30-60s old. That is real
information loss, not a bug -- a live bot would see the current book. Prices
older than --max-prob-age-sec are dropped rather than silently used. Treat
a backtest built on these as *approximately* what was quotable, and expect
the true decision-moment price to be somewhat closer to the eventual
outcome than what you get here.

ON resolved_outcome: these markets settle against the CHAINLINK BTC/USD
stream, NOT the Binance klines used for signals. Recording the market's own
resolution removes that mismatch from the grading; the harness prefers this
column over deriving the outcome from Binance prices (see schema.py).

Usage:
  python scripts/build_windows_from_bars.py --bars data/historical/bars.csv \
      --out data/historical/resolutions.csv
  python scripts/fetch_polymarket_history.py \
      --resolutions data/historical/resolutions.csv \
      --out data/historical/resolutions_real.csv \
      --start 2026-07-01 --end 2026-07-23
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
SLUG_PREFIX = "btc-updown-5m-"  # default; --slug-prefix overrides for other
# market families (btc-updown-15m-, eth-updown-5m-, ...). All of them key the
# slug on the window start in unix seconds, so the same fetcher works for each.


def _parse_date_ms(s: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    return ms + 86_400_000 if end_of_day else ms


def get_json(session: requests.Session, url: str, params=None, retries: int = 4):
    """Retries on 429/5xx with backoff. Returns None if it never succeeds --
    a missing window is fine, a silently wrong one is not."""
    delay = 1.0
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return None
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(delay)
            delay *= 2
            continue
        return None
    return None


def fetch_events_batch(session, window_starts_sec: list[int], slug_prefix: str = SLUG_PREFIX) -> dict[int, dict]:
    """Gamma accepts repeated ?slug= params, so event lookups batch. One
    request per ~20 windows instead of one per window."""
    params = [("slug", f"{slug_prefix}{ws}") for ws in window_starts_sec]
    data = get_json(session, f"{GAMMA_BASE}/events", params=params)
    out = {}
    if not isinstance(data, list):
        return out
    for event in data:
        slug = event.get("slug", "")
        if not slug.startswith(slug_prefix):
            continue
        try:
            ws = int(slug[len(slug_prefix):])
        except ValueError:
            continue
        markets = event.get("markets") or []
        if markets:
            out[ws] = markets[0]
    return out


def parse_market(market: dict) -> tuple[str | None, str | None]:
    """Returns (up_token_id, resolved_outcome). Both None if unusable.

    The outcome ordering is read from the market's own `outcomes` list
    rather than assumed to be index 0 -- getting this backwards inverts
    every probability in the dataset, and would look entirely plausible in
    the output."""
    try:
        outcomes = json.loads(market["outcomes"]) if isinstance(market["outcomes"], str) else market["outcomes"]
        tokens = json.loads(market["clobTokenIds"]) if isinstance(market["clobTokenIds"], str) else market["clobTokenIds"]
    except (KeyError, ValueError, TypeError):
        return None, None
    if not outcomes or not tokens or len(outcomes) != len(tokens):
        return None, None

    upper = [str(o).upper() for o in outcomes]
    if "UP" not in upper:
        return None, None
    up_token = tokens[upper.index("UP")]

    resolved = None
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            prices = None
    if prices and len(prices) == len(upper):
        try:
            floats = [float(p) for p in prices]
        except (TypeError, ValueError):
            floats = []
        # a settled binary market prices the winner at 1 and the loser at 0
        if floats and max(floats) > 0.99 and min(floats) < 0.01:
            resolved = upper[floats.index(max(floats))]
    return up_token, resolved


def fetch_decision_price(session, up_token: str, window_start_sec: int,
                         decision_sec: int, max_age_sec: int):
    """Last traded price at or before the decision moment. Returns
    (price, age_seconds) or (None, None)."""
    hist = get_json(session, f"{CLOB_BASE}/prices-history", params={
        "market": up_token,
        "startTs": window_start_sec - 300,
        "endTs": decision_sec,
        "fidelity": 1,
    })
    if not isinstance(hist, dict):
        return None, None
    points = [p for p in hist.get("history", []) if p.get("t") is not None and p["t"] <= decision_sec]
    if not points:
        return None, None
    last = max(points, key=lambda p: p["t"])
    age = decision_sec - last["t"]
    if age > max_age_sec:
        return None, None
    try:
        return float(last["p"]), age
    except (TypeError, ValueError):
        return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolutions", required=True, help="input resolutions.csv (from build_windows_from_bars.py)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", help="YYYY-MM-DD UTC, limit to windows at/after this date")
    ap.add_argument("--end", help="YYYY-MM-DD UTC, limit to windows before the end of this date")
    ap.add_argument("--entry-offset-sec", type=int, default=120,
                    help="decision point, seconds before window close (must match run_backtest's)")
    ap.add_argument("--max-prob-age-sec", type=int, default=90,
                    help="reject a decision price older than this (default 90)")
    ap.add_argument("--batch-size", type=int, default=20, help="windows per Gamma request")
    ap.add_argument("--sleep", type=float, default=0.15, help="pause between requests, be polite")
    ap.add_argument("--limit", type=int, default=None, help="stop after N windows (for a quick trial)")
    ap.add_argument("--slug-prefix", default=SLUG_PREFIX,
                    help="market family, e.g. btc-updown-15m- or eth-updown-5m- (default 5m BTC)")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.resolutions, newline="")))
    if args.start:
        lo = _parse_date_ms(args.start)
        rows = [r for r in rows if int(r["window_start_ms"]) >= lo]
    if args.end:
        hi = _parse_date_ms(args.end, end_of_day=True)
        rows = [r for r in rows if int(r["window_start_ms"]) < hi]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        raise SystemExit("no windows in range")

    print(f"Fetching real Polymarket odds + resolutions for {len(rows)} windows "
          f"({rows[0]['window_start_ms']} .. {rows[-1]['window_start_ms']})")

    session = requests.Session()
    by_start = {int(r["window_start_ms"]) // 1000: r for r in rows}
    starts = sorted(by_start)

    n_no_market = n_no_price = n_stale = n_ok = 0
    ages = []
    t0 = time.time()

    for i in range(0, len(starts), args.batch_size):
        batch = starts[i:i + args.batch_size]
        markets = fetch_events_batch(session, batch, args.slug_prefix)
        time.sleep(args.sleep)

        for ws in batch:
            row = by_start[ws]
            market = markets.get(ws)
            if market is None:
                n_no_market += 1
                continue
            up_token, resolved = parse_market(market)
            if not up_token:
                n_no_market += 1
                continue
            decision_sec = (int(row["window_end_ms"]) // 1000) - args.entry_offset_sec
            price, age = fetch_decision_price(session, up_token, ws, decision_sec, args.max_prob_age_sec)
            time.sleep(args.sleep)
            if price is None:
                n_stale += 1
                # keep the real resolution even when the price is unusable:
                # it is still better ground truth than the Binance-derived one
                if resolved:
                    row["resolved_outcome"] = resolved
                continue
            row["market_prob_up_at_decision"] = f"{price:.4f}"
            row["prob_age_sec"] = str(age)
            if resolved:
                row["resolved_outcome"] = resolved
            ages.append(age)
            n_ok += 1

        done = min(i + args.batch_size, len(starts))
        if done % (args.batch_size * 10) == 0 or done == len(starts):
            rate = done / max(1e-9, time.time() - t0)
            print(f"  {done}/{len(starts)} windows  ({n_ok} with usable odds)  "
                  f"{rate:.1f} windows/s  eta {(len(starts)-done)/max(rate,1e-9)/60:.1f} min")

    fields = ["window_start_ms", "window_end_ms", "open_price", "close_price",
              "market_prob_up_at_decision", "prob_age_sec", "resolved_outcome"]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print(f"\nWrote {len(rows)} windows -> {out_path}")
    print(f"  {n_ok} with real decision-moment odds")
    if ages:
        ages.sort()
        print(f"  price staleness: median {ages[len(ages)//2]}s, worst {ages[-1]}s "
              f"(1-minute fidelity -- a live bot would see fresher prices)")
    if n_no_market:
        print(f"  {n_no_market} window(s) had no matching market on Polymarket")
    if n_stale:
        print(f"  {n_stale} window(s) had no price within {args.max_prob_age_sec}s of the decision "
              f"point (thin/untraded market)")

    resolved_count = sum(1 for r in rows if r.get("resolved_outcome"))
    disagree = sum(1 for r in rows
                   if r.get("resolved_outcome")
                   and r["resolved_outcome"] != ("UP" if float(r["close_price"]) >= float(r["open_price"]) else "DOWN"))
    if resolved_count:
        print(f"  {resolved_count} window(s) carry the market's real resolution; "
              f"{disagree} of those ({100*disagree/resolved_count:.1f}%) DISAGREE with the "
              f"Binance-derived outcome")
        print("    (expected: these markets settle on the Chainlink BTC/USD stream, not on "
              "Binance klines.\n     The harness now grades against the real resolution -- see schema.py)")


if __name__ == "__main__":
    main()
