# Positioning this as a work sample

Replaces the "sell validations" framing. The artifacts are the same; what
changes is who they are aimed at and what they are asking for.

## Why the switch

The service pitch had a buyer problem. It targets people who own a backtest
they are emotionally invested in, and the product is "I will tell you it does
not work." Those people are price-sensitive and want confirmation, not
disconfirmation.

Who genuinely pays for disconfirmation is whoever has external accountability —
an allocator deciding whether to trust someone else's strategy, a manager who
needs a third-party stamp for LPs. That is a better buyer and a much harder one
to reach cold with no track record.

Meanwhile the same artifacts read as a strong engineering portfolio, and quant
or data-engineering contract work pays $75–200/hour. One engagement beats a
year of $400 reports, and nobody has to buy bad news.

## What the work actually demonstrates

Not "I know statistics." Specifically:

- **Finding bugs that survive review.** Four in code that had already been
  looked at: a 56-second timing gap worth 96% of a headline result; a
  60-second look-ahead from bars stamped at their open; a sign-inverted Newton
  step that diverged to 1e15 without erroring; a threshold selector that picked
  an n=11 sample and collapsed out of sample.
- **Knowing what a measurement cannot show.** Ten hypotheses across two venues,
  every one reported with the smallest edge the data could have confirmed.
  Several were "underpowered", not "negative" — a distinction most analysis
  never makes and which changes what you do next.
- **Killing your own work.** The strategy this began as does not work, and the
  repo says so in the first paragraph.
- **Shipping tools other people can run.** Zero dependencies, positive control,
  worked examples including one with a planted leak.
- **Reverse-engineering undocumented APIs.** Deterministic slug structure and
  closed-market price history that turned "capture data for weeks" into an
  hour-long backfill.

The last one is the most commercially legible: it is ordinary, valuable
engineering that has nothing to do with trading.

## Where to put it

**GitHub profile README.** Pin `edge-validation`. Two sentences: what it does,
and that it was built while killing a strategy of your own.

**A writeup.** The `pitfalls` piece already works. Post it to HN and
r/algotrading as before — but the ask changes. Not "send me your data", just
the writeup. If people find it useful they will look at the profile, and that
is the funnel.

**Contract platforms.** Upwork and similar have steady demand for backtest and
data-pipeline work. The repo is the proposal; most applicants have nothing
comparable to link.

**Direct, to small crypto funds and data shops.** Short note, link, no pitch:

> I built an open-source validator for trading-signal backtests — it reports
> the smallest edge a dataset could actually have confirmed, which is the
> number most analysis omits. Built it while invalidating a strategy of my own
> across ten hypotheses and two venues. If you ever need a second pair of eyes
> on a backtest or a data pipeline, I'd be glad to help.
>
> github.com/Menoxcide/edge-validation

No rates in the first message. The link does the work.

## What to say when asked "did it make money"

Say no, directly:

> No. That was the finding. Ten hypotheses across Polymarket's short-horizon
> crypto markets and OKX perpetuals, and none produced an edge that cleared
> measured costs. The detection floor sat 8x above trading cost on the first
> venue, so most of the tradeable range was unmeasurable with the data that
> exists — which is a more useful thing to know than another failed backtest.

That answer is a better credential than a claimed win, because anyone
technical will assume a claimed win is unvalidated. It also demonstrates the
exact judgement you would be hired for.

## Honest expectations

- Profile and repo: near-zero effort, compounds quietly.
- Writeup: same coin-flip as before on reach, but no conversion needed.
- Contract work: realistically weeks to months to a first engagement, and it
  depends far more on volume of applications than on the quality of the repo.
  The repo makes you credible; it does not make you found.

This is slower to a first dollar than the service pitch and has a much higher
ceiling. It also does not require anyone to pay for bad news, which was the
structural problem with the other plan.

## What is still true from the service plan

If someone reads the writeup and asks for a validation, the pipeline in `b2/`
runs it end to end. Take those — they are case studies, and case studies are
what the contract path runs on. Just do not build the funnel around them.
