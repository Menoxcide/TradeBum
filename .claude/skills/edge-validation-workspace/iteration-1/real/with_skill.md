# Signal C — edge validation

Data: `signal_c.csv`, 20,000 rows, 5-minute spacing, ts 1700000000–1705999700 (~69 days).
Features: `momentum`, `volume_ratio`. Market prob: `mkt_p`. Assumed friction: 0.01/entry.

## Bottom line

**Do not allocate capital.** The apparent edge is a data artifact — `momentum` contains
information about the outcome of the very period it is supposed to predict. The headline
backtest number is fictional, not conservative-but-real.

Separately, and independently of the leak: even a *genuine* edge anywhere in the tradeable
range would have been unmeasurable with this much data. The detection floor is 4x your cost.
So there is no version of this dataset that supports a capital decision right now.

## What the backtest said first

Fitting with `logit(mkt_p)` as a fixed offset (so coefficients measure departure *from the
price*, not from a coin flip), splitting by time, selecting the threshold on the lower
confidence bound, and touching the holdout once:

```
coefficients:  momentum +0.18013   volume_ratio +0.09491
|model - market| on validate: median 0.0251, p90 0.0660
HOLDOUT thr 0.020    n=2947  mean=+0.0732 [+0.0349,+0.1144]  win=55.4%  POSITIVE
zero-friction        n=4451  mean=+0.1614 [+0.1201,+0.2036]  win=55.5%  POSITIVE
```

A positive holdout with a lower bound clear of zero, on a time-split, with the threshold
chosen on the lower bound. This is exactly the result that gets capital allocated. It is
also exactly the result to distrust hardest, because no statistical test can distinguish a
leak from a discovery — if a feature contains the future, the correlation is real and every
holdout confirms it. Only magnitude and mechanism can tell them apart.

## Why it is a leak — three independent confirmations

**1. Magnitude is not survivable.** The model claims a median disagreement with the market
of 2.5 probability points, p90 of 6.6. Real mispricings in a market with participants are
fractions of a point. The holdout claims +7.3% per trade, on 2,947 trades, at 5-minute
frequency. An edge that size in a priced binary market would be arbitraged away long before
you finished measuring it. Calibrated against the script's own injection grid, the observed
median deviation of 0.0251 corresponds to an injected mispricing of roughly 5 probability
points — i.e. the model is asserting the market is wrong by 5pp, persistently, every five
minutes, for 69 days.

**2. Lagging the features by one full period destroys the edge completely.** This is the
decisive and cheap test. Rebuilding the dataset so each row uses the *previous* period's
features and rerunning the entire pipeline unchanged:

```
coefficients:  momentum -0.00375  (was +0.18013)   volume_ratio +0.04266
|model - market| on validate: median 0.0018 (was 0.0251), p90 0.0049
validate: nothing passed — best threshold 0.005 gives n=465, +0.0830 [-0.0199,+0.1858] flat
holdout:  unspent
```

The momentum coefficient does not shrink, it vanishes and flips sign. The model's typical
disagreement with the market falls by 14x, to 0.0018 — well below the 0.01 cost of acting on
it, meaning that even if every remaining disagreement were correct it would not pay. A signal
whose entire value disappears when shifted back by one bar was reading that bar.

**3. The correlation structure has the leak's exact fingerprint.**

```
corr(momentum[t], won[t])   = +0.0923     <- predicts the contemporaneous outcome
corr(momentum[t], mkt_p[t]) = +0.0177     <- but the price has not seen it
corr(momentum[t-1], won[t]) = -0.0020     <- zero predictive value one period out
corr(mkt_p[t], won[t])      = +0.4005     <- the price IS informative (Brier 0.2099 vs 0.2500)
momentum autocorrelation:  lag1 +0.0017, lag2 +0.0066, lag3 -0.0011
```

Read together these are conclusive. `momentum` is serially uncorrelated white noise — it
carries no persistent information about the market at all. Yet it correlates 0.09 with the
outcome of its own period while being essentially orthogonal to the price of that same
period. A legitimately-computed feature that predicted outcomes this well would be partly
reflected in a price that is itself 0.40-correlated with outcomes; this one is invisible to
the market and visible only to the outcome. The only quantity `momentum` has any relationship
with is the thing it is not allowed to know.

