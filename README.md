# BTC 5m Polymarket — Backtest Harness

A way to find out whether the confluence-scored momentum strategy has real
edge *before* any of it touches `EXECUTE=true`. It replays historical data
through the actual signal/sizing/risk code (not a simplified stand-in) and
reports statistics with confidence intervals, not point estimates.

**Status: it has now been run on real data, and the answer is no.** 114 days
of real Binance BTC 1-minute bars (2026-04-01 to 2026-07-23, 164,160 bars,
no gaps) and 8,640 windows of real historical Polymarket odds with real
resolutions. Priced against what Polymarket actually quoted, the strategy's
mean P&L is **$0.08/trade with a 95% CI of [−$0.14, +$0.28]** — statistically
indistinguishable from zero, before friction. No bucket of the calibration
table deviates significantly from its priced-in probability.

That is a real result, not a failure to measure: the market prices these
windows about right, and the `$70-move` momentum signal does not add
information to them. Details in §4b; the two bugs that initially hid this
answer are in §4c and "Corrections".

## Quickstart

```bash
pip install -r requirements.txt

# 1. real BTC bars + funding (Binance public dumps)
python scripts/fetch_binance_vision.py --start 2026-04-01 --end 2026-07-23 \
    --out-dir ./data/historical

# 2. 5-minute windows from those bars (no Polymarket data needed)
python scripts/build_windows_from_bars.py --bars ./data/historical/bars.csv \
    --out ./data/historical/resolutions.csv

# 3. real Polymarket odds + real resolutions for those windows
python scripts/fetch_polymarket_history.py \
    --resolutions ./data/historical/resolutions.csv \
    --out ./data/historical/resolutions_real.csv --start 2026-06-24

# 4. the 3-way comparison
python scripts/run_backtest.py --data-dir ./data/historical --profile research \
    --trades-output ./data/trades.jsonl --sensitivity

# 5. is the entry price a good forecast of the outcome?
python scripts/analyze_calibration.py --trades ./data/trades.jsonl \
    --strategy naive_momentum

# tests, including the look-ahead / no-fake-edge self-checks
python tests/test_smoke.py
```

Every run reports **three strategies side by side** on the same data:
`coinflip` (random direction), `naive_momentum` (momentum direction, no
filtering), and `confluence` (the full scored/gated/Kelly-sized pipeline).
That's the only way to tell whether the added machinery earns its
complexity, versus adding moving parts to noise.

## Read this before you read any numbers

**Win rate is not edge.** If BTC is up $70 with 2 minutes left in a
5-minute window, a *driftless random walk* is already likely to still be up
at the close — not because of predictability, but because there's less time
left for randomness to erase the move. `src/backtest/fair_value.py`
computes that probability exactly (`Φ(move / (σ√τ))`). A strategy that only
enters already-likely-to-win positions shows a high win rate and pays a
correspondingly high price for it.

On the real data below, `naive_momentum` wins **94.9%** of its trades and
makes **$0.09 per trade**. Judge this strategy on mean P&L, never on win
rate. `stats.py` deliberately refuses to compare win rate to 50%.

## What the real-data run found

### 1. The strategy's own design puts its money where the edge is worst

Entering at T-120s after a $70 move means most trades are priced as
near-certainties. Calibration of `naive_momentum` over 6,241 trades
(114 days, every qualifying window), priced at geometry-implied fair value:

| entry price | n | predicted | realized | deviation |
|---|---|---|---|---|
| 0.70–0.80 | 284 | 76.2% | 84.2% | **+7.9pp** |
| 0.80–0.90 | 949 | 85.8% | 89.3% | **+3.4pp** |
| 0.90–0.95 | 1067 | 92.8% | 94.1% | +1.3pp |
| 0.95–0.98 | 1232 | 96.7% | 95.5% | **−1.2pp** |
| 0.98–1.01 | 2683 | 98.9% | 98.4% | **−0.5pp** |
| **all** | **6241** | **94.3%** | **94.9%** | **+0.7pp** |

Bolded rows have a 95% Wilson interval excluding the predicted value.
**63% of trades land in the two buckets where the deviation is negative.**
The aggregate +0.7pp is the average of a real positive effect at moderate
prices and a real negative one at extreme prices.

Buying at 0.98 risks $1 to win $0.02: it needs the priced-in probability to
be right to a fraction of a point, and it isn't. `max_entry_price` /
`min_entry_price` in the config now gate this. On the same data,
`naive_momentum` with no gate makes $563 over 6,241 trades; restricted to
the 0.70–0.90 band it makes $671 over 1,233 trades — 4× more per trade
(mean $0.090 → $0.544), at a lower max drawdown (8.0% → 5.2%).

