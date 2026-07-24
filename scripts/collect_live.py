#!/usr/bin/env python3
"""
Prospective data collector for the BTC L2 order book, plus live Polymarket
odds.

SCOPE CHANGED -- READ THIS FIRST. This script was written on the assumption
that neither Polymarket odds nor BTC depth was available as free history,
so both had to be captured going forward. Testing against the live API
showed that is only half true:

  * Polymarket odds: available historically after all. The 5-minute markets
    use a deterministic event slug (`btc-updown-5m-<window_start_unix>`) and
    CLOB /prices-history serves closed markets at 1-minute fidelity. Use
    scripts/fetch_polymarket_history.py -- it reconstructs months of
    decision-moment odds in minutes, with the market's real resolution
    attached. You do not need to wait weeks for this data.
  * BTC L2 depth: still genuinely prospective-only. Nothing free serves
    historical order-book depth, so this collector remains the way to get
    `orderbook.csv`, and every day it isn't running is a day you can't get
    back.

So: run this for the order book (and as a live cross-check on the
historical odds), not because the odds themselves are unobtainable.

TWO OUTPUTS PER SNAPSHOT, DELIBERATELY REDUNDANT:

  raw/<date>.jsonl   every API response, unparsed, exactly as received
  orderbook.csv      parsed, in DataProvider's format
  odds_log.csv       parsed decision-moment odds (feeds build_resolutions.py)

The raw JSONL is not redundancy for its own sake: if the parsing here is
ever subtly wrong, the parsed CSVs are quietly garbage, while the raw
captures stay re-parseable. Keep it.

VERIFIED AGAINST THE LIVE API. The response shapes below were confirmed
directly, and the parsing in this file matches them: midpoint returns
{"mid": "0.535"}, spread returns {"spread": "0.01"}, and book returns
levels as {"price": "0.73", "size": "..."} with an `asset_id` echoing the
token you asked for. UP and DOWN midpoints summed to 1.000. Two real bugs
were found and fixed in the process -- see `discover()` (fetched one
unpaginated page and so found nothing) and `snapshot_binance_depth()`
(Binance answers 451 on many networks, silently yielding an empty book).
Still hand-check your first few snapshots; shapes drift over time.

ENDPOINTS USED (all public/no-auth reads):
  GET https://clob.polymarket.com/midpoint?token_id=...   -> {"mid": "0.535"}
  GET https://clob.polymarket.com/book?token_id=...       -> {bids:[{price,size}], asks:[...]}
  GET https://clob.polymarket.com/spread?token_id=...     -> {"spread": "0.01"}
  GET https://gamma-api.polymarket.com/events?slug=btc-updown-5m-<unix>
  GET https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=10
      (falls back to OKX /api/v5/market/books when Binance returns 451)

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
# fallback venue: api.binance.com is geo-blocked (451) on many networks
OKX_DEPTH = "https://www.okx.com/api/v5/market/books"


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- discovery


def current_window_slug(ts_ms: int | None = None, window_seconds: int = 300) -> str:
    """The 5-minute BTC markets use a deterministic event slug keyed to the
    window's start in unix seconds -- verified against the live API, e.g.
    `btc-updown-5m-1781999400` is the 23:50-23:55 UTC window on 2026-06-20.
    That makes discovery exact instead of a search over open markets."""
    ts = (ts_ms if ts_ms is not None else now_ms()) // 1000
    return f"btc-updown-5m-{ts - (ts % window_seconds)}"


def resolve_current_market(session=None, window_seconds: int = 300, log=print):
    """Returns (up_token_id, market_dict) for the window happening right now,
    or (None, None). Use this instead of pinning a --token-id: these markets
    roll over every 5 minutes, so any fixed id goes stale almost immediately."""
    session = session or requests.Session()
    slug = current_window_slug(window_seconds=window_seconds)
    resp = session.get(f"{GAMMA_BASE}/events", params={"slug": slug}, timeout=15)
    if resp.status_code != 200:
        return None, None
    events = resp.json()
    if not events or not events[0].get("markets"):
        log(f"  no open market for {slug} yet")
        return None, None
    market = events[0]["markets"][0]

    outcomes = market.get("outcomes")
    tokens = market.get("clobTokenIds")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(tokens, str):
        tokens = json.loads(tokens)
    if not outcomes or not tokens or len(outcomes) != len(tokens):
        return None, market
    upper = [str(o).upper() for o in outcomes]
    if "UP" not in upper:
        return None, market
    # index by name, never by position -- picking the wrong token inverts
    # every probability collected and still looks perfectly plausible
    return tokens[upper.index("UP")], market


def discover(query: str, session=None, log=print, max_pages: int = 20):
    """Queries Gamma for markets matching `query` and prints candidates with
    their token ids.

    Two fixes after testing this against the live API: it used to request a
    single page of 100 open markets and filter client-side, which found
    nothing useful because Polymarket has thousands of open markets and the
    5-minute BTC ones were never on page 1. Gamma caps `limit` at 100
    regardless of what you ask for, so this paginates with `offset`, and
    prefers the `/public-search` endpoint, which actually searches."""
    session = session or requests.Session()
    q = query.lower()
    hits = []

    resp = session.get(f"{GAMMA_BASE}/public-search",
                       params={"q": query, "limit_per_type": 20}, timeout=20)
    if resp.status_code == 200:
        for event in (resp.json() or {}).get("events", []):
            hits.extend(event.get("markets") or [])

    if not hits:
        for page in range(max_pages):
            resp = session.get(f"{GAMMA_BASE}/markets",
                               params={"closed": "false", "limit": 100, "offset": page * 100},
                               timeout=20)
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            hits.extend(m for m in batch if q in json.dumps(m).lower())

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
    """BTC L2 depth, from Binance if reachable and OKX otherwise.

    The fallback is not belt-and-braces: api.binance.com answers HTTP 451
    from many networks (including several cloud regions), and 451 is a hard
    geo-block, not a transient error -- retrying never succeeds. Without a
    fallback this returns an error dict on every single snapshot and the
    collector quietly produces an orderbook.csv with no order book in it,
    which you would not notice until a backtest weeks later showed
    orderbook_imbalance at zero confidence throughout.

    OKX's BTC-USDT book is a different venue with different depth, so the
    `venue` field is recorded per snapshot -- do not pool the two without
    accounting for that."""
    session = session or requests.Session()
    raw = None
    venue = None
    errors = {}

    for name, url, params, unwrap in (
        ("binance", BINANCE_DEPTH, {"symbol": symbol, "limit": limit}, lambda d: d),
        ("okx", OKX_DEPTH, {"instId": "BTC-USDT", "sz": limit},
         lambda d: (d.get("data") or [{}])[0]),
    ):
        try:
            r = session.get(url, params=params, timeout=8)
            r.raise_for_status()
            raw = unwrap(r.json())
            venue = name
            break
        except Exception as e:  # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {e}"

    if raw is None:
        return None, {"_error": errors}

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
    parsed = {"bid_prices": bid_p, "bid_sizes": bid_s,
              "ask_prices": ask_p, "ask_sizes": ask_s, "venue": venue}
    return parsed, {"venue": venue, "response": raw, "_errors": errors or None}


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
