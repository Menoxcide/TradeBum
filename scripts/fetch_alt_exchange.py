#!/usr/bin/env python3
"""
Fetches 1-minute BTC bars from exchanges OTHER than Binance, so the
settlement feed can be approximated by a cross-venue consensus instead of a
single venue.

Why this exists: the Polymarket 5-minute BTC markets settle on the
Chainlink BTC/USD stream, and the market description is explicit that it is
"not according to other sources or spot markets". Chainlink's stream is an
aggregate across venues. This harness's signals read Binance. So a window
where Binance disagrees with the rest of the market is a window where the
Binance-implied outcome and the actual settlement can diverge -- and they
do, on 3.4% of windows.

That is only exploitable if the divergence is visible BEFORE resolution,
which is the point of pulling independent venues: a cross-venue median
computed at the decision moment is a better proxy for what Chainlink will
print than Binance alone.

Supported: okx, coinbase, kraken. All were reachable from networks where
api.binance.com returns 451.

Usage:
  python scripts/fetch_alt_exchange.py --venue okx --start 2026-06-24 --end 2026-07-23 \
      --out ./data/alt/okx_bars.csv
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


def to_ms(date_str: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt += timedelta(days=1)
    return int(dt.timestamp() * 1000)


def fetch_okx(start_ms: int, end_ms: int, inst: str = "BTC-USDT", log=print):
    """OKX history-candles pages BACKWARDS from `after` (exclusive), 100 rows
    per call. Rows: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]."""
    url = "https://www.okx.com/api/v5/market/history-candles"
    session = requests.Session()
    out = {}
    cursor = end_ms
    while cursor > start_ms:
        r = session.get(url, params={"instId": inst, "bar": "1m",
                                     "after": cursor, "limit": 100}, timeout=20)
        if r.status_code != 200:
            time.sleep(1.0)
            continue
        data = r.json().get("data") or []
        if not data:
            break
        for row in data:
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                out[ts] = (row[1], row[2], row[3], row[4], row[5])
        oldest = min(int(row[0]) for row in data)
        if oldest >= cursor:
            break
        cursor = oldest
        if len(out) % 5000 < 100:
            log(f"  okx: {len(out)} bars, at {datetime.fromtimestamp(cursor/1000, tz=timezone.utc):%Y-%m-%d %H:%M}")
        time.sleep(0.12)
    return out


def fetch_coinbase(start_ms: int, end_ms: int, product: str = "BTC-USD", log=print):
    """Coinbase Exchange candles: max 300 per request, granularity in seconds.
    Rows: [time_s, low, high, open, close, volume] -- note the ordering is
    NOT OHLC, getting it wrong silently swaps open and low."""
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    session = requests.Session()
    out = {}
    step = 300 * 60  # 300 one-minute candles per request, in seconds
    cur = start_ms // 1000
    end_s = end_ms // 1000
    while cur < end_s:
        hi = min(cur + step, end_s)
        r = session.get(url, params={
            "granularity": 60,
            "start": datetime.fromtimestamp(cur, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(hi, tz=timezone.utc).isoformat(),
        }, timeout=20, headers={"User-Agent": "btc5m-backtest/1.0"})
        if r.status_code == 429:
            time.sleep(1.0)
            continue
        if r.status_code != 200:
            cur = hi
            continue
        for row in r.json() or []:
            ts = int(row[0]) * 1000
            if start_ms <= ts < end_ms:
                low, high, op, close, vol = row[1], row[2], row[3], row[4], row[5]
                out[ts] = (op, high, low, close, vol)
        cur = hi
        if len(out) % 5000 < 300:
            log(f"  coinbase: {len(out)} bars")
        time.sleep(0.2)
    return out


def fetch_kraken(start_ms: int, end_ms: int, pair: str = "XBTUSD", log=print):
    """Kraken OHLC only serves ~720 recent bars regardless of `since`, so this
    is useful for a live cross-check, not for deep history."""
    r = requests.get("https://api.kraken.com/0/public/OHLC",
                     params={"pair": pair, "interval": 1}, timeout=20)
    r.raise_for_status()
    result = r.json().get("result", {})
    series = next((v for k, v in result.items() if k != "last"), [])
    out = {}
    for row in series:
        ts = int(row[0]) * 1000
        if start_ms <= ts < end_ms:
            out[ts] = (row[1], row[2], row[3], row[4], row[6])
    log(f"  kraken: {len(out)} bars (endpoint caps at ~720 recent bars)")
    return out


FETCHERS = {"okx": fetch_okx, "coinbase": fetch_coinbase, "kraken": fetch_kraken}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", required=True, choices=sorted(FETCHERS))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start_ms, end_ms = to_ms(args.start), to_ms(args.end, end_of_day=True)
    print(f"Fetching {args.venue} 1m bars {args.start}..{args.end}")
    bars = FETCHERS[args.venue](start_ms, end_ms)
    if not bars:
        raise SystemExit("no bars returned")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ms", "open", "high", "low", "close", "volume"])
        for ts in sorted(bars):
            o, h, l, c, v = bars[ts]
            w.writerow([ts, o, h, l, c, v])
    print(f"Wrote {len(bars)} bars -> {out_path}")
    expected = (end_ms - start_ms) // 60000
    print(f"  coverage: {100*len(bars)/expected:.1f}% of {expected} expected minutes")


if __name__ == "__main__":
    main()
