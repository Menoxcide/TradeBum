# Failure modes that manufacture edge

Every entry below has been observed in working code producing a confident,
plausible, entirely fictional result. They are ordered by how much fake edge
they typically create.

Contents:
1. Stale price paired with a fresh signal
2. The still-forming bar
3. Win rate mistaken for edge
4. Benchmarking against your own model instead of the market
5. Grading against the wrong settlement source
6. Selection on the point estimate
7. Random splits on serially correlated data
8. Survivorship and the capped sample
9. Assumed costs
10. Silent unit and timestamp corruption
11. Divergent fits that still "work"
12. Multiple comparisons

---

## 1. Stale price paired with a fresh signal

**Fake edge produced: enormous — 20%+ per trade.**

The signal is computed at time T. The best available price is the last print
at or before T, which may be 30–60s old. Pairing them means buying at a price
that predates part of the move being traded on: the market has not yet
reacted to information the signal already has.

This is the single most dangerous bug in this list because nothing looks
wrong. The price is real, the outcome is real, the timestamps are all "≤ T".

**Check:** what is the median age of the price you are filling at? If it is
not ~0, shift the signal's decision time back to the price's timestamp and
rerun. A large drop in P&L confirms it.

**Fix:** evaluate the signal at the timestamp of the price you could actually
have traded. You cannot trade on information newer than your quote.

## 2. The still-forming bar

**Fake edge produced: large, scales with bar size relative to horizon.**

A bar is timestamped at its **open**; its `close` is the price one full
interval *later*. So "every bar with `timestamp <= decision_time`" includes a
bar that had not finished forming, and its close is future information.

With 5-second bars on a 5-minute horizon the leak is 1.7% of the window and
easy to miss. With 1-minute bars it is 20% of the window, on every trade.

**Fix:** only use bars where `timestamp + duration <= decision_time`. Infer
duration from median spacing rather than assuming.

**Note:** a test asserting `all(bar.timestamp <= t)` passes while this bug is
live. Assert on bar *close* time.

## 3. Win rate mistaken for edge

**Fake edge produced: none directly — it disguises the absence of edge.**

In any market pricing probability, entering only near-certain positions gives
a very high win rate at a correspondingly high price. A 95% win rate at an
average entry of 0.95 is exactly zero edge.

Win rate and edge are only the same thing at fixed odds. Report mean P&L per
trade with an interval; do not compare win rate to 50%.

**Corollary:** buying at 0.98 risks 1 to win 0.02, so the priced probability
must be accurate to a fraction of a point. Small calibration errors are
devastating at extreme prices and negligible near 0.5.

## 4. Benchmarking against your own model instead of the market

**Fake edge produced: moderate and highly persuasive — it survives holdouts.**

Comparing realized outcomes to a theoretical fair value (a random-walk model,
a Gaussian, your own estimate) measures the gap between reality and *your
model*. That gap is real and replicates out of sample — and is completely
untradeable, because you cannot transact against your own model.

Real markets often price effects your model omits (fat tails, jumps,
microstructure). Beating a naive benchmark just means the benchmark is naive.

**Fix:** the comparison must be against the price you can actually transact
at. Use `logit(market_price)` as a fixed offset so coefficients measure
departure *from the market*.

**Tell:** the effect is large at moderate probabilities and reverses at
extremes — that shape usually means the benchmark misfits the tails.

## 5. Grading against the wrong settlement source

**Fake edge produced: small but concentrated exactly on marginal trades.**

Contracts settle on a specific feed. If signals read a different feed, some
outcomes are graded wrong. Disagreements cluster in near-flat cases — the
median disagreeing case may show a fraction of the typical move — and those
are precisely the marginal trades a strategy is most likely to get wrong.

**Fix:** record and grade against the venue's actual resolution. Read the
contract's resolution text rather than assuming; feeds can differ between
products on the same venue.

## 6. Selection on the point estimate

**Fake edge produced: large in validation, zero in the holdout.**

Choosing the best-performing configuration by mean return reliably selects
the smallest sample, because small samples have the widest spread and thus
the best maximum. A threshold with 11 trades and a stellar average will win
every sweep and collapse immediately.

**Fix:** select on the lower confidence bound with a minimum sample size.
This penalizes small samples automatically rather than by manual rule.

## 7. Random splits on serially correlated data

**Fake edge produced: moderate, and invisible to every other check.**

Adjacent opportunities share information. Random assignment puts a window's
near-duplicate neighbour in train and the window itself in test, leaking the
answer.

**Fix:** split by time, always. Accept that this makes the test harder — that
is the point. Consider a gap between segments if features use long lookbacks.

## 8. Survivorship and the capped sample

**Fake edge produced: unpredictable — it can flip the sign.**

Risk caps (max trades per day, daily loss halts) don't sample opportunities
randomly. "First 5 signals per day" is both a small sample and a
time-of-day-biased one.

Observed in practice: 569 capped trades gave a mean of −$0.20 with a CI
excluding zero, while the same signal over all 6,241 opportunities gave
+$0.09 with a CI excluding zero. Same data, same code, opposite conclusions,
both "significant".

**Fix:** measure edge with capital constraints lifted, and simulate
constraints separately to answer the different question of what you would
have traded. Never let a capped run be the evidence for or against a signal.

## 9. Assumed costs

**Fake edge produced: whatever you want — this is the free parameter.**

The friction estimate decides the entire question, so a placeholder
determines the answer.

Measure at the size you would trade. The quoted spread is the cost of an
infinitesimal trade; you pay the size-weighted price of walking the book.
Cost can double between a small and a medium order.

Watch the tail. If p90 slippage is many multiples of the median, the book
vanishes exactly when you would be filled — adverse selection, and the main
way liquidity providers lose money.

## 10. Silent unit and timestamp corruption

**Fake edge produced: usually none — it produces silent nonsense instead.**

Exchange data changes units without warning: millisecond vs microsecond
timestamps, seconds vs milliseconds, price scaling. A microsecond timestamp
parsed as milliseconds places data ~50,000 years in the future, matches
nothing, and often does not raise.

Field ordering also differs between venues — some return OHLC, others
LHOC. Silently swapping open and low corrupts every derived feature.

**Fix:** detect units by magnitude per row rather than assuming. After
loading, assert the date range is plausible and spacing is uniform. Check
each venue's documented field order.

## 11. Divergent fits that still "work"

**Fake edge produced: garbage predictions that look like a model.**

A sign error in a Newton step, or perfect separation, produces coefficients
of ~1e15. The code does not error; it returns a "model" that predicts
confident nonsense, and downstream simulation happily reports numbers.

**Fix:** assert coefficient magnitudes are sane after fitting. Check that the
model's typical disagreement with the market is plausible — a median
deviation of 0.65 probability points is believable, 0.65 is not.

## 12. Multiple comparisons

**Fake edge produced: exactly one spurious winner, eventually.**

Each test family is another chance at a false positive. Seven tests at 95%
confidence gives roughly a 1-in-3 chance one comes back significant by luck.

The failure is not running many tests — it is running many and reporting only
the winner, without noting how many were tried.

**Fix:** count the tests and state the count. A result that emerges after
many attempts is a lead requiring fresh out-of-sample data, not a finding.
Keep a written record of every test and its outcome, negative ones included.
