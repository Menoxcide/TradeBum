# Validation engagement — intake

Fill this in before sending data. It takes about twenty minutes and it is the
part that determines whether the analysis can say anything useful.

Every question here exists because getting it wrong has produced a confident,
plausible, entirely fictional result in real code. Several of them cannot be
recovered after the fact: if the answer is missing, the data has to be rebuilt,
and that is your time rather than mine. The questions are grouped by what they
protect against.

Answer in `manifest.yaml` alongside your data (template at the bottom). If you
do not know an answer, write `unknown` rather than guessing — a guess here
propagates silently into the verdict, which is exactly the failure this
service exists to prevent.

---

## 1. The claim

**What result are you asking me to check?** State the metric, the number, and
the sample size. "It makes money" is not checkable; "mean P&L of $2.10 per
trade over 1,356 trades" is.

**What would change your mind?** If the honest answer comes back negative,
what happens next — do you shelve the strategy, or collect more data? This
tells me what the report needs to support, and it is worth knowing before the
result arrives rather than after.

---

## 2. Data provenance — the timing questions

These are the ones that most often turn a spectacular result into a flat one.

**Where did the market price come from, and what is its granularity?**
Tick-level, one-minute bars, end-of-day snapshots?

**How old was the price you filled at, relative to the moment the signal was
computed?** In seconds. If you have not measured this, measure it before
sending — it is the single highest-yield check in the whole process.

> A momentum strategy on five-minute contracts backtested at +$2.10 per trade
> on a $10 stake. The price feed was one-minute granularity, so the quote being
> paid averaged 56 seconds older than the signal being acted on. Aligning them
> took the result to +$0.08 with an interval spanning zero. **96% of the
> apparent profit was that gap.** Nothing about the data looked wrong; every
> timestamp was legitimately at or before the decision moment.

**Are your bars timestamped at their open or their close?** If at the open,
does your as-of lookup exclude the bar that had not finished forming when the
decision was made? A bar stamped at its open carries a close one full interval
in the future. On one-minute bars over a five-minute horizon, that is 20% of
the window leaking backwards, on every single trade.

**Which feed settles the contract, and which feed do your signals read?** If
they differ, some outcomes are graded against the wrong source. The
disagreements cluster in near-flat cases — in one measured sample, disagreeing
windows had a median move of $2 against $34 overall — which are precisely the
marginal trades a strategy gets wrong.

---

## 3. Costs

**Have you measured execution cost, or estimated it?** If estimated, the
analysis will report results across a range of cost assumptions rather than a
single verdict, because the cost number decides the entire question and an
assumed one lets the answer be whatever you like.

**At what order size?** The quoted spread is the cost of an infinitesimal
trade. What you actually pay is the size-weighted price of walking the book,
and it grows with size — in one measured book, from 1.24 to 2.80 percentage
points between a $50 and a $250 order.

**What does the tail look like?** Median and p90. If p90 is many multiples of
the median, the book is vanishing exactly when you would be filled, which is
adverse selection and a different problem from average cost.

---

## 4. Search history

This section is the one people are most tempted to soften. Please don't — an
understated search history produces an overstated verdict, and I would rather
report a smaller result honestly than a larger one you cannot rely on.

**How many configurations did you try in total?** Thresholds, parameter
sweeps, feature sets, entry rules — everything, including the ones you
discarded early. Seven independent tests at 95% confidence gives roughly a
one-in-three chance that one comes back "significant" from luck alone. I can
correct for this if I know the count; I cannot if I don't.

**Did you split by time or randomly?** Adjacent opportunities share
information, so a random split leaks neighbours across the boundary and
manufactures edge that evaporates live.

**Have you already looked at your holdout?** If yes, it is no longer a
holdout — it has become part of the training set, and a fresh out-of-sample
period will be needed. This is recoverable and normal; concealing it is not.

**Was any rule chosen after seeing results?** An entry band, a filter, an
exclusion. This does not disqualify anything; it just means that rule needs
validating on data it was not derived from.

---

## 5. Data file

One row per opportunity. CSV.

| column | required | meaning |
|---|---|---|
| timestamp | yes | sortable; rows split by it |
| outcome | yes | 1 if the "yes"/up side won, 0 otherwise |
| market price | strongly preferred | market's implied probability of that side |
| features | yes | one column per feature, numeric |
| return | if applicable | for directional strategies, P&L of a unit long |

Without a market price column, the model can only be compared against the base
rate. That is a much weaker claim and does not imply tradeability — if a
market exists, send its price.

Send the data that generated the result you are asking about. Not a cleaned
version, not a subset. If cleaning was part of the pipeline, describe it.

---

## Template

```yaml
client: 
submitted: 

claim:
  description: 
  metric: mean_pnl_per_trade      # or win_rate, sharpe, total_return
  value: 
  n_trades: 

data:
  file: opps.csv
  time_col: ts
  outcome_col: won
  market_prob_col: mkt_p          # omit if none exists
  return_col:                     # for directional strategies
  features: [feature_a, feature_b]

provenance:
  price_source: 
  price_granularity_sec: 
  price_age_at_decision_sec:      # measure this
  bar_timestamp_convention:       # open | close | unknown
  bar_interval_sec: 
  excludes_forming_bar:           # true | false | unknown
  settlement_source: 
  signal_price_source: 

costs:
  measured:                       # true | false
  method: 
  cost_per_entry: 
  cost_p90: 
  order_size: 

search:
  configurations_tried: 
  split_method:                   # time | random | none
  holdout_already_examined:       # true | false
  rules_chosen_post_hoc: 
```

---

## What you get back

A report stating the verdict, the confidence interval, and the **detection
floor** — the smallest edge your dataset could have confirmed. That last number
is the one most analyses omit, and it is often the most useful: it separates
"there is no edge here" from "this data cannot tell you either way", which look
identical in output and mean completely different things.

If the result is negative, the report says so plainly and gives the arithmetic,
including how much more data would be needed to resolve the question. A clean
negative delivered early is worth more than a positive you cannot trust, and it
is the more common outcome.

If the result is positive, it will come with the search history correction, the
cost sensitivity, and an explicit statement of what has *not* been tested —
live fills, latency and queue position are not simulated by any backtest. A
good result is "cleared to paper-trade", never "cleared to size up".
