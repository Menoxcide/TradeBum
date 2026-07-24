# BTC 5m Polymarket — Offline Backtest Harness

This is Phase 0 from the scaffolding-plan review: a way to find out whether
the confluence-scored momentum strategy has real edge *before* any of it
touches `EXECUTE=true`. It replays historical data through the actual
signal/sizing/risk code (not a simplified stand-in) and reports statistics
with confidence intervals, not just point estimates.

**It ships with no real market data.** Nothing here has been validated
against real BTC or Polymarket history yet — that's the next step, not
something this harness can shortcut. See "Getting real data" below.

## Next steps, in order

1. **Run `scripts/fetch_binance_data.py`** (needs network access this build
   environment didn't have — run it from your own machine). Gets you real
   `bars.csv` + `funding.csv` today; Binance keeps deep public history and
   no key is required.
2. **Run the 3-way comparison on that real BTC data immediately** — you
   don't need Polymarket data yet. As of this build, when
   `market_prob_up_at_decision` is missing, the engine prices trades at the
   geometry-implied fair value estimated from real BTC volatility
   (`fair_value.py`) instead of a flat 50%. That alone answers the first
   real question: **does momentum on real BTC data beat pure random-walk
   math**, using nothing but free data. If `naive_momentum`'s mean-pnl CI
   doesn't clear zero here, that's an important, cheap, early signal —
   don't move on to step 4 yet if so.
3. **Start `scripts/collect_live.py` now, in parallel** — don't wait for
   step 2's result. Neither real Polymarket odds nor BTC L2 depth is
   available as deep free history, so both have to be captured going
   forward; every day it isn't running is a day of data you can't get back
   later. A few weeks of real 5-minute windows is enough to start.
   Then `scripts/build_resolutions.py` merges that capture with your
   Binance bars into `resolutions.csv`.
4. **Once real Polymarket odds exist**, rerun the comparison with real
   `market_prob_up_at_decision` filled in. This is the test that actually
   matters: does the edge (if step 2 found any) survive being priced
   against Polymarket's *actual* odds and fees, not just against theory.
5. **Only if step 4 clears friction with a CI that excludes zero**: fit
   `confluence_score_to_model_prob` against the labeled outcomes you now
   have (logistic regression of win/loss on the composite score, or better,
   on the raw features directly), and fit `signal_weights` the same way
   instead of using the doc's hand-set numbers.

## Quickstart

```bash
pip install -r requirements.txt

# smoke test on synthetic (fake) data -- proves the code runs, tells you
# NOTHING about whether the real strategy works
python scripts/run_backtest.py --synthetic --n-windows 8000 --profile conservative

# once you have real CSVs (see "Data format" below):
python scripts/run_backtest.py --data-dir ./data/historical --profile conservative --sensitivity --output report.json

# run the test suite (includes a self-check that the harness doesn't fool itself):
python tests/test_smoke.py
```

Every run reports **three strategies side by side** on the same data:
`coinflip` (random direction), `naive_momentum` (momentum direction, no
filtering), and `confluence` (the full scored/gated/Kelly-sized pipeline).
That's deliberate — it's the only way to tell whether the added machinery
is earning its complexity, versus just adding moving parts to noise.

## The most important thing this harness found (read this first)

Building this surfaced a real bug, and fixing it surfaced a real lesson —
worth understanding before you look at any numbers this produces.

**The bug:** the first synthetic run showed the confluence strategy winning
91-99% of the time on *pure random walk data with no edge in it by
construction*. That's impossible without a bug. Cause: the fake market odds
were generated as flat noise around 50%, ignoring the price move that had
already happened by decision time. Real markets don't do that.

**Why that matters — window geometry:** if BTC is up $70 with 2 minutes
left in a 5-minute window, a *driftless random walk* is already likely to
still be up at the close, simply because there's less time left for the
remaining randomness to erase the move — not because of any real
predictability. This harness computes that exact probability
(`src/backtest/fair_value.py`, `fair_value_prob_up()`, via the reflection
principle: `Φ(move / (σ√τ))`). Once the synthetic market was repriced to
reflect that fair value instead of flat noise, here's what the same
strategies showed on 8,000 fake windows:

| strategy | win rate | mean pnl/trade | 95% CI on mean pnl |
|---|---|---|---|
| coinflip | 51.4% | $0.67 | -$4.81 to $9.07 |
| naive_momentum | **97.1%** | $0.10 | -$0.22 to $0.36 |
| confluence | (rarely fires — see below) | — | — |

**naive_momentum wins 97% of the time and has approximately zero edge.**
That's the whole lesson in one row: it wins almost every trade because it's
buying positions that were already very likely to win, and it pays a fair
price for that certainty. Win rate and edge are different things once
you're paying a *probability-based price* instead of fixed-odds. Judge this
strategy (and report it to yourself) on **mean pnl / EV**, never on win rate
alone — `stats.py` deliberately does not compare win rate to 50% for this
reason.

One more consequence: `confluence` fires few or zero trades on the
synthetic self-check. That's correct, not broken — its `model_prob`
(`risk/ev_gate.py::confluence_score_to_model_prob`) is an unfit placeholder
anchored near 50%, so it essentially never out-bids a market that's already
fairly priced near the geometry-implied value. A real model needs to start
from `fair_value_prob_up(...)` and add a *learned* adjustment — i.e. it
needs to say "more likely than geometry alone implies," not just "more
likely than 50%." That adjustment is the actual edge you're looking for,
and fitting it is the highest-value next step (see below).

## Data format

Point `--data-dir` at a folder with four CSVs:

- **bars.csv**: `timestamp_ms,open,high,low,close,volume`
- **orderbook.csv**: `timestamp_ms,bid_prices,bid_sizes,ask_prices,ask_sizes`
  (each price/size field is `;`-separated, best-to-worst, e.g. `100.1;100.0;99.9`)
- **funding.csv**: `timestamp_ms,funding_rate`
- **resolutions.csv** (required): `window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision`
  — one row per resolved 5-minute window. `market_prob_up_at_decision` is
  Polymarket's implied UP probability at your entry decision point; leave
  blank if you don't have it yet (the harness will warn you and default to
  0.5, which makes the EV gate nearly blind — get this field real before
  trusting any confluence-strategy output).

