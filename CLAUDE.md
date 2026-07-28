# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this is

A backtest/validation harness for Polymarket's 5-minute BTC up-down markets,
plus the generalized edge-validation tooling that came out of it. It replays
historical data through the real signal/sizing/risk code and reports intervals
rather than point estimates.

**The headline result is negative, and that is the deliverable.** Seven
independent tests came back negative (`README.md` §4g). Mean P&L against real
Polymarket prices is $0.08/trade, 95% CI [−$0.14, +$0.28]; the detection floor
at this sample size is ~8pp while measured execution cost is ~1pp. `PLAN.md`
records the consequence: any edge large enough to detect on this venue lives in
a book too thin to harvest, so the repo's centre of gravity has moved from
"find edge here" to "validate other people's edge claims" (`b2/`) and "test a
deeper venue" (`trackA/`, also now closed).

Treat that as settled. Re-running the Polymarket search, re-cutting the same 30
days, or tuning until a configuration shows green is explicitly out of scope —
see "Methodological rules" below.

## Read first

- `README.md` — the harness, the data contracts, and every result with its
  caveats. Long, but it is the authority on what the numbers mean.
- `PLAN.md` — what the results rule out, and the two tracks that followed.
- `.claude/skills/edge-validation/references/pitfalls.md` — twelve ways a
  backtest manufactures edge. Every one has been observed in this codebase.

## ⚠️ A clean clone does not import

`.gitignore:7` carries an unanchored `data/` rule intended for market data.
Git matches it at any depth, so it also matches `src/data/` — and that package
was never committed to the default branch.

```
$ python3 -c "import src.backtest.engine"
ModuleNotFoundError: No module named 'src.data'
```

