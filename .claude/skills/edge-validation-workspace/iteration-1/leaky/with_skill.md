# Edge validation: `signal_a.csv` (crypto, 5-min bars)

## Bottom line

**DO NOT TRADE IT.** The backtest is not "incredible" — it is broken. The
`momentum` feature contains the outcome it is supposed to predict. Once that
leak is removed there is no measurable edge, and on top of that this dataset
could not have detected a tradeable edge even if one existed.

Two separate conclusions, and both matter:

1. The headline result is a **look-ahead artifact**, not a finding.
2. The clean result is **"can't tell"**, not "no edge" — the detection floor
   (~8pp) is 8x your 1pp cost, so the entire economically interesting range is
   invisible in 12,000 rows.

Either one alone is enough to keep capital off this.

---

## What the headline backtest said

Run through `scripts/validate_edge.py` with `momentum,volume_ratio` against
the market price as a fixed offset, friction 0.01:

```
coefficients:  momentum +2.80264   volume_ratio +0.14509
|model - market| on validate: median 0.3703, p90 0.5962
validate thr 0.080:  n=2807  mean=+0.8598 [+0.8219,+0.9001]  win=93.3%  POSITIVE
HOLDOUT   thr 0.080: n=2799  mean=+0.8354 [+0.7999,+0.8720]  win=93.7%  POSITIVE
```

+0.84 mean P&L per contract, 93.7% win rate, positive on a time-split holdout
that was touched once. This is exactly the shape of result that gets people to
size up. It is fictional.

## Why it is fictional

**The magnitude is the first diagnostic, not the finding.** An 84%-per-trade
return in liquid crypto is a bug. Treat implausible results as pointers at
code, not discoveries — that instinct is what turned this up.

**The model disagrees with the market by 37 probability points at the median.**
A believable median deviation from a real market price is a fraction of one
point. 0.3703 is not a model that has found a small mispricing; it is a model
that knows something the price cannot know.

**`momentum` correlates 0.81 with the outcome — but only contemporaneously:**

| relationship | corr |
|---|---|
| `momentum[i]` vs `won[i-2]` | −0.019 |
| `momentum[i]` vs `won[i-1]` | −0.012 |
| **`momentum[i]` vs `won[i]`** | **+0.812** |
| `momentum[i]` vs `won[i+1]` | −0.018 |
| `momentum[i]` vs `won[i+2]` | +0.001 |

Genuine predictive features decay smoothly across neighbouring bars. A spike at
lag 0 with pure noise on both sides is not prediction — it is the same quantity
measured twice. `sign(momentum)` matches the outcome on **91.8%** of rows
(11,018 / 12,000), which is the 92% "win rate" reappearing under a different
name. The momentum window and the resolution window are the same window: the
bar is being read after it closed.

This is pitfalls #1 and #2 in combination — a signal timestamped as if it were
available at the bar's open while actually containing the bar's close. Nothing
in the file looks wrong; every timestamp is monotone, spacing is a clean 300s,
there are zero duplicates, and the market price is genuinely informative
(Brier 0.219 vs 0.250 base rate). The data passes every hygiene check and is
still unusable.

## The two isolation tests that settle it

**Test A — drop `momentum`, keep `volume_ratio` only:**

```
threshold 0.005: n=733  mean=-0.0583 [-0.1285,+0.0167]  win=48.3%  flat
all other thresholds: no trades.  Holdout stays unspent.
```

**Test B — lag both features by one bar** (i.e. use only what had actually
closed by the decision time), rerun the entire pipeline:

```
coefficients:  momentum -0.00420   volume_ratio -0.06585
|model - market| on validate: median 0.0044, p90 0.0104
threshold 0.005: n=1306  mean=-0.0475 [-0.1068,+0.0124]  win=48.0%  flat
threshold 0.010: n= 339  mean=-0.0894 [-0.2040,+0.0278]  win=46.3%  flat
zero-friction:   n=1306  mean=-0.0257 [-0.0870,+0.0356]  win=48.0%  flat
Holdout stays unspent — nothing earned a positive lower bound on validate.
```

The momentum coefficient collapses from **+2.803 to −0.004** — a 670x drop, to
indistinguishable from zero — from a one-bar shift. That is the confirmation.
Under the offset fit, a coefficient of ~0 is a real finding: *the market
already prices whatever momentum contains.*

Note also that the honest model's median disagreement with the market is
0.0044, **below the 0.01 cost of acting on it**. Even if every one of those
disagreements were correct, it would not pay. That check alone ends the
inquiry before any P&L question arises.

The zero-friction run is also flat. This is not an execution problem, so
optimizing fills, chasing better venues, or shaving fees cannot rescue it. The
problem is the signal.

## Detection floor — why "no edge" is not the honest phrasing