### 2. That shape is not a volatility-estimation artifact

The obvious objection: the "predicted" column comes from a σ estimated off
trailing bars, so maybe σ is just wrong. But a mis-scaled σ moves *every*
bucket the same direction — a too-high σ pulls all fair values toward 0.5
and makes every favorite look underpriced. Sweeping a `fair_value_vol_scale`
multiplier confirms it: at 1.20 every bucket is positive; at 1.00 the sign
flips across the range. **No single σ reconciles both ends.** A Gaussian
random walk fitted to the body of the distribution genuinely misprices both
tails of this market — moderate continuations are underpriced, extreme
certainties overpriced. That's the signature of real fat tails.

### 3. The moderate-price effect survives a holdout; the tail effect mostly doesn't

Split at 2026-06-01 (the price band was chosen *after* looking at the full
sample, so this matters):

| segment | in-sample (Apr–May) | holdout (Jun–Jul) |
|---|---|---|
| all trades | +0.67pp, $+0.094 [+0.007, +0.174] | +0.69pp, $+0.087 [−0.005, +0.175] |
| entry 0.70–0.90 | +5.69pp, $+0.692 [+0.387, +0.991] | +3.38pp, $+0.414 [+0.087, +0.723] |
| entry ≥ 0.95 | −1.13pp, $−0.116 [−0.194, −0.042] | −0.32pp, $−0.032 [−0.101, +0.029] |

The 0.70–0.90 band replicates with a CI excluding zero in both halves. The
≥0.95 penalty keeps its sign but loses significance out of sample.

### 4. …and none of that is yet a reason to trade

The friction budget is `assumed_polymarket_spread` (0.02) plus
`slippage_buffer` (0.005) — **an edge must exceed ~2.5 probability points
before it is tradeable at all.** The aggregate +0.7pp is nowhere near it.
The holdout 0.70–0.90 band at +3.4pp clears it by less than a point, and
that 2.5pp figure is itself an unverified assumption.

More importantly, everything above is measured against *geometry*, not
against Polymarket. "Beats a random walk" and "beats the price Polymarket
actually quotes" are different claims, and only the second one pays. The
next section runs the second one.

### 4b. Against real Polymarket prices, the edge is gone

8,640 windows of real historical odds (2026-06-24 to 2026-07-23), 8,605
with a real decision-moment price and the market's real resolution:

| bucket | n | predicted | realized | deviation |
|---|---|---|---|---|
| 0.70–0.80 | 76 | 76.9% | 72.4% | −4.5pp |
| 0.80–0.90 | 305 | 86.2% | 88.5% | +2.4pp |
| 0.90–0.95 | 338 | 92.4% | 92.6% | +0.2pp |
| 0.95–1.01 | 243 | 96.9% | 98.4% | +1.5pp |
| **all** | **972** | **90.1%** | **90.8%** | **+0.7pp** |

**Not one bucket's 95% interval excludes its predicted value.** Mean P&L is
**$0.08/trade, 95% CI [−$0.14, +$0.28]** — indistinguishable from zero, and
that's *before* the ~2.5pp friction budget. Split in half by date, each half
is independently indistinguishable from zero (+1.18pp and −0.04pp).

This also explains §1–3 rather than contradicting them. The deviations
against geometry were real, but they were an edge over a *naive Gaussian
model*, not over the market: Polymarket's prices already reflect the
fat-tailed structure that `fair_value_prob_up` misses. Beating your own
benchmark is not the same as beating the people quoting the market.

**Conclusion for the strategy as specified: no detectable edge.** The
`confluence` pipeline fires 1 trade in 8,640 windows, so nothing about the
scoring, sizing, or execution machinery has been validated either.

### 4c. The timing trap — read this before trusting any real-odds run

The first real-odds run reported `naive_momentum` at **$2.10/trade, CI
[$1.81, $2.41]** — a 21% return per trade, at 95.6% win rate, in a market
doing ~$255k per window. That number was entirely an artifact, and it is
worth understanding because it looks completely plausible in a results
table.

`/prices-history` is 1-minute fidelity, so the last available print before
the T-120s decision point is typically ~56s old. The engine was scoring the
signal at T-120s while paying that older price: **it read three minutes of
move and paid a price quoted before the last minute of it.** The strategy
was buying from a market that had not yet seen the move it was trading on.

The engine now shifts the decision timestamp back by `prob_age_sec` so the
signal and the price are evaluated at the same instant. That single change
took mean P&L from $2.10 to $0.08 and moved the CI across zero. The cost is
real — you lose ~56s of signal freshness — but that is the honest constraint:
you cannot trade on information newer than your quote.