Nine of fourteen `src/` modules, every script, and every test import from it,
so on the default branch **nothing runs**. Do not "fix" this by writing a new
`src/data/` from scratch — the real package (`schema.py`, `provider.py`,
`synthetic.py`) is restored on branch `claude/test-coverage-analysis-18j6sk`
(PR #1, open), together with the anchored `/data/` rule. Check whether that has
merged before doing anything that needs the code to execute; if it has not and
you need a runnable tree:

```bash
git checkout origin/claude/test-coverage-analysis-18j6sk -- src/data .gitignore
```

## Layout

```
src/data/            schema (Bar, OrderBookSnapshot, ResolvedWindow, MarketState),
                     DataProvider (CSV, look-ahead-safe), synthetic generator
                     -- ABSENT on the default branch, see above
src/signal_engine/   6 filter modules + ConfluenceScorer
src/sizing_engine/   KellyVolatilitySizer
src/risk/            ev_gate.py (friction screen), correlation_tracker.py
src/backtest/        engine.py (replay loop), stats.py (Wilson/bootstrap CIs),
                     fair_value.py (random-walk benchmark)

scripts/             data fetchers + the analyses (see README's file map)
config/btc_5m_profiles.yaml   conservative / research / aggressive profiles
tests/test_smoke.py  plumbing + look-ahead + no-fake-edge self-checks

.claude/skills/edge-validation/   the published skill: SKILL.md, pitfalls.md,
                     scripts/validate_edge.py (standalone, stdlib-only)
b2/                  intake + engagement pipeline for validating client backtests
trackA/              OKX perp hypothesis tests: pre-registration and results
```

`src/` is a plain package rooted at the repo — imports are absolute
(`from src.data.schema import MarketState`) and scripts do
`sys.path.insert(0, <repo root>)` at the top. There is no `setup.py`, no
`pyproject.toml`, and no install step.

### The pipeline

`run_backtest.py` → `DataProvider` → `engine.run_backtest()` per strategy →
`stats.summarize()`. Every run reports **three strategies side by side** on the
same data — `coinflip`, `naive_momentum`, `confluence` — because that is the
only way to see whether the machinery earns its complexity. Keep that ablation
intact when changing the engine.

Inside `engine.py` the order is: staleness shift → warm-up → direction proposal
→ shared hard gates → strategy-specific gates (confluence only: volume
percentile, confluence score, EV gate, fade check, correlation tracker, Kelly)
→ price-band gate → resolve against ground truth. Every skip path increments a
named reason in `result.skipped_reasons` and appends to the rolling ATR/volume
history before `continue`. **If you add a skip path, do both** — dropping the
history append silently changes every later window's percentile gate.

### Signal modules

`price_momentum` is the only module that proposes a direction
(`propose_direction`). The other five score "how much does this evidence
support the *candidate* direction" on the `BaseFilter` contract: `evaluate()`
returns `(raw, confidence)` with raw 0–100 (50 = neutral) and confidence 0–1.
That contract is load-bearing — `ConfluenceScorer` weights by
`raw * confidence * weight`, and a module returning out-of-range values
corrupts the composite silently.

## Running things

```bash
pip install -r requirements.txt          # PyYAML, requests. That is all.

# smoke test on synthetic data -- proves plumbing, says nothing about edge
python scripts/run_backtest.py --synthetic --profile conservative

# real run, 3-way comparison
python scripts/run_backtest.py --data-dir ./data/historical --profile research \
    --trades-output ./data/trades.jsonl --sensitivity

# does anything beat the market price? how small an edge could we see?
python scripts/fit_model.py --data-dir ./data/real --profile research --power-sweep

# the generic validator, on any CSV of opportunities
python .claude/skills/edge-validation/scripts/validate_edge.py \
    --data opps.csv --time-col ts --outcome-col won \
    --market-prob-col mkt_p --features z_move,vol_ratio --friction 0.01
```

The full data-acquisition sequence (Binance bars → windows → Polymarket odds)
is in `README.md`'s Quickstart. Everything under `data/` is gitignored and
reproducible from `scripts/`.

### Tests

```bash
python tests/test_smoke.py      # self-running, exits non-zero on failure
python -m pytest tests/ -v      # same file, if pytest is installed
```

Both are blocked by the missing `src/data`. PR #1 adds `pytest.ini`,
`requirements-dev.txt`, a CI workflow across Python 3.10–3.12, and ~550 tests;
prefer `pytest` once that lands. There is no linter or formatter configured —
match surrounding style, which is stdlib-only, `from __future__ import
annotations` at the top, and comments that explain *why* rather than what.

## Data contracts

All timestamps are **unix milliseconds, UTC**. `--data-dir` wants four CSVs; only
the first two are required.

| file | columns |
|---|---|
| `bars.csv` | `timestamp_ms,open,high,low,close,volume` |
| `resolutions.csv` | `window_start_ms,window_end_ms,open_price,close_price,market_prob_up_at_decision,prob_age_sec,resolved_outcome` |
| `orderbook.csv` | `timestamp_ms,bid_prices,bid_sizes,ask_prices,ask_sizes` (`;`-separated, best-to-worst) |
| `funding.csv` | `timestamp_ms,funding_rate` |

`orderbook.csv` is the **underlying BTC book**, not Polymarket's YES/NO share
book — two different books, and the config comments say which is which. Without
it `orderbook_imbalance` reports zero confidence and everything else still runs.

## Methodological rules

These are the repo's real conventions. Violating one produces a plausible,
confident, wrong number — which is the exact failure the whole codebase exists
to prevent.

1. **Look-ahead discipline.** Every value in `market_state` comes from a
   `*_as_of(decision_ts)` accessor. Rolling history is updated *after* a window
   is scored, never before. Bars are stamped at their **open**, so a bar with
   `timestamp <= decision_ts` may not have closed yet and its close is the
   future — `bars_as_of` must return only bars that had *closed*.
2. **Signal and price must be sampled at the same instant.** `prob_age_sec`
   shifts the decision timestamp back to the price's own timestamp
   (`engine.py:141`). Removing that shift restores a fabricated ~$2/trade
   "edge". Any new data source needs the same treatment.
3. **Judge mean P&L, never win rate.** `naive_momentum` wins 94.9% of trades
   and makes $0.09 each; in a market that prices probability this is normal and
   means nothing. `stats.py` deliberately refuses to compare win rate to 50% —
   do not add that comparison.
4. **Report intervals, not point estimates.** Below ~100–200 trades a point
   estimate is close to meaningless, and `summarize()` attaches a note saying so.
5. **Never report "no edge" without a detection floor.** "No edge here" and
   "this data cannot tell you either way" look identical in output and imply
   opposite next steps. `--power-sweep` computes the floor.
6. **Split by time, never randomly.** Adjacent 5-minute windows are correlated;
   a random split leaks across the boundary. Select thresholds on the **lower
   confidence bound**, not the point estimate — selecting on the mean reliably
   picks the smallest sample.
7. **Costs are measured, not assumed, and measured at the size you would
   trade.** The quoted spread is the cost of an infinitesimal trade.
8. **Count your tests.** Seven families at 95% is a ~1-in-3 chance of a
   spurious winner. A positive result found after many attempts is a lead
   needing fresh data, not a finding.
9. **Do not delete negative results.** `trackA/RESULTS.md` and README §4g exist
   so the next person does not re-run the same dead ends. Pre-registrations
   (`trackA/PREREGISTRATION.md`) are written before data arrives and are not
   edited afterwards.
10. **Grade against the real settlement feed.** These markets settle on
    Chainlink while the signals read Binance; the two disagree on 3.4% of
    windows, concentrated in near-flat ones — exactly the marginal trades.
    `ResolvedWindow.resolved_outcome` wins over the price-derived guess.
11. **A ≥20%-per-trade result in a liquid market is a bug.** Go find it before
    reporting the number.

## Known traps

- `api.binance.com` returns **HTTP 451** (geo-block) from many networks and
  cloud regions. Use `fetch_binance_vision.py`, which reads the static dumps.
  Those dumps switched to **microsecond** timestamps in 2025; the fetcher
  normalizes per row, and a loader that does not will place bars ~50,000 years
  out.
- `confluence` fires ~zero trades against a fair-priced market (1 in 8,640).
  Per `ev_gate.py:46-56` that is the **correct** outcome for an unfit
  `model_prob`, not a bug. Consequences: the five confirming filters have never
  been measured, and `test_smoke.py`'s flagship self-check passes vacuously.
- Still placeholders, listed in README's "Still placeholders" section:
  `confluence_score_to_model_prob` (flat-50%-anchored linear map, not a fit),
  `signal_weights` (hand-set 25/20/20/15/10/10), `taker_fee` (0.0, unverified),
  `min_realized_vol_5m`, `max_entry_price` (off by default; chosen after seeing
  the data). Fitting `model_prob` is the highest-value change left.
- That README section still calls `assumed_polymarket_spread` a 0.02
  placeholder; §4e and `config/btc_5m_profiles.yaml` supersede it at **0.01,
  measured**. The config is the source of truth.
- `CorrelationTracker` encodes a mean-reversion belief that sits in tension
  with `price_momentum`'s continuation belief. Deliberate, and worth watching
  in `--sensitivity`.

## b2/ — client engagements

`b2/` is the validation-as-a-service pipeline: `INTAKE.md` (questionnaire) →
`intake_check.py` (triage, exits 2 when BLOCKED) → `run_engagement.py`
(→ `report.md`). It shells out to the skill's `validate_edge.py`.

**`b2/engagements/` is gitignored apart from `TEMPLATE/`. Never commit client
data, to this repo or any other.** If a submission is needed as a worked
example, regenerate it synthetically first. `b2/launch/WORK_SAMPLE.md` records
the current framing: these artifacts are positioned as a work sample, not a
service being sold.

## Git

Work on the branch named in the task. Do not push to the default branch
(`claude/shared-session-continuation-fpwoi9`) directly, and push with
`git push -u origin <branch>`.

Commit messages here are declarative statements of what the change establishes
("Eval complete: the real-edge case failed, and the fault was the test"), not
imperatives. Match that. Results commits state the finding, including when the
finding is negative.
