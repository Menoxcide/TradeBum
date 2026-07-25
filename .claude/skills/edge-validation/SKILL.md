---
name: edge-validation
description: Rigorously test whether a trading, betting, or forecasting signal has real edge that survives costs — before any capital is risked. Use this whenever someone wants to backtest a strategy, evaluate a signal, check if a model beats the market, validate an arbitrage or mispricing idea, tune entry rules against historical data, or interpret backtest results they already have. Also use it when a backtest looks profitable and you need to find out whether it is real, when someone says a strategy "works" or reports a high win rate, or when the task involves prediction markets, sports betting, crypto, equities, or any other bet where a price already exists. Assume a promising backtest is an artifact until proven otherwise — that is the default this skill exists to enforce.
---

# Validating edge before risking capital

Most discovered "edge" is an artifact of the measurement, not a property of
the market. This skill is a procedure for telling the two apart, and for
saying something honest when the answer is no.

The core asymmetry: a false positive costs real money and is discovered
slowly, while a false negative costs an opportunity and is discovered
cheaply by looking again with more data. Set the bar accordingly.

## The one question that matters

Not "does this predict the outcome?" — **"does this add information the
price does not already contain, by more than it costs to act on?"**

A model that forecasts outcomes brilliantly is worth exactly zero if the
market forecasts them equally well. So build the comparison into the model
rather than checking it afterwards: fit with `logit(market_price)` as a
**fixed offset**, so every coefficient answers "what does this feature add
*on top of* the price?" A coefficient near zero is then a real finding —
the market already knows — rather than a failed fit.

If there is no market price, say plainly that you are testing against a
base rate, which is a much weaker claim and does not imply tradeability.

## Procedure

Run `scripts/validate_edge.py`, which does all of this in one pass. Read it
before adapting it; the reasoning is in the comments.

```bash
python scripts/validate_edge.py --data opps.csv --time-col ts \
    --outcome-col won --market-prob-col mkt_p \
    --features z_move,vol_ratio --friction 0.01
```

Input is one row per opportunity: a timestamp, the realized outcome, the
market's implied probability, and your features. The script reports
coefficients, a validated threshold, a single holdout number, a
zero-friction check, and a detection floor.

Interpret the output in this order — the sequence matters, because each
step can make the later ones moot:

1. **Hygiene.** Class balance, duplicate timestamps, and whether the market
   price is even informative (Brier score vs the base rate). A "market
   price" column that fails this is usually the wrong column.
2. **Deviation size.** If the model's median disagreement with the market is
   smaller than the cost of trading, stop. Even if every disagreement were
   correct, it would not pay.
3. **Holdout.** One number, reported once. If nothing earned a positive lower
   bound on validate, the holdout goes unspent — running it anyway is just
   another draw from noise.
4. **Zero-friction check.** If the strategy is flat with costs set to zero,
   the problem is the signal. No execution improvement will rescue it, and
   optimizing fills is wasted effort.
5. **Detection floor.** Covered below. Without it, no result means anything.

## Never report "no edge" without a detection floor

"We found no edge" and "we could not have found any edge" look identical in
output and mean completely different things. Distinguish them by injecting a
known mispricing into the real data and rerunning the entire pipeline: real
prices, real features, resampled outcomes, known answer.

The smallest injected edge that comes back POSITIVE is the detection floor.
Then:

- **floor below your cost** → the test was capable of finding anything worth
  trading. A negative result is genuinely informative.
- **floor above your cost** → the entire tradeable range is unmeasurable
  with this much data. Neither a positive nor a negative result means
  anything, and more searching on this dataset is wasted. Detection scales
  with √n, so compute how much more data would be needed and decide whether
  it is obtainable before spending another hour.

The same injection doubles as a **positive control**. If the pipeline cannot
recover an edge that is definitely present, the bug is in the pipeline, and
every negative result so far is uninterpretable.

## Splitting, selection, and the holdout

Split **by time**, never randomly. Adjacent opportunities are correlated —
consecutive 5-minute windows share overlapping information — and a random
split leaks neighbours across the boundary, producing edge that evaporates
live.

- **train** — fit coefficients only
- **validate** — choose thresholds and configurations; look as much as you like
- **holdout** — touched once, reported once, never tuned against

Select on the **lower confidence bound**, not the point estimate. Selecting
on the mean reliably picks the smallest sample: a threshold with 11 trades
and a spectacular average will win every time and collapse immediately. The
lower bound penalizes small samples automatically.

Count your tests. Seven test families at 95% confidence gives roughly a
1-in-3 chance that one comes back "significant" by luck. If a result appears
after many attempts, treat it as a lead requiring fresh out-of-sample data,
not a finding — and say so explicitly rather than quietly reporting the
winner.

## Costs are measured, not assumed

The friction number decides the entire question, so it cannot be a guess.
Measure it from the real book, and measure it **at the size you would
trade**: the quoted spread is the cost of an infinitesimal trade, while what
you pay is the size-weighted price of walking the book. Cost can easily
double between a small and a medium order.

Watch the tail, not just the median. If p90 slippage is many times the
median, the book is disappearing exactly when you would be filled — that is
adverse selection, and it is where liquidity providers lose money.

Expect mispricing and illiquidity to be the same phenomenon viewed from two
sides. Thin markets really are more loosely priced, and that looseness is
usually smaller than the cost of trading a thin market. "Find a less
efficient market" is not automatically an answer.

## Failure modes that manufacture edge

Read `references/pitfalls.md` before trusting any positive result, and again
when a result looks too good. Each entry there has been observed in real
code producing a confident, plausible, entirely fictional number. The
highest-yield checks:

- **Timing alignment.** Signal and price must be sampled at the *same
  instant*. A signal computed at T paired with a price quoted at T−60s reads
  information the price has not yet absorbed. This is look-ahead wearing a
  convincing disguise and can fabricate double-digit per-trade returns.
- **Bars are timestamped at their open.** "Every bar with timestamp ≤ now"
  silently includes a bar that has not finished forming, whose close is the
  future. Use only bars that had *closed* by the decision time.
- **Win rate is not edge.** In any market that prices probability, a
  strategy entering only near-certain positions shows a high win rate and
  pays a correspondingly high price. Judge on mean P&L. A 95% win rate with
  zero edge is completely normal.
- **Grade against the real settlement.** If the contract settles on a
  different feed than your signals read, grade on the settlement source.
  Disagreements concentrate in near-flat cases, which are exactly the
  marginal trades.
- **Sanity-check magnitude.** A 20%-per-trade return in a liquid market is a
  bug, not a discovery. Treat implausible results as diagnostics pointing at
  code, and go find the cause before reporting the number.

## Reporting

State the finding, the interval, and the floor together — a point estimate
alone is not a result. Record what was tested and what came back negative;
without that list the next person re-runs the same dead ends.

When the answer is no, say so directly and give the arithmetic. "No edge
detected, detection floor ~8pp, measured cost ~1pp, so any edge is below 8pp
and anything under 1pp is unprofitable regardless" is a genuinely useful
result. Padding it into a maybe is not.

Never resolve a negative result by re-cutting the same data until something
passes. That always succeeds and always loses money live. If someone asks
for a profitable configuration and the data does not support one, deliver
the honest answer plus the cheapest experiment that *could* change it.