The general lesson: whenever a price and a signal come from different
sources, verify they are sampled at the same moment. A stale price paired
with a fresh signal is look-ahead bias wearing a convincing disguise, and
here it fabricated a 21%-per-trade edge.

### 5. Sample size, and a caution about it

A first run using the `conservative` profile's `max_trades_per_day: 5`
produced 569 trades and a mean P&L of **−$0.20, 95% CI [−$0.42, −$0.01]** —
significantly *negative*. The full sample of the same signal gives
**+$0.09, CI [+$0.03, +$0.15]** — significantly *positive*. Same data, same
code, opposite conclusions, both "significant at 95%".

The capped run isn't wrong, it answers a different question: it takes only
the first 5 qualifying windows per day, which is both a small sample and an
early-UTC-hours sample. Use the `research` profile to measure whether a
signal has edge and `conservative` to ask what you'd actually have traded,
and don't let a 569-trade CI that barely excludes zero convince you of a
sign.

## Corrections to earlier assumptions

Four things the scaffolding plan and the first version of this harness had
wrong. Each was checked directly against the live API or the real data.

**Polymarket's 5-minute BTC markets settle on Chainlink, not Binance.**
Market description: *"The resolution source for this market is information
from Chainlink, specifically the BTC/USD data stream."* This harness reads
Binance klines for signals — a different feed. `fetch_polymarket_history.py`
therefore records each market's **actual resolution**, and `ResolvedWindow`
prefers it over the price-derived outcome (`schema.py`). Note the *daily*
"Bitcoin above ___" markets *do* resolve on "the Binance 1 minute candle for
BTC/USDT" — the feed differs per market, so check the one you're trading.

**Historical Polymarket odds are available.** The plan assumed they had to
be captured prospectively. They don't: the 5-minute markets use a
deterministic event slug, `btc-updown-5m-<window_start_unix>`, and CLOB
`/prices-history` serves closed markets at 1-minute fidelity long after
settlement. `fetch_polymarket_history.py` reconstructs months of
decision-moment odds in minutes. BTC L2 *depth* is still genuinely
prospective-only, which is what `collect_live.py` is now for.

**There was a look-ahead bug.** Bars are timestamped at their open, so
"every bar with `timestamp <= decision_ts`" included the bar that hadn't
finished forming — its close is a price from up to a full interval later.
On the 5-second synthetic bars the harness was written against, the leak
was 5 seconds. On real 1-minute bars it's 60 seconds of unknowable price on
a 300-second window, on every trade. `bars_as_of` now returns only bars that
had *closed* by the query time (`provider.infer_bar_duration_ms`), and
`tests/test_smoke.py` has a regression test the old look-ahead test missed.

**Chainlink and Binance disagree on 3.4% of windows.** Over the 8,640
windows with real resolutions, 290 settled the opposite way from what the
Binance close implied. They are exactly the windows you'd expect: median
absolute move of **$2.01**, versus $34 across all windows. Near-flat windows
are where two feeds differ on the last dollar — and they're also where a
momentum strategy's marginal trades live, so grading them against the wrong
feed is not a rounding error.

**A tie resolves UP.** The market resolves Up if the closing price is
"greater than *or equal to*" the opening price, which matches what
`ResolvedWindow.outcome` already did. 0.12% of real windows close exactly
flat, so this is small but not zero.

## Data

`--data-dir` wants four CSVs; only `bars.csv` and `resolutions.csv` are
required.

- **bars.csv**: `timestamp_ms,open,high,low,close,volume` — from
  `fetch_binance_vision.py`. Note `api.binance.com` returns **HTTP 451**
  (hard geo-block, not rate limiting) from many networks including several
  cloud regions; `data.binance.vision` serves the same klines as static
  zips and isn't blocked the same way. Those dumps switched to
  **microsecond** timestamps in 2025 — the fetcher normalizes per row, and
  a loader that doesn't will silently place every bar ~50,000 years out.
- **resolutions.csv**:
  `window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision,prob_age_sec,resolved_outcome`
  — from `build_windows_from_bars.py` (blank odds) then
  `fetch_polymarket_history.py` (real odds + real resolution).
- **orderbook.csv**: `timestamp_ms,bid_prices,bid_sizes,ask_prices,ask_sizes`
  (each field `;`-separated, best-to-worst). Prospective capture only, via
  `collect_live.py`. Without it `orderbook_imbalance` reports zero
  confidence and everything else still runs. This is the **underlying BTC
  spot/perp book**, a different book from Polymarket's own YES/NO share
  book, which this harness does not model.
