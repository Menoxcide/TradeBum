# Revenue plan

Written after seven negative tests on Polymarket's BTC/ETH up-down markets.
Those results constrain what follows, so they come first.

## What we established, and what it rules out

| finding | consequence |
|---|---|
| BTC 5m market prices windows to within **0.65pp** | no directional edge from public features |
| Detection floor **~8pp** at 30 days of data | the tradeable range is unmeasurable here |
| Measured cost **1.2pp** ($50) to **2.8pp** ($250) | small edges cannot pay for themselves |
| Mispricing scales with thinness (0.65 → 1.8pp), so does cost | "find a thinner market" is not an exit |
| p90 slippage **8–17pp** | the book vanishes when you'd be filled |

The binding constraint is not "we haven't found the right signal." It is
that **any edge large enough to detect lives in a book too thin to harvest**.
More searching on this venue is not a plan.

## The goal

Not "make money" — that is not actionable and cannot be falsified. Instead:

> **Within 6 weeks and a hard cap of $500 in data/infrastructure spend,
> determine whether a tradeable edge exists that clears measured costs on a
> venue with real depth — or establish that it does not, and stop.**

Success is a *decision*, not a profit. Either outcome is a win; only
drifting without deciding is a loss. Every stage below has a kill criterion
written before the data arrives, because that is the only time you can write
one honestly.

## Capital constraint: under $1k, and what it forces

This is the number that decides whether Track A is a revenue path or a
tuition payment, so work it through rather than assuming.

Measured execution cost is 1.2pp at $50 and 2.8pp at $250 — cost roughly
doubles as size grows, because you walk the book. So position size is pinned
near the bottom: $50–100 per trade to keep costs tolerable, which is also
where sub-$1k capital caps you anyway.

Now the arithmetic. Suppose a 2pp edge exists and you find it — generous,
given the detection floor here was 8pp. On a $50 position that is **$1.00 per
trade gross**, before the 1.2pp cost takes ~$0.60. At 100 trades a month
that is roughly **$40/month net**, on a strategy that took weeks to validate
and can stop working without warning.

The conclusion is not "trade more" — more trades on a smaller edge is how
costs eat you. It is that **sub-$1k capital cannot produce meaningful income
from this, even if the edge hunt succeeds.** Fixed costs and minimum sizes
dominate, and no amount of skill changes the multiplication.

So Track A is re-scoped, not cancelled:

- It is a **bounded learning exercise with a real deliverable** — a working,
  validated pipeline against a deep venue — not a revenue line.
- Hard cap: **2 weeks and $200**, then it stops regardless of results.
- Its actual payoff is Track B credibility: "here is a validation pipeline I
  ran against live venue data" is the portfolio piece that sells B2.
- If it finds something real, the finding keeps: edge does not expire the
  moment you lack capital, and it becomes actionable if capital arrives.

**Track B carries the revenue expectation.** It has no capital constraint at
all, which is exactly why it is the right primary track at this size.

## The honest base rate

Systematic trading against professional counterparties has a low success
rate for an individual, and the people on the other side have better data,
lower latency, and more capital. Nothing found so far contradicts that.

So the plan runs two tracks in parallel, because they have very different
risk profiles:

- **Track A** (capital at risk, low probability, high variance) — hunt for
  edge on deeper venues.
- **Track B** (no capital at risk, moderate probability, capped upside) —
  monetize the validation capability itself.

Track B is the expected-value anchor. Track A is the lottery ticket, sized
like one.

---

# Track A — find edge on a venue with depth

## Stage A1 — port the machinery (week 1, ~$0)

The statistics are venue-agnostic; only the data adapters change. Build
adapters for a venue where 2.8pp slippage on $250 would be absurd — a major
CEX perp or options book.

Concretely: reuse `validate_edge.py` unchanged, write one loader, and
produce the same CSV shape (`ts, outcome, market_prob, features…`).

**Kill criterion:** if the venue does not expose enough history to clear the
detection floor for a 1pp edge, do not proceed — you would be running a test
that cannot answer the question. Compute the required sample *before*
collecting it.

