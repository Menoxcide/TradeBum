# Track A — results

All three pre-registered hypotheses tested on OKX BTC-USDT-SWAP. Track A ends
here per the kill criterion, which was written before any data was fetched.

## Cost gate: PASSED

| | Polymarket 5m | OKX perp |
|---|---|---|
| spread | 100 bps | 0.02 bps |
| ask depth | ~$12,600 | $13.7M |
| slippage @ $250k | n/a (book too thin) | 0.01 bps |
| **round trip incl. fees** | **248–560 bps** | **10 bps** |

25–90x cheaper. The structural problem that killed the Polymarket work — any
detectable edge living in a book too thin to harvest — genuinely does not
apply here.

**A correction worth recording.** The first measurement returned 0.03 bps,
counting spread and slippage but omitting exchange fees, which at 5 bps per
side outweigh the spread by ~300x. That is pitfall #9 from this repo's own
list, made while explicitly testing for it. Caught before it reached a result.

## Results

| hypothesis | n | zero-friction mean | verdict |
|---|---|---|---|
| H1 funding carry | 286 | +0.0005 [−0.0025, +0.0038] | **underpowered** |
| H2 cross-venue lead-lag | 43,195 | +0.0000 [−0.0000, +0.0001] | **negative** |
| H3 volatility reversal | 164,123 | −0.0000 [−0.0001, −0.0000] | **negative** |

**H1 is not a negative result.** 114 days yields only 286 funding intervals,
and the detection floor was never reached at any injected edge size — the
dataset cannot confirm an edge of *any* magnitude. The honest statement is
"cannot tell", and resolving it needs years of history, not a better model.

**H2 and H3 are genuine negatives.** Both are flat or worse with costs set to
zero, so the signal is the problem and no execution improvement rescues them.
H2's zero-friction interval is tight enough to bound any edge below ~1 bp per
trade against a 10 bp cost.

**H3 was wrong in an interesting direction.** The `fade_z` coefficient came out
*positive*: large moves on elevated volume continued rather than reverted. The
liquidation-cascade mechanism was real reasoning and the data contradicts it.

## A limitation found in the validator

The detection-floor sweep injects edges in the units of the outcome. In
`market` mode those are probability points and the 0.01–0.12 grid is
well-scaled. In `returns` mode they are *fractional returns*, so 0.01 is a 1%
move per trade — enormous over a 5-minute horizon. The grid is therefore far
too coarse for return-mode tests, and the floors it reports there (2.0% for H2,
1.0% for H3) overstate what is really undetectable.

For return-mode tests the **zero-friction confidence interval** is the better
bound: H2's [−0.0000, +0.0001] rules out anything above ~1 bp directly. Fixing
the sweep to scale its grid to the outcome's own standard deviation is the
obvious improvement, and it is a real defect in a tool that is already public.

## Conclusion

Ten independent tests across two venues, all negative or underpowered. The
cheap-execution venue removed the structural objection to the Polymarket
result and the answer did not change: no detectable edge in any mechanism
tested.

Per the pre-registration: **no fourth hypothesis.** That is precisely where a
false positive would come from.

Track A closes as a bounded learning exercise, which is what it was scoped as
once the capital constraint was worked through. Its deliverable is a validated
pipeline against live venue data. Track B carries the revenue expectation.
