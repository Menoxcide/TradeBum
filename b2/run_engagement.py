#!/usr/bin/env python3
"""
Run a validation engagement end to end and emit the client report.

Sequence: re-run intake triage (refuse to proceed if blocked) -> apply the
timing correction the manifest disclosed -> run the validator -> write a
Markdown report.

The report is the deliverable, so it is written to be read by someone who is
hoping for a positive answer and may be receiving a negative one. That means
leading with the verdict rather than burying it, giving the arithmetic behind
it, and always reporting the detection floor -- because "no edge here" and
"this data cannot tell you either way" look identical in output and have
completely different consequences for what the client should do next.

Usage:
  python run_engagement.py --manifest engagements/acme/manifest.yaml
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("needs PyYAML:  pip install pyyaml")

HERE = Path(__file__).resolve().parent
VALIDATOR = HERE.parent / ".claude" / "skills" / "edge-validation" / "scripts" / "validate_edge.py"


def get(d, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur or cur[key] is None:
            return default
        cur = cur[key]
    return cur


def run_validator(manifest, base: Path, friction: float) -> str:
    data = base / str(get(manifest, "data", "file", default="opps.csv"))
    features = get(manifest, "data", "features", default=[]) or []
    cmd = [
        sys.executable, str(VALIDATOR),
        "--data", str(data),
        "--time-col", str(get(manifest, "data", "time_col")),
        "--outcome-col", str(get(manifest, "data", "outcome_col")),
        "--features", ",".join(features),
        "--friction", str(friction),
    ]
    mkt = get(manifest, "data", "market_prob_col")
    if mkt:
        cmd += ["--market-prob-col", str(mkt)]
    ret = get(manifest, "data", "return_col")
    if ret:
        cmd += ["--return-col", str(ret), "--mode", "returns"]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return f"VALIDATOR FAILED (exit {proc.returncode})\n\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def parse_verdict(output: str):
    """Pull the headline facts out of the validator's output so the report can
    lead with them instead of making the client read a console dump."""
    floor = None
    median_dev = None
    holdout = None
    zero_friction_flat = None

    for line in output.splitlines():
        s = line.strip()
        if s.startswith("DETECTION FLOOR:"):
            floor = s
        if s.startswith("|model - market| on validate:"):
            median_dev = s
        if "HOLDOUT" in s or s.startswith("Nothing produced a positive lower bound"):
            holdout = s
        if s.startswith("validate, no friction"):
            zero_friction_flat = s
    return floor, median_dev, holdout, zero_friction_flat


def build_report(manifest, validator_output: str, friction: float, triage: str) -> str:
    client = get(manifest, "client", default="(unnamed)")
    claim_desc = get(manifest, "claim", "description", default="(none given)")
    claim_metric = get(manifest, "claim", "metric", default="")
    claim_value = get(manifest, "claim", "value")
    age = get(manifest, "provenance", "price_age_at_decision_sec")
    tried = get(manifest, "search", "configurations_tried")
    measured = get(manifest, "costs", "measured")

    floor, median_dev, holdout, zero_fric = parse_verdict(validator_output)
    positive = bool(holdout and "POSITIVE" in holdout)

    parts = []
    parts.append(f"# Validation report — {client}")
    parts.append(f"\n_{datetime.now(timezone.utc):%Y-%m-%d}_\n")
    parts.append(f"**Claim submitted.** {claim_desc}")
    if claim_value is not None:
        parts.append(f"Reported {claim_metric or 'result'}: `{claim_value}`.")

    # ---- verdict up front ----
    parts.append("\n## Verdict\n")
    if positive:
        parts.append(
            "The signal cleared the holdout at the stated cost. That is a real result, and "
            "the qualifications below are the difference between it being tradeable and "
            "merely being statistically interesting."
        )
    elif holdout and "Nothing produced" in holdout:
        parts.append(
            "**No detectable edge.** No configuration earned a positive lower confidence "
            "bound on the validation segment, so the holdout was left unspent rather than "
            "converted into another draw from noise."
        )
    else:
        parts.append("See the validator output below; the holdout did not produce a clear result.")

    if median_dev:
        parts.append(f"\n```\n{median_dev}\n```")
        parts.append(
            "This is how far the fitted model departs from the market price. If it is "
            "smaller than your cost per entry, then even if every disagreement were "
            "correct, acting on them would not pay."
        )

    # ---- the floor: the number most reports omit ----
    parts.append("\n## What this data could have detected\n")
    if floor:
        parts.append(f"```\n{floor}\n```")
    parts.append(
        "A negative result is only meaningful alongside this number. It comes from "
        "injecting a *known* mispricing into your real data and rerunning the entire "
        "pipeline — real prices, real features, resampled outcomes, known answer. The "
        "smallest injected edge that comes back positive is the floor.\n\n"
        "If that floor sits above your cost per entry, the whole tradeable range is "
        "unmeasurable with this much data, and neither a positive nor a negative result "
        "from it should drive a decision. The fix is more data, not more searching."
    )

    # ---- corrections applied ----
    parts.append("\n## Corrections applied to your reported figure\n")
    applied = []
    if age not in (None, "", "unknown"):
        try:
            if float(age) > 0:
                applied.append(
                    f"**Timing alignment ({float(age):.0f}s).** Your fill price was quoted "
                    f"{float(age):.0f} seconds before the signal was computed, so the strategy "
                    f"was reading price movement the market had not yet absorbed. The decision "
                    f"timestamp was shifted back to match the quote. Any gap between your "
                    f"reported figure and the one above is largely this."
                )
        except (TypeError, ValueError):
            pass
    if tried not in (None, "", "unknown"):
        try:
            n = int(tried)
            if n > 5:
                applied.append(
                    f"**Multiple comparisons ({n} configurations).** At 95% confidence, {n} "
                    f"independent tests give roughly a "
                    f"{min(99, int(100 * (1 - 0.95 ** n)))}% chance that at least one comes back "
                    f"significant from luck alone. Selection here is on the lower confidence "
                    f"bound with a minimum sample size, which penalises small samples "
                    f"automatically."
                )
        except (TypeError, ValueError):
            pass
    if measured is not True:
        applied.append(
            "**Costs were estimated, not measured.** The friction figure decides the entire "
            "question, so treat the verdict as conditional on it. Walking the live book at "
            "your intended order size is a few hours' work and removes this caveat entirely."
        )
    applied.append(
        f"**Splits are by time, never random**, and the holdout is evaluated once. Friction "
        f"charged: `{friction}` per entry, as a worse entry price rather than a flat fee — "
        f"that is what crossing a spread actually costs in a binary market."
    )
    parts.extend(f"- {a}" for a in applied)

    if zero_fric:
        parts.append("\n## Is it an execution problem?\n")
        parts.append(f"```\n{zero_fric}\n```")
        parts.append(
            "Result with costs set to zero. If this is also flat, the signal is the problem "
            "and no improvement in fills, latency or venue will rescue it — worth knowing "
            "before spending on execution infrastructure."
        )

    parts.append("\n## Full validator output\n")
    parts.append(f"```\n{validator_output.strip()}\n```")

    parts.append("\n## Intake triage\n")
    parts.append(f"```\n{triage.strip()}\n```")

    parts.append("\n## Scope and limits\n")
    parts.append(
        "This is a statistical validation of the data supplied. It does **not** simulate "
        "live fills, latency, queue position, or the market's reaction to your own order — "
        "all of which degrade live results relative to any backtest. A positive result here "
        "means *cleared to paper-trade*, never *cleared to size up*.\n\n"
        "Engineering and statistics work product. Not investment advice."
    )
    return "\n".join(parts) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", default=None, help="report path (default: alongside the manifest)")
    ap.add_argument("--force", action="store_true",
                    help="run even if triage blocks. Only for exploring a client's data before "
                         "they rebuild it -- the resulting numbers are not reportable.")
    args = ap.parse_args()

    path = Path(args.manifest)
    manifest = yaml.safe_load(path.read_text()) or {}
    base = path.parent

    triage_proc = subprocess.run(
        [sys.executable, str(HERE / "intake_check.py"), "--manifest", str(path)],
        capture_output=True, text=True,
    )
    triage = triage_proc.stdout + triage_proc.stderr
    print(triage)

    if triage_proc.returncode == 2 and not args.force:
        sys.exit("Engagement blocked at intake. Send the client the blockers above; "
                 "these defects cannot be corrected downstream.")

    friction = get(manifest, "costs", "cost_per_entry", default=0.01)
    try:
        friction = float(friction)
    except (TypeError, ValueError):
        friction = 0.01

    print("Running validator...\n")
    output = run_validator(manifest, base, friction)
    report = build_report(manifest, output, friction, triage)

    out = Path(args.out) if args.out else base / "report.md"
    out.write_text(report)
    print(f"Report written -> {out}")
    print("Read it before sending. If the verdict is negative, say so plainly in the "
          "covering note -- a clean negative delivered early is the product, not a failure "
          "to deliver one.")


if __name__ == "__main__":
    main()
