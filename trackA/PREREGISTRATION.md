# Track A — pre-registration

Written **before** any data was fetched or examined. Committed before the first
result. The point of writing it down first is that afterwards I will be able to
tell myself a story about why whichever hypothesis survived was the one I
believed all along.

## Venue

OKX perpetual swaps (`BTC-USDT-SWAP`). Chosen because it is reachable from this
environment (Binance returns 451, Bybit's CDN blocks the region), publishes deep
free history for klines and funding, and has real book depth — the binding
constraint that killed the Polymarket work was that any detectable edge lived in
a book too thin to harvest.

## Order of work

Execution cost is measured **first**, before any signal is examined. The cost
number defines the minimum edge worth looking for, and measuring it after seeing
a promising result invites picking the assumption that makes the result work.

## The three hypotheses

Three, fixed. Not four. Each additional test raises the false-positive rate, and
seven tests at 95% confidence gives a ~30% chance of a spurious winner — that is
the arithmetic that ended the Polymarket work, not a slogan.

Each is stated with its mechanism, because a hypothesis without one is a pattern
in noise waiting to be found.

### H1 — Funding-rate carry

**Mechanism.** Perpetual funding is paid by the crowded side. Persistently high
positive funding means leveraged longs are paying to hold, which is both a
crowding signal and a direct cost that must eventually be borne. If positioning
overshoots, extreme funding should precede mean reversion in the underlying.

**Test.** Does funding at time T predict the sign and size of return over the
following funding interval, beyond zero?

**Prediction.** Negative coefficient: high positive funding → subsequent
underperformance.

### H2 — Cross-venue lead-lag

**Mechanism.** OKX and Coinbase serve different flow. If one venue
systematically incorporates information first, the other's price briefly lags,
and the gap predicts the laggard's next move. This is a microstructure claim
about information propagation, not about anyone being wrong.

**Test.** Does the OKX-minus-Coinbase price gap at time T predict OKX's return
over the next few minutes?

**Prediction.** Negative coefficient: convergence, not divergence.

### H3 — Volatility-regime reversal

**Mechanism.** Forced liquidation flow is mechanical rather than informed. A
large move on elevated volume that partly reflects liquidations should retrace
more than an equivalent move on ordinary volume, since the flow driving it was
not expressing a view.

**Test.** After a large adverse move on elevated volume, is the subsequent
return positive (reversal) beyond zero?

**Prediction.** Positive coefficient on reversal.

## Kill criteria — written before the data

- If measured round-trip execution cost exceeds **0.5%**, stop. The required
  edge becomes implausibly large and the venue is wrong.
- If no hypothesis produces a holdout result whose **lower confidence bound**
  clears measured cost, Track A ends. **No fourth hypothesis.** That is exactly
  where a false positive would come from.
- If the detection floor sits above measured cost, report that the test was
  underpowered rather than reporting a negative — those are different findings
  and only one of them justifies stopping.

## Protocol

Split by time, never randomly. Fit on train, select on validate, spend the
holdout once. Select on the lower confidence bound with a minimum trade count,
never on the point estimate. Run the positive control and report the detection
floor alongside every verdict.

## Prior

I expect all three to come back negative. These are among the most competitive
markets in existence and the mechanisms above are known to everyone in them.
Recording the prior so that a positive result has to overcome it rather than
merely arrive.
