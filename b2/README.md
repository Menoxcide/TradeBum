# B2 — validation engagements

Intake and delivery pipeline for paid validation work. Someone sends a
backtest; this turns it into a report.

## Running one

```bash
# 1. client fills in INTAKE.md's template, sends manifest.yaml + their CSV
mkdir -p engagements/acme && cd engagements/acme   # drop both files here

# 2. triage before spending any time on analysis
python b2/intake_check.py --manifest engagements/acme/manifest.yaml

# 3. if it isn't blocked, run it
python b2/run_engagement.py --manifest engagements/acme/manifest.yaml
#    -> engagements/acme/report.md
```

Triage exits non-zero when blocked, so it can gate the run in a script.

## Why intake carries the value

The statistics are the easy part and largely automated. What distinguishes a
useful engagement from an expensive one is asking the questions a client will
not volunteer — because every artifact found while building this was an intake
question, not an analysis result:

| question | what it catches |
|---|---|
| how stale was the fill price? | 96% of a 21%/trade result |
| bars stamped at open or close? | 60s of future price per decision |
| which feed settles the contract? | wrong grading on marginal trades |
| costs measured or assumed? | the free parameter that sets the answer |
| how many configurations tried? | the one-in-three spurious winner |
| split by time or randomly? | leakage across the boundary |

Several of these cannot be corrected downstream. If the fill price predates the
signal, no amount of careful statistics recovers the truth — the data has to be
rebuilt. Finding that out in the first ten minutes rather than the fourth hour
is most of what intake is for, and it is why `intake_check.py` runs before
anything else and refuses to proceed when blocked.

## What the report leads with

The verdict, then the **detection floor**. Most analyses omit the floor, and it
is frequently the most useful number: it separates "there is no edge here" from
"this data cannot tell you either way", which look identical in output and imply
completely different next steps. A client whose floor sits above their cost
should be collecting data, not iterating strategies — and telling them that
early is worth more than another sweep.

A negative verdict is the product, not a failure to deliver one. Most
submissions will be negative; that is the base rate for retail strategy code,
not a reflection on the client. The report is written to be read by someone
hoping for a yes.

## Client data is confidential

`engagements/` is gitignored apart from the template. Client data must not be
committed — not to this repo, not to any public one. If a submission is used as
a worked example, regenerate it synthetically first.

## Pricing notes

Per engagement, not hourly — the work is bounded once the pipeline exists and
clients are buying a decision, not time. Bill triage separately if the
submission is blocked: identifying the defect *is* the deliverable in that case,
and it is often the most valuable one, since it stops them trading a broken
result.

Decline engagements where the client wants a specific answer rather than an
answer. Reputation here is the whole asset, and one laundered positive destroys
it.

## Files

```
INTAKE.md          client-facing questionnaire; the template lives at the bottom
intake_check.py    triage: BLOCKED / QUALIFIED / CLEAN, exits 2 when blocked
run_engagement.py  orchestrates triage -> validator -> report.md
engagements/       one directory per client (gitignored)
```

The validator itself is `.claude/skills/edge-validation/scripts/validate_edge.py`,
published at github.com/Menoxcide/edge-validation.
