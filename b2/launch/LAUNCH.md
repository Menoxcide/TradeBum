# Launch kit — first paying client

Everything below is written and ready to send. Your job is roughly twenty
minutes of pasting, spread over a week. Nothing here needs editing unless you
want to change the voice.

The sequence matters: the free validations come first, because the case study
is what makes the paid ones sellable. Without a public "here's what they found
in my code" you are a stranger asking for money.

---

## Day 1 — Reddit, r/algotrading

Post title:

> I found a 21% per-trade "edge" in my own backtest. It was a 56-second timing bug. Here are twelve more ways backtests lie.

Body:

> I spent a few weeks validating a momentum strategy on 5-minute crypto
> prediction markets. The backtest said +$2.10 per trade on a $10 stake — 21%
> per trade, 95.6% win rate, in a market doing $255k per window.
>
> Every number in it was real. The prices were real, the outcomes were real,
> every timestamp was legitimately at or before the decision moment.
>
> The price feed was 1-minute granularity, so the quote I was filling at
> averaged 56 seconds older than the signal I was acting on. The strategy was
> buying from a market that hadn't yet seen the move it was trading on. After
> aligning them: +$0.08 per trade, confidence interval spanning zero. **96% of
> the profit was that gap.**
>
> That sent me looking for the others. Twelve of them, each one caught in real
> code that had already been reviewed, ordered by how much fake profit they
> generate: [link to artifact]
>
> The one I'd flag hardest for this sub: **win rate is not edge.** In any
> market that prices probability, entering only near-certain positions gives
> you a 95% win rate and exactly zero edge, because you paid 0.95 for it. If
> you're reporting win rate, you may not have measured anything yet.
>
> Tool's here if useful — no dependencies, MIT:
> github.com/Menoxcide/edge-validation
>
> The bit most backtests skip is the **detection floor**: inject a known
> mispricing into your real data, rerun the whole pipeline, and see the
> smallest edge your dataset could actually have confirmed. Mine was 8
> percentage points against a 1-point trading cost — meaning the entire
> tradeable range was unmeasurable and I'd have been searching noise forever
> without knowing it.
>
> Happy to run it against a few people's data for free if you want a second
> pair of eyes. Just curious how common the timing bug is.

That last line is the whole funnel. Do not make it a sales pitch.

---

## Day 2 — Hacker News

Title: `Twelve ways a backtest lies (and how to make it confess)`
URL: the artifact link.

First comment, post it yourself immediately:

> Author here. The thing that surprised me most wasn't any individual bug, it
> was that "no edge found" and "this data couldn't have detected edge" produce
> identical output and mean completely different things. Almost nothing
> reports the second one.
>
> The fix is cheap: inject a known mispricing into your real data, rerun the
> whole pipeline, and find the smallest edge that comes back positive. That's
> your detection floor. If it's above your trading costs, the entire tradeable
> range is unmeasurable and more searching is wasted — you need more data, not
> better ideas.
>
> Mine came back at 8 percentage points against ~1 point of measured cost.
> Seven hypotheses, all dead. The negative result is the useful part.

HN rewards the honest negative. Do not oversell.

---

## Day 3–7 — the free validations

Reply to anyone who bites:

> Happy to. Send me:
>
> 1. A CSV — one row per trade/opportunity, with a timestamp, the outcome
>    (1/0), the market's price at entry, and whatever features your signal
>    uses.
> 2. Roughly what your backtest reported, and how many configurations you
>    tried before landing on it.
>
> You'll get back a report with the verdict, confidence intervals, and your
> detection floor. Usually takes me a day.
>
> Fair warning: most come back negative. That's the base rate for strategy
> code, not a comment on yours — I killed my own after seven tests.

Run each through the pipeline:

```bash
python b2/intake_check.py --manifest engagements/<name>/manifest.yaml
python b2/run_engagement.py --manifest engagements/<name>/manifest.yaml
```

Then ask for one thing:

> If this was useful, a couple of sentences I can quote would help me a lot.

Three testimonials is the threshold where paid work becomes sellable.

---

## Week 2 — start charging

Once you have testimonials, add this to your posts and to a simple landing
page (the artifact works as one):

> **Backtest validation — $400.** Send your data and your claim, get back a
> report with the verdict, the confidence intervals, and the smallest edge your
> data could actually have detected. Most come back negative; that's what
> you're paying to find out before you trade it. Blocked submissions —
> where the data has a defect that can't be corrected downstream — are $150,
> because identifying that is usually worth more than the analysis would have
> been.

$400 is deliberate: cheap enough to be an easy yes against the cost of trading
a broken strategy, expensive enough to filter people who want a rubber stamp.

---

## Expected outcome, honestly

- Reddit post: maybe 5–20k views, 2–5 people take the free offer.
- HN: a coin flip. Front page is 20k+ views, missing it is ~200.
- Realistic first month: 3–5 free validations, 0–2 paid. **$0–800.**
- Realistic month three, if the testimonials land: 2–4 paid. **$800–1600.**

That is a real side income, not a business, and it is roughly the honest
ceiling for one person with no capital and no existing audience. Anyone
promising more from this starting position is selling something.

The compounding asset is reputation for calling things dead. That is rare, and
it is why the negative result gets led with rather than buried.

---

## The part I can't do

I can write every word here and run every analysis. I can't post as you, hold
a client conversation, or take a payment. If nobody posts this, it earns
exactly zero — and that is the actual gap between where we are and cash, not
any missing code.