Injecting a known mispricing into the real prices and features, resampling
outcomes, and rerunning the whole pipeline (clean lagged data):

| injected edge | median &#124;model−mkt&#124; | holdout result |
|---|---|---|
| real | 0.0044 | nothing passed validate |
| 0.010 | 0.0088 | nothing passed validate |
| 0.020 | 0.0131 | nothing passed validate |
| 0.030 | 0.0193 | nothing passed validate |
| 0.050 | 0.0343 | +0.0350 [−0.0200,+0.0886] flat |
| **0.080** | 0.0556 | **+0.1348 [+0.0910,+0.1822] POSITIVE** |
| 0.120 | 0.0831 | +0.2476 [+0.1996,+0.2984] POSITIVE |

**Detection floor ≈ 0.080. Your cost ≈ 0.010.**

The pipeline recovers an 8pp edge, so it is not broken — the positive control
passes. But it cannot see anything below 8pp, and the entire range worth
trading (1pp to 8pp) sits underneath that. So the correct statement is:

> Any edge here is smaller than 8pp, and anything under 1pp is unprofitable
> after costs regardless. The band in between is unmeasurable with 12,000 rows.

That is genuinely different from "there is no edge." I cannot rule out a 3pp
edge; I can only say this dataset is incapable of showing me one. Detection
scales with √n, so confirming an edge down at the cost threshold needs roughly
**64x more data** — about 770,000 five-minute bars, or ~7 years of continuous
5-minute observations. Decide whether that is obtainable before spending
another hour searching this file.

(Sanity note on the leaked run: its detection floor came out at 0.120 while the
"real" result printed +0.84 — a result seven times larger than the biggest edge
the test could resolve. A measurement exceeding its own instrument's ceiling is
itself a leak signature.)

## Specific concerns about the data

- **`momentum` is contaminated and cannot be used as delivered.** It reads the
  same 5-minute window that determines `won`. Recompute it from bars where
  `timestamp + duration <= decision_time`, and re-derive the whole dataset. A
  test asserting `all(bar.timestamp <= t)` passes while this bug is live —
  assert on bar *close* time.
- **The 93.7% win rate is meaningless as evidence**, twice over. It is the leak
  restated, and separately, in any market that prices probability a high win
  rate is normal at zero edge — it just means entering near-certain positions
  at a correspondingly high price. Judge on mean P&L with an interval.
- **Costs are assumed, not measured.** 0.01 was supplied as "about." That number
  decides the entire question and it is currently a free parameter. Measure it
  from the real book at the size you would actually trade — the quoted spread is
  the cost of an infinitesimal order, and walking the book can easily double it.
  Check p90 slippage too; if the tail is many multiples of the median, the book
  is vanishing exactly when you get filled.
- **Settlement source is unverified.** I have no way to check that `won` was
  graded on the same feed the contract actually resolves against. Disagreements
  concentrate in near-flat cases, which are precisely the marginal trades.
- **`volume_ratio` is inert** — correlation with the outcome is 0.004, and it
  contributes nothing in either isolation test.
- Clean bill on the rest: 12,000 rows, uniform 300s spacing, no duplicate
  timestamps, balanced outcomes (49.8% up), and the market price is informative.
  The leak survived all of these, which is the point.

## Tests run, including the negatives

Five test families, all reported:

1. Full model, `momentum,volume_ratio`, market offset, friction 0.01 → POSITIVE,
   rejected as leakage.
2. Lead/lag correlation structure of `momentum` vs `won` → **leak confirmed.**
3. `volume_ratio` alone → flat, holdout unspent.
4. Both features lagged one bar → flat at every threshold, holdout unspent,
   flat at zero friction.
5. Injection sweep / positive control on clean data → floor ≈ 0.080.

The holdout was spent exactly once, on the leaked configuration, before the
leak was identified. It is now burned for this dataset. On the clean data
nothing earned a positive lower bound on validate, so its holdout correctly
went untouched — and any future test on these same 12,000 rows no longer has a
clean holdout available. That is a further reason not to keep re-cutting this
file until something passes; that procedure always succeeds and always loses
money live.

## What would actually change the answer

In cost order:

1. **Rebuild `momentum` with correct bar-close alignment** and confirm the
   lag-0 correlation spike disappears. Until this is done nothing else is
   interpretable. This is a data-generation fix, not an analysis fix.
2. **Measure real friction** from the book at your intended size, including the
   p90 tail. If true cost is 2pp rather than 1pp, the required-data arithmetic
   gets worse, not better, and the question may close on cost alone.
3. **Get more data before searching further.** ~64x more rows to resolve edge at
   the cost threshold. If that is not obtainable, this signal is not answerable
   and no amount of re-analysis will change that.

Do not put money behind this in its current state.
