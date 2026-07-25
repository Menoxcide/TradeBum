# signal_b.csv — edge validation

Data: 9,000 opportunities, uniform 300s spacing, no duplicate timestamps, ts range
2023-11-14 → 2023-12-16 (plausible, no unit corruption). Outcome balance 50.9% up.
Market Brier 0.2031 vs base-rate 0.2499 — the price column is real and informative.
Assumed friction 0.01 per entry (your number, not measured — see caveat below).

## Bottom line

**Do not launch the bot. And — importantly — this is not the same as "there is no edge."**

## Which claim am I making?

**"This data cannot tell us."** Not "there is no edge."

The detection floor is the whole story. I injected known mispricings of increasing
size into the real prices and real features, resampled outcomes, and re-ran the entire
pipeline. Results:

| injected edge | pipeline verdict |
|---|---|
| 0.25x–4x cost (0.0025–0.04) | nothing passed validate |
| 8x cost (0.08) | POSITIVE +0.187 [+0.123, +0.257] |
| 16x cost (0.16) | POSITIVE +0.436 [+0.361, +0.515] |

**Detection floor ≈ 0.08, which is 8x your 0.01 cost.**

The pipeline recovers an 8pp edge cleanly, so it is not broken — the positive control
passes, which means the negative result on the real data is at least *interpretable* as
a measurement. But everything from 0.01 to 0.08 is invisible with 9,000 rows. That band
is the entire range of edges that would be worth trading. A 2pp edge — which would be a
genuinely excellent, highly profitable prediction-market signal at 1pp cost — is
completely undetectable here and would have looked exactly like what I observed.

So the honest reading: any edge in this signal is smaller than 8pp. That rules out
nothing you would have wanted, because nobody expected 8pp. Detection scales with √n,
so confirming an edge at the cost threshold needs roughly **64x more data (~575,000
opportunities)**. Decide whether that is obtainable before spending more time on this
dataset.

## What the data does say (all of it consistent with "no edge", none of it proof)

**1. Deviation size — this alone would stop the trade.** The fitted model's median
disagreement with the market on validate is 0.0132, p90 is 0.0183. Your cost is 0.0100.
Even if *every single disagreement were correct*, the typical trade grosses 1.3pp
against a 1.0pp cost. That is a 0.3pp net on the median trade with no margin for error
whatsoever. There is no configuration of this signal that pays meaningfully, because
the signal never disagrees with the price by enough.

**2. The coefficients are ~zero, which is the actual finding.** Fit with logit(mkt_p) as
a fixed offset, so every coefficient answers "what does this add *on top of* the price?":

- momentum: +0.0036
- volume_ratio: −0.0424
- intercept: +0.1054

Both features are negligible. Note that the intercept is doing most of the work behind
that 0.0132 median deviation — that is a constant tilt inherited from the training
period, not a feature-driven signal. Direct check of feature slopes against the residual
(won − mkt_p) over all 9,000 rows confirms it:

- momentum: −0.144pp per 1 SD, 95% CI ±0.94pp
- volume_ratio: −0.317pp per 1 SD, 95% CI ±3.09pp

Both intervals straddle zero. The CIs are ~1pp and ~3pp wide — again, the same problem:
the precision is at or worse than the cost.

**3. Nothing passed validation, so the holdout is unspent.** Thresholds 0.005 and 0.010
gave flat-to-negative means over ~1,700–2,100 trades. Threshold 0.020 showed
mean +0.0724 — but on n=82 with CI [−0.150, +0.299]. That is exactly the selection trap:
the best-looking mean is the smallest sample. Selecting on the lower bound rejects it,
correctly. Since nothing earned a positive lower bound, the holdout stays clean and
available for a future, better-powered test. Do not let anyone spend it on this.

**4. Zero-friction check: also flat.** +0.0094 [−0.0417, +0.0625] with costs set to zero.
The problem is not execution. Better fills, maker rebates, and smarter routing cannot
rescue this — there is nothing there to rescue. Do not spend effort optimizing fills.

**5. Win rate is a non-signal here.** Every configuration sits at 49.9–52.4%. Worth
stating explicitly because win rate is the number people quote: in a market that prices
probability, win rate carries no information about edge at all. Mean P&L with an
interval is the only thing I judged on.

**6. Market calibration is clean.** Bucketed by market price, realized frequency sits
inside the ±1.96 SE band in all five buckets (largest gap +1.9pp at 0.0–0.2 and 0.6–0.8,
both within their ±2.0–2.4pp intervals). Overall +0.64pp ± 1.03pp. There is no static
mispricing to harvest either.

## Tests run (so the next person does not redo them)

Thirteen comparisons total: 6 thresholds × 1 feature set, 5 price-bucket calibration
checks, 2 feature-residual slopes. **Zero came back positive.** With that many looks at
95% confidence I would expect roughly one spurious winner by chance; getting none is
itself mild evidence the signal is genuinely inert rather than merely unmeasured. If a
positive had appeared, it would have been a lead requiring fresh data, not a finding.

## Caveats I cannot resolve from this CSV

- **The 0.01 friction is assumed, not measured.** You said "around 0.01." This is the free
  parameter that decides the whole question, and here it decides it twice over — it sets
  both the profitability bar and the scale of the detection floor. Measure it from the real
  book *at the size you would trade*: the quoted spread is the cost of an infinitesimal
  order; you pay the size-weighted cost of walking the book, which can easily be double.
  Also check the p90, not just the median — if the tail is many multiples of the median,
  the book is vanishing exactly when you get filled, and your true cost is higher still.
  If real cost is 0.02, the conclusion gets worse, not better.
- **Timing alignment is unverifiable here.** I cannot confirm from this file that momentum
  and volume_ratio were computed strictly from data available *at the instant mkt_p was
  quoted*. This normally matters enormously — stale-price-with-fresh-signal is the single
  largest manufacturer of fake edge. It happens to be moot in this case: a look-ahead bug
  would have *inflated* the result, and the result is flat regardless. Worth fixing before
  any future run on more data, not worth chasing now.
- **Whether `won` matches the venue's actual settlement feed.** Same reasoning — only
  matters once something looks positive.

## What would actually change the answer

In priority order:

1. **Measure your real friction at trade size.** Cheapest step, and it tightens or
   invalidates the entire framing above. Do this regardless.
2. **Get more data.** ~575k opportunities (64x) to put the detection floor at the cost
   threshold. At 300s spacing that is roughly 5 years of one market, or a few months
   across ~50 parallel markets — the cross-sectional route is the realistic one.
3. **Find features that disagree with the price by more than 1–2pp.** This is the real
   blocker and no amount of data fixes it. momentum and volume_ratio are both things the
   market can trivially see; a signal that moves the fair value by 1.3pp against a 1.0pp
   cost is not a business even if it is perfectly real. Look for information the price
   plausibly does not contain, not for better fitting of information it does.

What I will not do is re-cut this dataset until some configuration passes. That always
succeeds and always loses money live.
