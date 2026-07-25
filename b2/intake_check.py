#!/usr/bin/env python3
"""
Triage a validation submission before any analysis time is spent on it.

Reads a client's manifest.yaml plus their data file and answers one question:
**can this engagement produce a meaningful verdict at all?**

That question is worth asking first because some defects cannot be fixed
downstream. If the market price was quoted a minute before the signal was
computed, no amount of careful statistics recovers the truth -- the data has
to be rebuilt. Finding that out in the first ten minutes rather than the
fourth hour is most of the value of having an intake step.

Three outcomes:

  BLOCKED    a defect that invalidates any result. The client rebuilds the
             data or answers the missing question; nothing is analysed yet.
  QUALIFIED  runnable, with caveats that must appear in the report.
  CLEAN      runnable as-is.

Usage:
  python intake_check.py --manifest engagements/acme/manifest.yaml
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("needs PyYAML:  pip install pyyaml")


class Findings:
    """Blockers stop the engagement; caveats travel into the report; notes are
    context. Keeping them separate matters because the client needs to know
    which ones they have to act on."""

    def __init__(self):
        self.blockers: list[tuple[str, str]] = []
        self.caveats: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def block(self, title, detail):
        self.blockers.append((title, detail))

    def caveat(self, title, detail):
        self.caveats.append((title, detail))

    def note(self, text):
        self.notes.append(text)


def get(d, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur or cur[key] is None:
            return default
        cur = cur[key]
    return cur


def unknown(value):
    return value is None or (isinstance(value, str) and value.strip().lower() in ("", "unknown", "?"))


# ---------------------------------------------------------------- checks


def check_timing(m, f):
    """The highest-yield check. A price older than the signal lets the
    strategy trade on information the market has not yet absorbed."""
    age = get(m, "provenance", "price_age_at_decision_sec")
    gran = get(m, "provenance", "price_granularity_sec")

    if unknown(age):
        f.block(
            "Price staleness not measured",
            "provenance.price_age_at_decision_sec is missing. This is the single most "
            "common source of fake edge: if the price being paid predates the signal, "
            "the strategy is trading on information the market has not seen. Measure "
            "the median age of the fill price relative to the decision timestamp. "
            "Observed in practice: a 56-second gap accounted for 96% of a 21%/trade result.",
        )
        return

    try:
        age = float(age)
    except (TypeError, ValueError):
        f.block("Price staleness unparseable", f"got {age!r}, expected seconds as a number")
        return

    if age > 0:
        f.caveat(
            f"Price is {age:.0f}s stale at the decision moment",
            "The analysis will shift the signal's decision timestamp back by this amount "
            "so signal and price are evaluated at the same instant. Expect the result to "
            "come in below the client's reported figure -- that difference is the portion "
            "of their edge that was look-ahead, and it should be stated explicitly.",
        )
    if not unknown(gran):
        try:
            if float(gran) > 60:
                f.caveat(
                    f"Coarse price granularity ({gran}s)",
                    "Anything above about a minute means the decision-moment price is a "
                    "rough approximation of what was quotable. Results are indicative.",
                )
        except (TypeError, ValueError):
            pass


def check_bars(m, f):
    conv = get(m, "provenance", "bar_timestamp_convention")
    excl = get(m, "provenance", "excludes_forming_bar")
    interval = get(m, "provenance", "bar_interval_sec")

    if unknown(conv):
        f.caveat(
            "Bar timestamp convention unknown",
            "If bars are stamped at their open, the bar covering the decision moment had "
            "not finished forming, and its close is future information. Ask before relying "
            "on any feature derived from bars.",
        )
        return

    if str(conv).lower() == "open":
        if excl is True:
            f.note("Bars stamped at open, forming bar correctly excluded.")
        elif excl is False:
            detail = (
                "Bars are stamped at their open and the forming bar is NOT excluded, so every "
                "bar-derived feature contains up to one full interval of future price."
            )
            if not unknown(interval):
                try:
                    detail += f" At {float(interval):.0f}s bars that is {float(interval):.0f}s of leak per decision."
                except (TypeError, ValueError):
                    pass
            f.block("Look-ahead from the forming bar", detail + " The features must be rebuilt.")
        else:
            f.caveat(
                "Forming-bar handling unconfirmed",
                "Bars are stamped at their open; confirm the as-of lookup requires "
                "timestamp + interval <= decision time, not timestamp <= decision time. "
                "A test asserting the latter passes while the bug is live.",
            )


def check_settlement(m, f):
    settle = get(m, "provenance", "settlement_source")
    signal = get(m, "provenance", "signal_price_source")
    if unknown(settle) or unknown(signal):
        f.caveat(
            "Settlement source unconfirmed",
            "Confirm which feed settles the contract. If it differs from the feed the "
            "signals read, some outcomes are graded wrong -- concentrated in near-flat "
            "cases, which are the marginal trades.",
        )
    elif str(settle).strip().lower() != str(signal).strip().lower():
        f.caveat(
            f"Settlement ({settle}) differs from signal feed ({signal})",
            "Outcomes should be graded against the settlement source. Expect disagreement "
            "on a small share of cases, concentrated where the move is near zero.",
        )


def check_costs(m, f):
    measured = get(m, "costs", "measured")
    cost = get(m, "costs", "cost_per_entry")
    p90 = get(m, "costs", "cost_p90")
    size = get(m, "costs", "order_size")

    if unknown(cost):
        f.block(
            "No cost figure supplied",
            "costs.cost_per_entry is required. The friction number decides the entire "
            "question -- with it unset, the verdict can be whatever the analyst chooses.",
        )
        return

    if measured is not True:
        f.caveat(
            "Costs are estimated, not measured",
            "The report will present results across a range of cost assumptions rather "
            "than a single verdict. Recommend measuring by walking the live book at the "
            "intended order size.",
        )
    if unknown(size):
        f.caveat("Order size not stated",
                 "Cost grows with size as you walk the book. A cost figure without a size "
                 "is not actionable.")
    if not unknown(p90) and not unknown(cost):
        try:
            if float(p90) > 3 * float(cost):
                f.caveat(
                    f"Cost tail is heavy (p90 {p90} vs median {cost})",
                    "The book is thinning exactly when fills happen. This is adverse "
                    "selection and is not captured by an average-cost model; size "
                    "recommendations should reflect the tail, not the median.",
                )
        except (TypeError, ValueError):
            pass


def check_search(m, f):
    tried = get(m, "search", "configurations_tried")
    split = get(m, "search", "split_method")
    peeked = get(m, "search", "holdout_already_examined")
    posthoc = get(m, "search", "rules_chosen_post_hoc")

    if unknown(tried):
        f.block(
            "Search history not disclosed",
            "search.configurations_tried is required. Seven independent tests at 95% "
            "confidence gives roughly a one-in-three chance of a spurious winner. This is "
            "correctable when the count is known and not correctable when it isn't.",
        )
    else:
        try:
            n = int(tried)
            if n > 20:
                f.caveat(
                    f"{n} configurations tried",
                    "Substantial multiple-comparisons burden. Any positive result needs a "
                    "correction and should be treated as a lead requiring fresh "
                    "out-of-sample data rather than a finding.",
                )
            elif n > 5:
                f.note(f"{n} configurations tried -- correction applied in the report.")
        except (TypeError, ValueError):
            f.caveat("Search count unparseable", f"got {tried!r}")

    if str(split).lower() == "random":
        f.block(
            "Random train/test split",
            "Adjacent opportunities are serially correlated, so a random split puts a "
            "window's near-duplicate neighbour in train and the window itself in test. "
            "The reported result is not trustworthy. Re-split by time.",
        )
    elif unknown(split) or str(split).lower() == "none":
        f.caveat("No holdout split", "The analysis will construct one by time. The client's "
                                     "reported figure is in-sample and not comparable.")

    if peeked is True:
        f.caveat(
            "Holdout already examined",
            "It is now part of the training set. A fresh out-of-sample period is required "
            "before any positive result can be relied on. Normal and recoverable -- but it "
            "must be stated.",
        )
    if not unknown(posthoc):
        f.caveat(f"Post-hoc rule: {posthoc}",
                 "Chosen after seeing results, so it needs validating on data it was not "
                 "derived from.")


def check_claim_plausibility(m, f):
    metric = str(get(m, "claim", "metric", default="")).lower()
    value = get(m, "claim", "value")
    n = get(m, "claim", "n_trades")

    if not unknown(n):
        try:
            if int(n) < 400:
                f.caveat(
                    f"Only {n} trades in the claim",
                    "Below roughly 400 the holdout is too small to conclude much. The "
                    "detection floor will likely exceed any plausible edge.",
                )
        except (TypeError, ValueError):
            pass

    if unknown(value):
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        return

    if "win_rate" in metric and v > 0.9:
        f.note(
            f"Reported win rate of {v:.0%} is not itself remarkable -- in a market that "
            "prices probability, entering only near-certain positions produces a high win "
            "rate at a correspondingly high price. The report will lead with mean P&L."
        )
    if "pnl" in metric and v > 0:
        f.note(f"Reported {metric} of {v}. Magnitude will be sanity-checked against stake "
               f"size: an implausible per-trade return is a diagnostic pointing at code, "
               f"not a discovery.")


def check_data(m, base, f):
    path = base / str(get(m, "data", "file", default="opps.csv"))
    if not path.exists():
        f.block("Data file missing", f"{path} not found")
        return

    time_col = get(m, "data", "time_col")
    outcome_col = get(m, "data", "outcome_col")
    mkt_col = get(m, "data", "market_prob_col")
    features = get(m, "data", "features", default=[]) or []

    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        rows = list(reader)

    required = [c for c in [time_col, outcome_col] + list(features) if c]
    missing = [c for c in required if c not in cols]
    if missing:
        f.block("Declared columns absent from the data",
                f"missing {missing}; file has {cols}")
        return

    f.note(f"{len(rows)} rows, {len(cols)} columns.")

    if len(rows) < 400:
        f.caveat(f"Only {len(rows)} rows",
                 "Too few for a holdout to mean much. Expect the detection floor to "
                 "dominate the verdict.")

    outcomes = []
    for r in rows:
        try:
            outcomes.append(int(float(r[outcome_col])))
        except (TypeError, ValueError):
            pass
    if outcomes:
        ones = sum(1 for o in outcomes if o == 1)
        if ones in (0, len(outcomes)):
            f.block("Outcome column is constant", "nothing to predict")
        elif min(ones, len(outcomes) - ones) / len(outcomes) < 0.02:
            f.caveat("Severe class imbalance", "intervals will be unreliable")
        else:
            f.note(f"Outcome balance: {ones/len(outcomes):.1%} positive.")

    if mkt_col and mkt_col in cols and outcomes:
        probs = []
        for r in rows:
            try:
                probs.append(float(r[mkt_col]))
            except (TypeError, ValueError):
                probs.append(None)
        paired = [(p, o) for p, o in zip(probs, outcomes) if p is not None]
        if paired:
            if any(p < 0 or p > 1 for p, _ in paired):
                f.block("Market probability outside [0,1]",
                        "wrong column, or odds not converted to probability")
            else:
                brier = sum((p - o) ** 2 for p, o in paired) / len(paired)
                base_rate = statistics.mean(o for _, o in paired)
                brier_base = sum((base_rate - o) ** 2 for _, o in paired) / len(paired)
                if brier < brier_base:
                    f.note(f"Market price is informative (Brier {brier:.4f} vs "
                           f"{brier_base:.4f} for the base rate).")
                else:
                    f.block(
                        "Market price carries no information",
                        f"Brier {brier:.4f} is no better than always predicting the base "
                        f"rate ({brier_base:.4f}). Almost always the wrong column, or "
                        f"misaligned rows.",
                    )
    elif not mkt_col:
        f.caveat(
            "No market price column",
            "The model can only be compared against the base rate, which is a much weaker "
            "claim and does not imply tradeability. Ask whether a price exists.",
        )

    ts = []
    for r in rows:
        try:
            ts.append(float(r[time_col]))
        except (TypeError, ValueError):
            pass
    if ts:
        dupes = len(ts) - len(set(ts))
        if dupes:
            f.caveat(f"{dupes} duplicate timestamps",
                     "If these are the same event counted twice, every interval is too narrow.")
        if ts != sorted(ts):
            f.note("Rows not pre-sorted by time; the analysis sorts them.")


# ---------------------------------------------------------------- report


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    path = Path(args.manifest)
    manifest = yaml.safe_load(path.read_text()) or {}
    base = path.parent

    f = Findings()
    check_timing(manifest, f)
    check_bars(manifest, f)
    check_settlement(manifest, f)
    check_costs(manifest, f)
    check_search(manifest, f)
    check_claim_plausibility(manifest, f)
    check_data(manifest, base, f)

    client = get(manifest, "client", default="(unnamed)")
    claim = get(manifest, "claim", "description", default="(no description)")
    print(f"\nINTAKE TRIAGE — {client}")
    print(f"claim: {claim}\n")

    if f.blockers:
        print(f"BLOCKED — {len(f.blockers)} defect(s) that invalidate any result\n")
        for i, (title, detail) in enumerate(f.blockers, 1):
            print(f"  {i}. {title}")
            for line in _wrap(detail):
                print(f"     {line}")
            print()
    elif f.caveats:
        print(f"QUALIFIED — runnable, {len(f.caveats)} caveat(s) must appear in the report\n")
    else:
        print("CLEAN — runnable as submitted\n")

    if f.caveats:
        print("Caveats:\n")
        for i, (title, detail) in enumerate(f.caveats, 1):
            print(f"  {i}. {title}")
            for line in _wrap(detail):
                print(f"     {line}")
            print()

    if f.notes:
        print("Notes:")
        for n in f.notes:
            for j, line in enumerate(_wrap(n)):
                print(f"  {'- ' if j == 0 else '  '}{line}")
        print()

    if f.blockers:
        print("Next step: send the client the blockers above. Do not begin analysis --\n"
              "these defects cannot be corrected downstream.")
        sys.exit(2)

    print("Next step:\n  python run_engagement.py --manifest", args.manifest)


def _wrap(text, width=76):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


if __name__ == "__main__":
    main()
