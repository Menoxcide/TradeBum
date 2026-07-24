#!/usr/bin/env python3
"""
Prospective data collector. This is the piece that unblocks the test that
actually matters (README step 4): real Polymarket odds at the decision
moment, plus the BTC L2 order book. Neither is meaningfully available as
deep free history, so both have to be captured going forward -- meaning
every day this isn't running is a day of data you won't have later.

TWO OUTPUTS PER SNAPSHOT, DELIBERATELY REDUNDANT:

  raw/<date>.jsonl   every API response, unparsed, exactly as received
  orderbook.csv      parsed, in DataProvider's format
  odds_log.csv       parsed decision-moment odds (feeds build_resolutions.py)

The raw JSONL is not redundancy for its own sake. This script could not be
tested against the live Polymarket or Binance APIs from the environment it
was written in (network-restricted), and API response shapes drift. If the
parsing here is subtly wrong, the parsed CSVs will be quietly garbage --
but the raw JSONL will still contain everything, so you can re-parse
historical captures instead of discovering weeks later that the data is
unrecoverable. Verify the first few snapshots by hand before walking away
from this.

ENDPOINTS USED (Polymarket CLOB, public/no-auth read endpoints):
  GET https://clob.polymarket.com/midpoint?token_id=...   -> {"mid": "0.52"}
  GET https://clob.polymarket.com/book?token_id=...       -> {bids:[{price,size}], asks:[...]}
  GET https://clob.polymarket.com/spread?token_id=...
  GET https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=10

Usage:
  # 1. find the token id for the current BTC 5m market (prints candidates)
  python scripts/collect_live.py discover --query "bitcoin"

  # 2. run the collector (leave it running; ctrl-C to stop)
  python scripts/collect_live.py run --token-id <UP_TOKEN_ID> --out-dir ./data/live

Note on --token-id: Polymarket's short-dated BTC markets roll over, so a
token id is not permanent. For a long run you'll want the rediscovery loop
(--rediscover-every-min), which re-queries Gamma for the current market
rather than holding a stale id. Confirm the discovered market really is the
5-minute up/down market you intend to trade, and that the token you pass is
the UP/YES side -- passing the DOWN side silently inverts every probability
you collect.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"
BINANCE_DEPTH = "https://api.binance.com/api/v3/depth"


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- discovery


def discover(query: str, session=None, log=print):
    """Queries Gamma for markets matching `query` and prints candidates with
    their token ids. Deliberately does NOT guess a slug pattern -- you
    confirm which market is the right one and pass its id explicitly."""
    session = session or requests.Session()
    resp = session.get(f"{GAMMA_BASE}/markets", params={"closed": "false", "limit": 100}, timeout=15)
    resp.raise_for_status()
    markets = resp.json()

    q = query.lower()
    hits = []
    for m in markets:
        blob = json.dumps(m).lower()
        if q in blob:
            hits.append(m)

    if not hits:
        log(f"No open markets matched {query!r}. Try a broader --query, or browse "
            f"{GAMMA_BASE}/markets?closed=false directly.")
        return []

    log(f"{len(hits)} open market(s) matched {query!r}:\n")
    for m in hits[:25]:
        question = m.get("question") or m.get("title") or "(no question field)"
        slug = m.get("slug", "")
        end = m.get("endDate") or m.get("end_date_iso") or ""
        raw_tokens = m.get("clobTokenIds") or m.get("clob_token_ids") or ""
        if isinstance(raw_tokens, str):
            try:
                raw_tokens = json.loads(raw_tokens)
            except (ValueError, TypeError):
                raw_tokens = [raw_tokens]
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except (ValueError, TypeError):
                outcomes = None

        log(f"  {question}")
        log(f"    slug: {slug}   ends: {end}")
        if outcomes and raw_tokens and len(outcomes) == len(raw_tokens):
            for name, tid in zip(outcomes, raw_tokens):
                log(f"    outcome {name!r}: token_id={tid}")
        else:
            log(f"    token ids: {raw_tokens}   outcomes: {outcomes}")
        log("")

    log("Pass the token id for the UP/YES outcome to `run --token-id`.")
    return hits


# ---------------------------------------------------------------- snapshots


def snapshot_polymarket(token_id: str, session=None):
    """Returns (parsed_dict, raw_dict). Never raises on a single bad
    endpoint -- partial data plus an error note beats losing the window."""
    session = session or requests.Session()
    raw = {}
    parsed = {"token_id": token_id}

    for name, url, params in (
        ("midpoint", f"{CLOB_BASE}/midpoint", {"token_id": token_id}),
        ("book", f"{CLOB_BASE}/book", {"token_id": token_id}),
        ("spread", f"{CLOB_BASE}/spread", {"token_id": token_id}),
    ):
        try:
            r = session.get(url, params=params, timeout=8)
            r.raise_for_status()
            raw[name] = r.json()
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            raw[name] = {"_error": f"{type(e).__name__}: {e}"}

    mid = raw.get("midpoint", {})
    if isinstance(mid, dict) and "mid" in mid:
        try:
            parsed["market_prob_up"] = float(mid["mid"])
        except (TypeError, ValueError):
            parsed["market_prob_up"] = None
    else:
        parsed["market_prob_up"] = None

    sp = raw.get("spread", {})
    if isinstance(sp, dict):
        for key in ("spread", "value"):
            if key in sp:
                try:
                    parsed["spread"] = float(sp[key])
                except (TypeError, ValueError):
                    parsed["spread"] = None
                break

    book = raw.get("book", {})
    if isinstance(book, dict):
        parsed["pm_best_bid"] = _best(book.get("bids"), want_max=True)
        parsed["pm_best_ask"] = _best(book.get("asks"), want_max=False)

    return parsed, raw


def _best(levels, want_max: bool):
    if not isinstance(levels, list) or not levels:
        return None
    prices = []
    for lvl in levels:
        try:
            prices.append(float(lvl["price"] if isinstance(lvl, dict) else lvl[0]))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    if not prices:
        return None
    return max(prices) if want_max else min(prices)


def snapshot_binance_depth(symbol: str = "BTCUSDT", limit: int = 10, session=None):
    session = session or requests.Session()
    try:
        r = session.get(BINANCE_DEPTH, params={"symbol": symbol, "limit": limit}, timeout=8)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:  # noqa: BLE001
        return None, {"_error": f"{type(e).__name__}: {e}"}

    def split(levels):
        prices, sizes = [], []
        for lvl in levels or []:
            try:
                prices.append(float(lvl[0]))
                sizes.append(float(lvl[1]))
            except (TypeError, ValueError, IndexError):
                continue
        return prices, sizes

    bid_p, bid_s = split(raw.get("bids"))
    ask_p, ask_s = split(raw.get("asks"))
    parsed = {"bid_prices": bid_p, "bid_sizes": bid_s, "ask_prices": ask_p, "ask_sizes": ask_s}
    return parsed, raw


# ---------------------------------------------------------------- writers


def append_csv(path: Path, header: list[str], row: list):
    exists = path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(header)
        w.writerow(row)


def append_jsonl(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def record_snapshot(out_dir: Path, ts_ms: int, window_start_ms: int, window_end_ms: int,
                    pm_parsed: dict, pm_raw: dict, depth_parsed, depth_raw):
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    append_jsonl(out_dir / "raw" / f"{day}.jsonl", {
        "timestamp_ms": ts_ms,
        "window_start_ms": window_start_ms,
        "window_end_ms": window_end_ms,
        "polymarket": pm_raw,
        "binance_depth": depth_raw,
    })

    append_csv(
        out_dir / "odds_log.csv",
        ["timestamp_ms", "window_start_ms", "window_end_ms", "token_id",
         "market_prob_up", "spread", "pm_best_bid", "pm_best_ask"],
        [ts_ms, window_start_ms, window_end_ms, pm_parsed.get("token_id"),
         pm_parsed.get("market_prob_up"), pm_parsed.get("spread"),
         pm_parsed.get("pm_best_bid"), pm_parsed.get("pm_best_ask")],
    )

    if depth_parsed:
        append_csv(
            out_dir / "orderbook.csv",
            ["timestamp_ms", "bid_prices", "bid_sizes", "ask_prices", "ask_sizes"],
            [ts_ms,
             ";".join(map(str, depth_parsed["bid_prices"])),
             ";".join(map(str, depth_parsed["bid_sizes"])),
             ";".join(map(str, depth_parsed["ask_prices"])),
             ";".join(map(str, depth_parsed["ask_sizes"]))],
        )


# ---------------------------------------------------------------- main loop


def next_window_bounds(ts_ms: int, window_sec: int = 300):
    """5-minute windows aligned to the wall clock. Confirm this alignment
    matches how Polymarket actually bounds its windows before relying on it."""
    w = window_sec * 1000
    start = (ts_ms // w) * w
    return start, start + w


def run(token_id: str, out_dir: Path, entry_offset_sec: int = 120,
        window_sec: int = 300, symbol: str = "BTCUSDT", max_snapshots: int | None = None,
        session=None, sleep_fn=time.sleep, clock=now_ms, log=print):
    session = session or requests.Session()
    taken = 0
    log(f"Collecting. Snapshot at T-{entry_offset_sec}s of each {window_sec}s window.")
    log(f"Writing to {out_dir}  (raw JSONL + parsed CSVs)\n")

    while max_snapshots is None or taken < max_snapshots:
        ts = clock()
        w_start, w_end = next_window_bounds(ts, window_sec)
        target = w_end - entry_offset_sec * 1000

        if ts > target:  # already past this window's decision point
            w_start, w_end = w_end, w_end + window_sec * 1000
            target = w_end - entry_offset_sec * 1000

        wait_sec = (target - clock()) / 1000
        if wait_sec > 0:
            sleep_fn(wait_sec)

        snap_ts = clock()
        pm_parsed, pm_raw = snapshot_polymarket(token_id, session=session)
        depth_parsed, depth_raw = snapshot_binance_depth(symbol, session=session)
        record_snapshot(out_dir, snap_ts, w_start, w_end, pm_parsed, pm_raw, depth_parsed, depth_raw)

        taken += 1
        prob = pm_parsed.get("market_prob_up")
        prob_str = f"{prob:.3f}" if isinstance(prob, float) else "MISSING"
        depth_str = "ok" if depth_parsed else "MISSING"
        log(f"[{iso(snap_ts)}] window {iso(w_start)}-{iso(w_end)}  prob_up={prob_str}  depth={depth_str}")

        if prob is None or depth_parsed is None:
            log("    ^ something came back empty -- check raw/*.jsonl for the actual response "
                "before letting this run unattended.")

        sleep_fn(1.0)

    return taken


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="find candidate markets + token ids")
    d.add_argument("--query", default="bitcoin")

    r = sub.add_parser("run", help="collect snapshots continuously")
    r.add_argument("--token-id", required=True, help="CLOB token id for the UP/YES outcome")
    r.add_argument("--out-dir", required=True)
    r.add_argument("--entry-offset-sec", type=int, default=120,
                   help="snapshot this many seconds before window close (match your backtest's entry_offset_sec)")
    r.add_argument("--window-sec", type=int, default=300)
    r.add_argument("--symbol", default="BTCUSDT")
    r.add_argument("--max-snapshots", type=int, default=None)

    args = ap.parse_args()
    if args.cmd == "discover":
        discover(args.query)
    else:
        try:
            run(args.token_id, Path(args.out_dir), args.entry_offset_sec,
                args.window_sec, args.symbol, args.max_snapshots)
        except KeyboardInterrupt:
            print("\nStopped. Collected data is already on disk (appends, not buffered).")
            sys.exit(0)


if __name__ == "__main__":
    main()