All timestamps are unix milliseconds, UTC.

Note: the order book in `orderbook.csv` is the **underlying BTC spot/perp
book** (used for the `orderbook_imbalance` signal, likely sourced from
Binance/Bybit/etc.) — a *different* order book from Polymarket's own
YES/NO share book. This harness doesn't model Polymarket's book directly;
it uses `assumed_polymarket_spread` in the config as a placeholder for
execution friction. Replace that with a real historical spread if you can
get one — it directly changes what clears the EV gate.

### Getting real data

- **BTC bars/volume**: `scripts/fetch_binance_data.py` handles this now —
  Binance's public REST API (`/api/v3/klines`, `/fapi/v1/fundingRate`) keeps
  deep historical data, no key required. Confirm which price feed Polymarket
  actually resolves against before trusting Binance spot as an exact proxy.
- **BTC order book**: not available historically at depth for free —
  `scripts/collect_live.py` captures it going forward (Binance
  `/api/v3/depth`) alongside the Polymarket odds, in one pass.
- **Funding rate**: exchange APIs (Binance/Bybit futures) publish funding
  rate history directly.
- **Polymarket odds + resolutions**: `scripts/collect_live.py` captures the
  decision-moment midpoint/spread/book from the public CLOB read endpoints
  (`/midpoint`, `/book`, `/spread` — no auth). Use its `discover`
  subcommand to find the right market's token id rather than guessing a
  slug; short-dated BTC markets roll over, so ids aren't permanent.
  **Confirm you're passing the UP/YES token** — the DOWN side silently
  inverts every probability you collect. Historical retention on the free
  endpoints is limited, which is why this is prospective capture; check
  `docs.polymarket.com` for current specifics.

  The collector writes **raw JSONL alongside the parsed CSVs on purpose**.
  It could not be tested against the live APIs from the environment it was
  built in (network-restricted) and response shapes drift, so if the
  parsing is subtly wrong the raw captures are still re-parseable rather
  than lost. Hand-verify the first few snapshots before leaving it running
  unattended.

## What's still a placeholder (don't trust these as-is)

- `risk/ev_gate.py::confluence_score_to_model_prob` — flat-50%-anchored
  linear map. Replace with fair-value-plus-learned-adjustment, fit via
  logistic regression against real labeled outcomes.
- `signal_weights` in `config/btc_5m_profiles.yaml` — hand-set (25/20/20/
  15/10/10), carried over from the scaffolding doc unchanged. Fit these,
  don't eyeball them.
- `assumed_polymarket_spread` and `taker_fee` — verify against Polymarket's
  current fee schedule and real historical spreads.
- `min_realized_vol_5m` — the scaffolding doc's version was a fraction
  (0.0015); this harness's `atr_5m` is in raw USD, so the threshold needs
  recalibrating once you see real ATR magnitudes.

## Reading results responsibly

- Trust confidence intervals, not point estimates. Below ~100-200 trades,
  point estimates are close to meaningless (see the CSV-path demo above:
  100% win rate on 5 trades is not a strategy, it's a small sample).
- Always look at `mean_pnl_ci`, not `win_rate`, for whether there's edge.
- Run `--sensitivity`. If small weight perturbations swing the trade count
  or mean pnl a lot, the result is fragile and probably won't survive
  contact with slightly different real data.
- Reserve a holdout period. If you iterate `signal_weights` against the
  same historical window repeatedly, you will eventually fit noise —
  that's not a hypothetical, it's what backtests are for un-doing.
- A clean backtest still isn't a live-performance guarantee: real fills,
  real latency, and real queue position at Polymarket aren't simulated
  here. Treat a good backtest as "cleared to paper-trade," not "cleared to
  size up."

This is engineering/statistics tooling, not investment advice, and 5-minute
BTC direction is a genuinely hard market to have durable edge in — the
point of this harness is to find out honestly, not to assume the answer
either way.

## File map

```
src/data/          schema, CSV loader (DataProvider), synthetic generator
src/signal_engine/  the 6 filter modules + ConfluenceScorer, from the scaffolding doc
src/sizing_engine/  Kelly + vol-adjusted sizer (zero-division bug fixed, small-sample guard added)
src/risk/           EV gate, correlation/anti-clustering tracker
src/backtest/       engine.py (the replay loop), stats.py (CIs/bootstrap/drawdown), fair_value.py (the geometry benchmark)
scripts/run_backtest.py         CLI: synthetic or real data, 3-way comparison, optional sensitivity sweep
scripts/fetch_binance_data.py   pulls real BTC bars + funding rate from Binance (run from your own network)
scripts/collect_live.py         prospective capture: Polymarket odds + BTC L2 depth (raw JSONL + parsed CSV)
scripts/build_resolutions.py    merges collected odds + bars into resolutions.csv
tests/test_smoke.py             plumbing + look-ahead + no-fake-edge self-checks
```
