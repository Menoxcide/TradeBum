#!/usr/bin/env python3
"""
Fetches REAL historical BTC 1m klines + perpetual funding rate from Binance's
public data-dump host (data.binance.vision) and writes bars.csv / funding.csv
in the format DataProvider expects (see README.md, "Data format").

Why this exists alongside fetch_binance_data.py: the REST API
(api.binance.com) returns HTTP 451 from a lot of networks, including some
cloud regions -- the whole endpoint is geo-blocked, not rate-limited, so no
amount of retrying helps. The data-dump host serves the same underlying
klines as static zips and is not blocked in the same way. It's also faster
for bulk history: one request per month instead of one per 1000 bars.

Two data quirks this handles, both of which corrupt results silently if you
don't:

  1. TIMESTAMP UNITS. Binance switched the dump files to MICROSECOND
     timestamps (16 digits) in 2025; older files are milliseconds (13
     digits). Everything downstream assumes milliseconds. Loading a
     microsecond file as-is puts every bar ~50,000 years in the future,
     which the harness would not necessarily crash on -- it would just
     silently match nothing. Units are detected per row by magnitude.
  2. OPTIONAL CSV HEADER. Some dump files carry a header row, some don't.
     Detected, not assumed.

Downloads are checksum-verified against the .CHECKSUM sidecar when one
exists, and cached on disk so re-runs don't re-download.

Usage:
  python scripts/fetch_binance_vision.py --start 2026-04-01 --end 2026-07-24 --out-dir ./data/historical
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://data.binance.vision/data"

# A millisecond timestamp for any date this century is 13 digits; a
# microsecond one is 16. Anything at/above this bound is microseconds.
US_THRESHOLD = 10**14


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()


def _to_ms(ts: int) -> int:
    """Normalize a Binance dump timestamp to milliseconds. See quirk 1."""
    return ts // 1000 if ts >= US_THRESHOLD else ts


def _looks_like_header(row: list[str]) -> bool:
    try:
        float(row[0])
        return False
    except (ValueError, IndexError):
        return True


def download(url: str, cache_dir: Path, session: requests.Session, log=print) -> bytes | None:
    """Returns the zip bytes, or None if the file doesn't exist (404).
    Verifies sha256 against the .CHECKSUM sidecar when Binance publishes one."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / url.rsplit("/", 1)[-1]
    if cached.exists():
        return cached.read_bytes()

    resp = session.get(url, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    blob = resp.content

    checksum_resp = session.get(url + ".CHECKSUM", timeout=30)
    if checksum_resp.status_code == 200:
        expected = checksum_resp.text.split()[0].strip()
        actual = hashlib.sha256(blob).hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"checksum mismatch for {url}: expected {expected}, got {actual}. "
                "Refusing to write data that may be truncated or corrupted."
            )
    else:
        log(f"  (no checksum published for {cached.name} -- not verified)")

    cached.write_bytes(blob)
    return blob


