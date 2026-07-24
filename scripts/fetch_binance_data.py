#!/usr/bin/env python3
"""
Pulls REAL historical BTC spot klines + perpetual funding rate from
Binance's public API and writes bars.csv / funding.csv in the exact format
DataProvider expects (see README.md, "Data format").

This is the most tractable piece of real data to get -- Binance keeps deep
public history and no key is required for these endpoints. orderbook.csv
and resolutions.csv still need to come from elsewhere (see README.md and
scripts/README_next_steps.md).

NOTE: this script needs network access to api.binance.com / fapi.binance.com.
Run it from your own machine, not from a sandboxed environment that blocks
those domains (this repo's own build environment couldn't reach Binance to
test this against live data -- the request/pagination logic was verified
against Binance's documented response shapes and with mocked responses; a
first real run is still worth spot-checking, see the __main__ note below).

IMPORTANT: confirm which price feed Polymarket actually resolves 5-minute
BTC markets against before trusting a backtest built on this data. If
Polymarket uses a different index/oracle than Binance spot, and the two
occasionally diverge, that mismatch will quietly bias every result. This
gets you a reasonable, widely-used proxy series, not a guaranteed match.

Usage:
  python scripts/fetch_binance_data.py --start 2026-06-01 --end 2026-07-01 --out-dir ./data/historical
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"
FUTURES_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


def to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int, session=None, sleep_sec: float = 0.3, log=print):
    """Paginates through Binance's 1000-rows-per-request kline limit.
    Response shape per row: [open_time, open, high, low, close, volume,
    close_time, quote_vol, n_trades, taker_buy_base, taker_buy_quote, ignore]"""
    session = session or requests.Session()
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1000}
        resp = session.get(SPOT_KLINES_URL, params=params, timeout=10)
        if resp.status_code in (429, 418):
            retry_after = int(resp.headers.get("Retry-After", 5))
            log(f"  rate limited, sleeping {retry_after}s...")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        last_open_time = batch[-1][0]
        if last_open_time <= cursor:  # safety valve against an infinite loop on unexpected data
            break
        cursor = last_open_time + 1
        log(f"  {len(rows)} klines so far, at {datetime.fromtimestamp(cursor/1000, tz=timezone.utc):%Y-%m-%d %H:%M}...")
        time.sleep(sleep_sec)
    return rows


def fetch_funding(symbol: str, start_ms: int, end_ms: int, session=None, sleep_sec: float = 0.3, log=print):
    """Response shape per row: {"symbol", "fundingRate", "fundingTime", "markPrice"}"""
    session = session or requests.Session()
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        params = {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000}
        resp = session.get(FUTURES_FUNDING_URL, params=params, timeout=10)
        if resp.status_code in (429, 418):
            retry_after = int(resp.headers.get("Retry-After", 5))
            log(f"  rate limited, sleeping {retry_after}s...")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        last_time = batch[-1]["fundingTime"]
        if last_time <= cursor:
            break
        cursor = last_time + 1
        time.sleep(sleep_sec)
    return rows


def write_bars_csv(klines, out_path: Path):
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ms", "open", "high", "low", "close", "volume"])
        for k in klines:
            w.writerow([int(k[0]), k[1], k[2], k[3], k[4], k[5]])


def write_funding_csv(funding_rows, out_path: Path):
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ms", "funding_rate"])
        for r in funding_rows:
            w.writerow([int(r["fundingTime"]), r["fundingRate"]])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="BTCUSDT", help="spot symbol for klines (default BTCUSDT)")
    ap.add_argument("--funding-symbol", default="BTCUSDT", help="perpetual futures symbol for funding rate")
    ap.add_argument("--interval", default="1m", help="kline interval, e.g. 1m, 1s if available on your tier (default 1m)")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD, UTC")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD, UTC")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start_ms, end_ms = to_ms(args.start), to_ms(args.end)

    print(f"Fetching {args.symbol} {args.interval} klines, {args.start} to {args.end}...")
    klines = fetch_klines(args.symbol, args.interval, start_ms, end_ms)
    write_bars_csv(klines, out_dir / "bars.csv")
    print(f"Wrote {len(klines)} bars -> {out_dir / 'bars.csv'}")

    print(f"Fetching {args.funding_symbol} funding rate history...")
    funding = fetch_funding(args.funding_symbol, start_ms, end_ms)
    write_funding_csv(funding, out_dir / "funding.csv")
    print(f"Wrote {len(funding)} funding rows -> {out_dir / 'funding.csv'}")

    print(
        "\nStill needed before a real backtest run:\n"
        "  orderbook.csv   -- Binance's REST API doesn't expose deep historical L2 book;\n"
        "                     capture it live going forward, or use a data vendor.\n"
        "                     (orderbook_imbalance will just report 0 confidence without it --\n"
        "                     everything else in the harness still runs fine.)\n"
        "  resolutions.csv -- from Polymarket. See README.md 'Getting real data'.\n"
        "\n"
        "Sanity-check this data before trusting it: spot-check a handful of bars against\n"
        "a chart, and confirm timestamps are UTC and gapless where you expect continuity."
    )


if __name__ == "__main__":
    main()