## Stage A2 — establish the cost floor first (week 1, ~$0)

Measure execution cost before hunting for signal, not after. This inverts the
usual order deliberately: the cost number defines the minimum edge worth
looking for, and it is cheap to measure.

Reuse the approach in `scripts/measure_execution_cost.py`: walk the book at
the sizes you would actually trade, and record the p90, not just the median.

**Kill criterion:** if measured cost at your intended size exceeds ~0.5% per
round trip, the required edge is implausibly large. Pick a different venue.

## Stage A3 — test 3 hypotheses, no more (weeks 2–4, ~$0)

Three, chosen *in advance* and written down before any data is examined.
Three is not arbitrary: each additional test raises the false-positive rate,
and at seven tests you have a 1-in-3 chance of a spurious winner. Candidates
worth the slots, in rough order of prior plausibility:

1. **Cross-venue basis / funding dislocations** — genuinely structural, not a
   pattern in price history.
2. **Options-implied vs realized volatility** — a well-documented risk
   premium rather than a claim of market inefficiency.
3. **Liquidation cascades** — forced flow is not informed flow, which is the
   cleanest reason for a price to be temporarily wrong.

Each runs through `validate_edge.py` with the full protocol: time-split
holdout, lower-bound selection, positive control, detection floor.

**Kill criterion:** if none produces a holdout result whose lower bound
clears measured cost, Track A ends. Do not add a fourth hypothesis — that is
where the false positive comes from.

## Stage A4 — paper trade (weeks 4–6, ~$0)

A backtest that clears the bar is *not* permission to size up. Live queue
position, fills, and latency are not simulated. Paper trade at the intended
size for two weeks minimum.

**Kill criterion:** if live results underperform the backtest by more than
the backtest's own confidence interval, the backtest was wrong. Stop; do not
"adjust for slippage" and continue.

## Stage A5 — deploy small (week 6+)

Only if A4 matched. Size at an amount whose total loss is genuinely
acceptable, with a hard drawdown stop set in advance and honoured
mechanically. Expect the edge to decay; re-run the detection floor monthly.

---

# Track B — monetize the validation capability

No capital at risk, and it does not depend on finding edge.

**The observation this rests on:** in one session, on code that had already
been reviewed, this methodology found a 60-second look-ahead leak, a
21%-per-trade phantom edge, a sign-inverted solver, and a selection rule that
picked an n=11 sample. Those bugs are the norm in strategy code, not the
exception. Most people building trading systems find out with real money.

Three deliverables, in order of effort:

## B1 — the tool, public (week 1–2)

`validate_edge.py` and `pitfalls.md` are already useful standalone. Publish
them. This costs nothing, establishes credibility, and the failure mode is
merely that nobody cares.

## B2 — validation as a service (week 2+)

"Send me your backtest and your data; I'll tell you whether the edge is real
and what your detection floor is." The pitch writes itself from B1: a
concrete list of ways a backtest lies, and a tool that checks for each.

The buyers are individual algo traders and small funds who cannot afford a
quant risk hire. Price per engagement; the work is a few hours once the
pipeline is built.

## B3 — productize (month 2+)

Only if B2 shows repeat demand. Hosted version, or a paid deeper toolkit.
Do not build this speculatively — B2 tells you whether anyone will pay
before you invest.

---

# What I will not do

- Re-cut existing data until something shows green. With an 8pp detection
  floor, anything found that way is noise by construction and loses money
  live.
- Treat a backtest as permission to deploy without paper trading.
- Add hypotheses after seeing results and report only the winner.
- Present a strategy as validated when the detection floor says the test
  could not have detected it either way.

# Immediate next actions

1. Decide capital, time budget, and jurisdiction — these change venue choice
   materially and nothing in Track A should start before they are fixed.
2. Publish B1 (cheap, independent of every other decision).
3. Pick the Track A venue and run A2 (cost floor) before any signal work.