The likely mechanism is the classic one: a bar timestamped at its open, included because
`timestamp <= decision_time`, whose close lands after the decision moment. On 5-minute bars
that is the whole window.

## The second, independent problem: this data cannot answer the question

The positive control passes — injected edges are recovered, so the pipeline is not broken and
the negative results above are interpretable. But the recovery threshold is high:

```
injected      |model-mkt|   holdout
0.0025 (0.25x cost)  0.0038   nothing passed validate
0.005  (0.5x cost)   0.0038   nothing passed validate
0.01   (1x cost)     0.0051   nothing passed validate
0.02   (2x cost)     0.0096   +0.0320 [-0.0079,+0.0708]  flat
0.04   (4x cost)     0.0200   +0.0837 [+0.0459,+0.1209]  POSITIVE
```

**Detection floor ≈ 0.04, which is 4x the 0.01 cost.** The entire tradeable band — anything
between 0.01 (breakeven) and 0.04 — is invisible at n=20,000. Detection scales with √n, so
confirming an edge sitting right at the cost threshold needs roughly **16x more data, about
320,000 rows** (~3 years at 5-minute spacing).

This matters even after the leak is fixed. Once features are lagged correctly, a *negative*
result on this dataset would carry almost no information, because the test could not have
found a tradeable edge if one existed. Fixing the leak and rerunning on these same 20,000
rows will produce an uninformative answer either way.

## Caveats a decision-maker should know

- **Costs were assumed, not measured.** The 0.01 came from the brief. Friction is the free
  parameter that decides the whole question, and it must be measured at the size you would
  actually trade — the quoted spread is the cost of an infinitesimal order, while you pay the
  size-weighted price of walking the book, which can easily double. Also check p90 slippage,
  not just the median: a fat tail there is adverse selection. If true cost is above 0.01, the
  detection floor multiple gets worse and the data requirement grows faster than linearly.
- **Win rate is not the evidence.** The 55.4% win rate is not what made this look good and
  should not be quoted. In a market that prices probability, win rate and edge coincide only
  at fixed odds; judge on mean P&L with an interval.
- **`volume_ratio` is untested, not cleared.** Its coefficient survives lagging at +0.043,
  but nothing built on it passed validation, and it was never evaluated in isolation. It is
  unproven, not disproven.
- **Tests run, including negatives (so the next person does not repeat them):** (1) full
  offset-model pipeline on raw data — positive, rejected as leak; (2) same pipeline on
  1-period-lagged features — nothing passed validate; (3) contemporaneous vs lagged
  correlation of momentum with outcome and price — leak fingerprint; (4) momentum
  autocorrelation at lags 1–3 — white noise; (5) injection grid / positive control — pipeline
  validated, floor at 4x cost. Only one configuration was ever selected on validate, so this
  is not a multiple-comparisons winner; the positive result is real in the data and false
  about the world.
- **Min-sample discipline held.** Threshold selection used the lower bound with a 100-trade
  minimum, so the reported holdout is not the smallest-sample winner.

## Cheapest experiment that could change the answer

1. **Find the leak in the feature code, not in the data.** Audit how `momentum` is built and
   assert on bar *close* time (`bar.timestamp + duration <= decision_time`), not open time —
   a test asserting `bar.timestamp <= t` passes while this bug is live. Infer bar duration
   from median spacing rather than assuming it.
2. **Re-extract with features evaluated at the timestamp of the price you could actually have
   traded**, then confirm on a fresh sample — not this one.
3. **Size the new sample first.** You need ~320,000 opportunities at the current cost estimate
   before either answer means anything. Decide whether that history is obtainable *before*
   spending more time searching; if it is not, this signal is not decidable and no amount of
   re-cutting these 20,000 rows will change that.

Do not resolve this by re-running variations until something passes. That always succeeds and
always loses money live.
