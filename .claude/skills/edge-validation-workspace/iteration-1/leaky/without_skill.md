# Edge validation: signal_a.csv

## Bottom line: DO NOT TRADE IT.

The backtest looks incredible because the `momentum` column is **leaked** — it is
constructed from the outcome it is supposed to predict. Once you restrict the model
to information that would actually have been available before the bar resolved, the
edge is not merely smaller, it is **negative** and the strategy loses money to costs.

Do not put capital behind this.

---

## The data

12,000 rows, clean 300-second bar spacing (all 11,999 gaps are exactly 300s, no
duplicate timestamps, no gaps, no missing values). Base rate `won` = 0.498. Market
implied probability `mkt_p` averages 0.500.

## Why the backtest looks incredible

`momentum` correlates **+0.812** with `won`. The market's own price correlates only
+0.352. A raw feature that beats the market's price by that margin, on a liquid
crypto market, is not a discovery — it is a bug.

Backtest using `momentum` as it sits in the file (buy up when the implied model
probability exceeds `mkt_p` by 2pts, sell otherwise, 0.01 cost per entry):

| Strategy | Trades | Total PnL | Per-trade | t-stat |
|---|---|---|---|---|
| Naive, using `momentum` as given | 11,752 | **+4,152** | **+0.353** | **128.6** |

+0.353 per unit staked per trade, on a market that pays at most ~1.0, with a t-stat
of 128. Nothing in liquid crypto produces this. A t-stat above ~5 on a market with a
posted price is a bug report, not a discovery.

## Proof of the leak

I recovered the generating equation. `momentum` is exactly:

```
momentum = 1.3919 * (2*won - 1) + N(0, 1)
```

Subtracting that outcome term leaves a residual with mean -0.003, **sd 0.999**,
correlation with `won` of **-0.0000**, and correlation with `mkt_p` of -0.009. The
residual is clean unit Gaussian noise. In other words, `momentum` contains the
answer plus noise, and nothing else. It carries no market information at all.

Corroborating evidence, all pointing the same way:

- `sign(momentum) == won` **91.8%** of the time. That, alone, is the whole "signal."
- Conditional means: `momentum` is N(-1.395, 0.99) when `won=0` and N(+1.389, 1.01)
  when `won=1` — symmetric, same variance, textbook signal-plus-noise construction.
- **Lagged** `momentum` (the previous bar's value, which is genuinely knowable before
  the current bar resolves) correlates **-0.018** with `won`. Essentially zero. A real
  momentum feature carries information forward in time; this one only "knows" its own
  bar's outcome.
- `momentum` autocorrelation at lag 1 is **-0.006**. A real price-momentum series is
  persistent. This one has no memory whatsoever, because it is just relabeled noise
  around the outcome.

## What happens when the leak is removed

Refit using only information available before the bar: lagged `momentum`, lagged
`volume_ratio`, and `mkt_p`. Logistic regression fit on the first half, evaluated
walk-forward on the held-out second half.

| Strategy | Trades | Total PnL | Per-trade | t-stat |
|---|---|---|---|---|
| Leak-free walk-forward | 4,283 | **-61.1** | **-0.0143** | -2.06 |

Fitted weights: intercept -1.142, `mkt_p` **+2.848**, `mom_lag` **-0.006**,
`vol_lag` -0.284. The model puts essentially all its weight on `mkt_p` — it learns
to copy the market — and assigns lagged momentum a coefficient of ~zero. Even so it
loses 1.43 cents per trade, because copying the market and paying 0.01 to do it is a
guaranteed loss.

Out-of-sample Brier score: model **0.2211** vs market **0.2171**. The model is
*worse* than the price it is trying to beat, before costs.

## The market is well calibrated — there is nothing to exploit

Calibration regression of `won` on `mkt_p`: **slope 0.984, intercept 0.006**
(ideal is 1.0 and 0.0). Decile-by-decile the empirical rate tracks the implied
probability closely across the entire range, from the 0.0-0.1 bucket (6.4% actual vs
6.3% implied) to the 0.9-1.0 bucket (91.9% vs 93.7%). Market log loss is 0.627 vs
0.693 for a coin flip, so the price carries genuine information and prices it
honestly.

This matters: an efficiently priced, well-calibrated market means any edge must come
from information the market does not have. You do not have any.

## The other feature is dead

`volume_ratio` correlates **+0.004** with `won` — noise. Conditioning on it does
nothing: the top decile by volume ratio wins 47.2% (market implied 49.2%), the top
quartile wins 50.1% (market implied 49.9%). No subgroup edge.

## Specific concerns about the data

1. **`momentum` is outcome-derived and must be discarded entirely.** This is not a
   look-ahead of a few seconds that can be fixed with a shift — the column is a
   function of the label. Any result that touches it is meaningless.
2. **Fix the pipeline, not the feature.** Whatever process produced this column
   computed it after resolution, or over a window that includes the resolution bar.
   Every other feature from that pipeline is suspect until each one is individually
   verified against a strict point-in-time cutoff.
3. **The data looks synthetic.** Perfectly uniform 300s spacing with zero gaps, no
   missing values, no outliers, and a recoverable closed-form generating equation.
   Real crypto data has exchange outages, missing bars, and fat tails. Conclusions
   drawn here may not transfer to live data even after the leak is fixed.
4. **No cost model beyond the flat 0.01.** Real trading also pays slippage, spread
   crossing, and funding. 0.01 is a floor, so the true hurdle is higher than the one
   the leak-free test already fails.
5. **The 0.01 cost is large relative to any plausible edge.** With a calibrated
   market, you would need to beat the price by more than 1 full percentage point of
   probability, consistently, just to break even. Nothing in this dataset does that.

## What would have caught this before you got excited

A single question: *is any feature more correlated with the outcome than the market's
own price is?* Here `momentum` (0.81) crushed `mkt_p` (0.35). Whenever a raw feature
out-predicts a live traded price, assume leakage until you have proven otherwise.
The lag test is the cheap confirmation — shift the feature back one period and see if
the edge survives. Here it evaporated completely (0.81 to -0.018).

## Recommendation

**Do not trade this signal.** Discard `momentum`, audit the feature pipeline that
produced it for point-in-time correctness, and re-run the evaluation from scratch.
On the evidence available, `volume_ratio` has no edge and the market is efficiently
priced, so there is no tradeable signal in this file at all.