def read_zip_rows(blob: bytes) -> list[list[str]]:
    """Every dump zip holds exactly one CSV. Strips the header if present."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            rows = list(csv.reader(io.TextIOWrapper(fh, encoding="utf-8")))
    if rows and _looks_like_header(rows[0]):
        rows = rows[1:]
    return rows


def month_starts(start: date, end: date):
    """Month boundaries fully covered by [start, end]. A month is only
    'complete' -- and so only available as a monthly dump -- if the range
    spans its first day through its last."""
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        next_month = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
        last_day = next_month - timedelta(days=1)
        if cursor >= start and last_day <= end:
            yield cursor
        cursor = next_month


def days_not_covered(start: date, end: date, covered_months: list[date]):
    covered = set()
    for m in covered_months:
        next_month = date(m.year + (m.month == 12), (m.month % 12) + 1, 1)
        d = m
        while d < next_month:
            covered.add(d)
            d += timedelta(days=1)
    d = start
    while d <= end:
        if d not in covered:
            yield d
        d += timedelta(days=1)


def fetch_klines(symbol: str, interval: str, start: date, end: date, cache_dir: Path,
                 session: requests.Session, log=print) -> list[list[str]]:
    """Monthly dumps where a whole month is in range, daily dumps for the
    ragged edges (the current month has no monthly dump until it ends)."""
    rows: list[list[str]] = []
    months = list(month_starts(start, end))

    for m in months:
        url = f"{BASE}/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{m:%Y-%m}.zip"
        blob = download(url, cache_dir, session, log)
        if blob is None:
            log(f"  no monthly dump for {m:%Y-%m} -- will try daily files")
            continue
        batch = read_zip_rows(blob)
        rows.extend(batch)
        log(f"  {m:%Y-%m}: {len(batch)} bars")

    fetched_months = set()
    for m in months:
        url = f"{BASE}/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{m:%Y-%m}.zip"
        if (cache_dir / url.rsplit("/", 1)[-1]).exists():
            fetched_months.add(m)

    for d in days_not_covered(start, end, sorted(fetched_months)):
        url = f"{BASE}/spot/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{d:%Y-%m-%d}.zip"
        blob = download(url, cache_dir, session, log)
        if blob is None:
            log(f"  no daily dump for {d} (not published yet? today's file appears after UTC midnight)")
            continue
        batch = read_zip_rows(blob)
        rows.extend(batch)
        log(f"  {d}: {len(batch)} bars")

    return rows


def fetch_funding(symbol: str, start: date, end: date, cache_dir: Path,
                  session: requests.Session, log=print) -> list[list[str]]:
    """Funding-rate dumps: calc_time,funding_interval_hours,last_funding_rate."""
    rows: list[list[str]] = []
    for m in month_starts(start, end):
        url = f"{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m:%Y-%m}.zip"
        blob = download(url, cache_dir, session, log)
        if blob is None:
            continue
        rows.extend(read_zip_rows(blob))
    return rows


def write_bars_csv(kline_rows, out_path: Path, start_ms: int, end_ms: int) -> int:
    """Dedupes on open time (monthly and daily dumps can overlap) and clips
    to the requested range, since a monthly file covers the whole month."""
    seen = set()
    out_rows = []
    for k in kline_rows:
        ts = _to_ms(int(k[0]))
        if ts < start_ms or ts >= end_ms or ts in seen:
            continue
        seen.add(ts)
        out_rows.append([ts, k[1], k[2], k[3], k[4], k[5]])
    out_rows.sort()
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ms", "open", "high", "low", "close", "volume"])
        w.writerows(out_rows)
    return len(out_rows)


def write_funding_csv(funding_rows, out_path: Path, start_ms: int, end_ms: int) -> int:
    seen = set()
    out_rows = []
    for r in funding_rows:
        ts = _to_ms(int(r[0]))
        if ts < start_ms or ts >= end_ms or ts in seen:
            continue
        seen.add(ts)
        out_rows.append([ts, r[2]])
    out_rows.sort()
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ms", "funding_rate"])
        w.writerows(out_rows)
    return len(out_rows)


def report_gaps(bars_path: Path, expected_interval_ms: int, log=print) -> None:
    """Bars are the ground truth every window's open/close comes from. A gap
    means windows built over it are wrong, not just missing -- worth seeing
    before you trust any backtest built on this file."""
    ts = []
    with open(bars_path, newline="") as f:
        for row in csv.DictReader(f):
            ts.append(int(row["timestamp_ms"]))
    gaps = [(a, b) for a, b in zip(ts, ts[1:]) if b - a != expected_interval_ms]
    if not gaps:
        log("  no gaps: bar spacing is uniform across the whole range")
        return
    missing = sum((b - a) // expected_interval_ms - 1 for a, b in gaps)
    log(f"  WARNING: {len(gaps)} gap(s) in bar coverage, ~{missing} missing bar(s)")
    for a, b in gaps[:5]:
        log(f"    {datetime.fromtimestamp(a/1000, tz=timezone.utc):%Y-%m-%d %H:%M} -> "
            f"{datetime.fromtimestamp(b/1000, tz=timezone.utc):%Y-%m-%d %H:%M}")
    if len(gaps) > 5:
        log(f"    ... and {len(gaps) - 5} more")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--funding-symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1m", help="kline interval (default 1m)")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD, UTC, inclusive")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD, UTC, inclusive")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--cache-dir", default=None, help="where to keep downloaded zips (default <out-dir>/../cache)")
    args = ap.parse_args()

    start, end = _parse_date(args.start), _parse_date(args.end)
    if end < start:
        raise SystemExit("--end is before --start")
    start_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int((datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
                  + timedelta(days=1)).timestamp() * 1000)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir) if args.cache_dir else out_dir.parent / "cache"

    session = requests.Session()

    print(f"Fetching {args.symbol} {args.interval} klines, {start} to {end} (UTC)...")
    klines = fetch_klines(args.symbol, args.interval, start, end, cache_dir, session)
    if not klines:
        raise SystemExit("No klines downloaded -- check the symbol/interval and date range.")
    n_bars = write_bars_csv(klines, out_dir / "bars.csv", start_ms, end_ms)
    print(f"Wrote {n_bars} bars -> {out_dir / 'bars.csv'}")

    interval_ms = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "1h": 3_600_000}.get(args.interval)
    if interval_ms:
        report_gaps(out_dir / "bars.csv", interval_ms)

    print(f"Fetching {args.funding_symbol} funding rate history...")
    funding = fetch_funding(args.funding_symbol, start, end, cache_dir, session)
    n_funding = write_funding_csv(funding, out_dir / "funding.csv", start_ms, end_ms)
    print(f"Wrote {n_funding} funding rows -> {out_dir / 'funding.csv'}")

    print(
        "\nStill needed for a full backtest:\n"
        "  resolutions.csv -- build 5-minute windows straight from these bars with\n"
        "                     scripts/build_windows_from_bars.py (no Polymarket data\n"
        "                     required; prices trades at geometry-implied fair value),\n"
        "                     or from real captured odds with scripts/build_resolutions.py.\n"
        "  orderbook.csv   -- not available historically at depth; capture it going\n"
        "                     forward with scripts/collect_live.py. Without it,\n"
        "                     orderbook_imbalance reports 0 confidence and everything\n"
        "                     else still runs.\n"
        "\nSanity-check before trusting: spot-check a few bars against a chart, and\n"
        "confirm which price feed Polymarket actually resolves against -- Binance spot\n"
        "is a widely-used proxy, not a guaranteed match."
    )


if __name__ == "__main__":
    main()