- **funding.csv**: `timestamp_ms,funding_rate`. Monthly dumps only cover
  complete months, so the current month carries the last known rate
  forward.

All timestamps are unix milliseconds, UTC.

When `market_prob_up_at_decision` is blank the engine prices trades at the
geometry-implied fair value from real estimated volatility, not a flat 0.5.
That makes a run meaningful with BTC data alone — it just answers the
weaker question (§4 above).

A caveat on the historical odds: `/prices-history` is 1-minute fidelity, so
the last print before the decision moment is typically 55–60s stale. A live
bot would see the current book. `prob_age_sec` records the staleness per row
and **the engine uses it to shift the decision timestamp**, so the signal is
measured at the same instant as the price being paid — see §4c for what
happens when it doesn't.

## Still placeholders — don't trust these as-is

- `risk/ev_gate.py::confluence_score_to_model_prob` — a flat-50%-anchored
  linear map, not a fitted model. It's why `confluence` fires ~zero trades
  on real data (1 trade in 8,640 windows): anchored at 50%, it essentially
  never out-bids a market priced near fair value. That is the correct
  behavior for an unfit model, not a bug. A real one should start from
  `fair_value_prob_up(...)` and add a *learned* adjustment — it needs to say
  "more likely than geometry implies", not "more likely than a coin flip".
  Fitting it is the single highest-value change left. Be aware of what §4b
  implies for it, though: the thing a fitted model would have to beat is the
  market price, and on this sample the market price is already well
  calibrated. Fitting a better model is worth doing to *confirm* there's no
  edge; it is not a likely route to finding one.
- `signal_weights` — hand-set (25/20/20/15/10/10), straight from the
  scaffolding doc. Fit them; don't eyeball them. Note nothing has yet
  demonstrated that any signal *other than* price momentum contributes
  anything: with `confluence` firing no trades, the five confirming filters
  are currently unmeasured.
- `assumed_polymarket_spread` (0.02) and `taker_fee` (0.0) — unverified, and
  §4 above turns entirely on them. Real spreads are observable from
  `collect_live.py` captures; get them.
- `min_realized_vol_5m` — the scaffolding doc's 0.0015 was a *fraction*;
  `atr_5m` here is raw USD. Real 5m ATR runs $30–60 on this sample.
- `max_entry_price` — off by default. It's motivated by §1 and survives a
  holdout, but it was chosen after looking at the data. Re-validate before
  relying on it.

## Reading results responsibly

- Trust confidence intervals, not point estimates. Below ~100–200 trades
  point estimates are close to meaningless.
- Always look at `mean_pnl_ci`, never `win_rate`, for whether there's edge.
- Run `--sensitivity`. If small weight perturbations swing the trade count
  or mean P&L a lot, the result is fragile.
- Reserve a holdout. If you iterate `signal_weights` against the same window
  repeatedly you will fit noise — that's not hypothetical, it's what
  backtests are for un-doing.
- A clean backtest still isn't a live-performance guarantee: real fills,
  real latency, and real queue position at Polymarket aren't simulated here.
  Treat a good backtest as "cleared to paper-trade", not "cleared to size
  up".

This is engineering/statistics tooling, not investment advice, and 5-minute
BTC direction is a genuinely hard market to have durable edge in — the point
of this harness is to find out honestly, not to assume the answer either way.

## File map

```
src/data/            schema, CSV loader (DataProvider, look-ahead-safe), synthetic generator
src/signal_engine/   the 6 filter modules + ConfluenceScorer
src/sizing_engine/   Kelly + vol-adjusted sizer
src/risk/            EV gate, correlation/anti-clustering tracker
src/backtest/        engine.py (replay loop), stats.py (CIs/bootstrap/drawdown),
                     fair_value.py (the random-walk benchmark)

scripts/fetch_binance_vision.py     real BTC bars + funding from Binance's dump host
scripts/fetch_binance_data.py       same via the REST API (451 on many networks)
scripts/build_windows_from_bars.py  resolutions.csv from bars alone, no Polymarket data
scripts/fetch_polymarket_history.py real historical odds + real resolutions
scripts/build_resolutions.py        merges a live capture's odds log with bars
scripts/collect_live.py             prospective L2 depth capture (+ live odds)
scripts/run_backtest.py             3-way comparison, sensitivity, trade log
scripts/analyze_calibration.py      predicted vs realized win rate, by price bucket
tests/test_smoke.py                 plumbing + look-ahead + no-fake-edge self-checks
```
