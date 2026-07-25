#!/usr/bin/env python3
"""
Generic edge validator: given per-opportunity features, a market price, and a
realized outcome, decide whether a signal has edge that survives costs.

Runs the full discipline in one pass:
  1. hygiene checks (class balance, time ordering, duplicate timestamps)
  2. logistic fit with the MARKET price as a fixed offset, so coefficients
     measure departure from the market rather than from a coin flip
  3. train / validate / holdout split BY TIME, holdout touched once
  4. threshold chosen on the lower confidence bound, not the point estimate
  5. positive control -- inject a known edge, confirm the pipeline finds it
  6. power sweep -- report the smallest edge this dataset could detect
  7. friction sensitivity -- at what cost does any edge disappear

Pure stdlib. No numpy/scipy/sklearn.

INPUT: a CSV, one row per opportunity, sorted or sortable by time.
  required: --time-col, --outcome-col (1 = the "yes"/up side won)
  usual:    --market-prob-col (the market's implied probability of that side)
  plus:     --features a,b,c

Two payout modes:
  market  (default)  you buy the side you favour at its market price; a win
                     pays 1 per contract. This is prediction markets, sports
                     books quoted as probabilities, any binary contract.
  returns            you supply --return-col giving the P&L of a unit long
                     position; the signal picks long or short. This is
                     directional trading of an instrument.

USAGE
  python validate_edge.py --data opps.csv --time-col ts --outcome-col won \\
      --market-prob-col mkt_p --features z_move,vol_ratio --friction 0.01

WHY THE OFFSET MATTERS (the single most common way people fool themselves):
a model that predicts outcomes well is worth nothing if the market already
predicts them equally well. Putting logit(market_prob) in as a fixed offset
means every fitted coefficient answers "what does this feature add ON TOP OF
the price?" -- and a coefficient of ~0 is then a real, publishable finding
rather than a failure to fit.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import sys
from pathlib import Path


# ----------------------------------------------------------------- stats


def logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def wilson_ci(wins: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 1.0)
    phat = wins / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def bootstrap_ci(values, n_resamples: int = 2000, ci: float = 0.95, seed: int = 11):
    """Percentile bootstrap on the mean. Used instead of a t-interval because
    per-trade P&L in binary markets is wildly non-normal -- a few large wins
    and many small losses, or the reverse."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    boot = []
    for _ in range(n_resamples):
        s = [values[rng.randrange(n)] for _ in range(n)]
        boot.append(sum(s) / n)
    boot.sort()
    return (boot[int((1 - ci) / 2 * n_resamples)],
            boot[min(n_resamples - 1, int((1 + ci) / 2 * n_resamples))])


def solve(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        for r in range(n):
            if r == col:
                continue
            f = M[r][col] / M[col][col]
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit_logistic(rows, features, use_offset: bool, l2: float = 1.0, iters: int = 60):
    """Newton-IRLS with L2 and an optional fixed offset.

    The Hessian below carries its own minus sign, so the update is
    beta - H^-1 g. Flipping that sign does not error -- it diverges to
    absurd coefficients (1e15) that still "fit", which is why the
    convergence guard at the end matters."""
    k = len(features) + 1
    beta = [0.0] * k
    X = [[1.0] + [r["f"][name] for name in features] for r in rows]
    y = [r["y"] for r in rows]
    off = [logit(r["mkt"]) if use_offset else 0.0 for r in rows]

    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for xi, yi, oi in zip(X, y, off):
            p = sigmoid(oi + sum(b * x for b, x in zip(beta, xi)))
            w = max(p * (1 - p), 1e-8)
            r_ = yi - p
            for a in range(k):
                grad[a] += r_ * xi[a]
                for c in range(k):
                    hess[a][c] -= w * xi[a] * xi[c]
        for a in range(k):
            grad[a] -= l2 * beta[a]
            hess[a][a] -= l2
        step = solve(hess, grad)
        if step is None:
            break
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-9:
            break

    if max(abs(b) for b in beta) > 1e6:
        raise SystemExit("fit diverged (|coef| > 1e6). Check for a feature that "
                         "perfectly separates the outcome, or a constant column.")
    return beta


def predict(beta, row, features, use_offset: bool):
    x = [1.0] + [row["f"][name] for name in features]
    base = logit(row["mkt"]) if use_offset else 0.0
    return sigmoid(base + sum(b * xi for b, xi in zip(beta, x)))


# ------------------------------------------------------------- simulation


def simulate(rows, beta, features, threshold, friction, mode, stake=1.0, use_offset=True):
    """Trade when the model disagrees with the market by more than
    `threshold`. `friction` is charged as a worse entry price, which is what
    crossing a spread actually costs you -- not as a flat fee, because in a
    binary market the cost scales with how far from 0.5 you enter."""
    out = []
    for r in rows:
        p = predict(beta, r, features, use_offset)
        diff = p - (r["mkt"] if use_offset else 0.5)
        if abs(diff) < threshold:
            continue
        side_yes = diff > 0

        if mode == "returns":
            ret = r["ret"] if side_yes else -r["ret"]
            pnl = stake * (ret - friction)
            won = pnl > 0
        else:
            entry = (r["mkt"] if side_yes else 1 - r["mkt"]) + friction
            if not (0.005 < entry < 0.995):
                continue
            won = (r["y"] == 1) if side_yes else (r["y"] == 0)
            pnl = stake * (1.0 / entry - 1.0) if won else -stake
        out.append({"pnl": pnl, "won": won})
    return out


def describe(trades, label, quiet=False):
    if not trades:
        if not quiet:
            print(f"  {label:<30} no trades")
        return None
    pnls = [t["pnl"] for t in trades]
    mean = sum(pnls) / len(pnls)
    lo, hi = bootstrap_ci(pnls)
    verdict = "POSITIVE" if lo > 0 else ("negative" if hi < 0 else "flat")
    if not quiet:
        wins = sum(1 for t in trades if t["won"])
        print(f"  {label:<30} n={len(trades):>5}  mean={mean:+.4f} "
              f"[{lo:+.4f},{hi:+.4f}]  win={wins/len(trades):.1%}  {verdict}")
    return {"n": len(trades), "mean": mean, "lo": lo, "hi": hi, "verdict": verdict}


def split(rows):
    n = len(rows)
    return rows[:n // 2], rows[n // 2:3 * n // 4], rows[3 * n // 4:]


def pipeline(rows, features, friction, mode, thresholds, min_trades, use_offset, quiet=True):
    """Fit, select on validate, evaluate once on holdout. Returns the holdout
    result and the median |model - market| deviation."""
    train, val, hold = split(rows)
    beta = fit_logistic(train, features, use_offset)
    ref = [(r["mkt"] if use_offset else 0.5) for r in val]
    devs = sorted(abs(predict(beta, r, features, use_offset) - m) for r, m in zip(val, ref))
    med_dev = devs[len(devs) // 2] if devs else 0.0

    best = None
    for t in thresholds:
        res = describe(simulate(val, beta, features, t, friction, mode, use_offset=use_offset),
                       f"threshold {t:.3f}", quiet=quiet)
        if res and res["n"] >= min_trades and res["lo"] > 0:
            if best is None or res["lo"] > best[1]["lo"]:
                best = (t, res)
    if best is None:
        return None, None, med_dev, beta
    hres = describe(simulate(hold, beta, features, best[0], friction, mode, use_offset=use_offset),
                    f"holdout thr {best[0]:.3f}", quiet=quiet)
    return best[0], hres, med_dev, beta


def reroll(rows, edge, features, seed, mode):
    """Positive control: rebuild outcomes from a world mispriced by `edge` in
    the direction of the first feature. Real prices, real features, known
    answer. Without this, 'no edge found' could equally mean 'this code
    cannot find edge', and you would never know which."""
    rng = random.Random(seed)
    key = features[0]
    out = []
    for r in rows:
        direction = 1 if r["f"][key] > 0 else -1
        if mode == "returns":
            out.append({**r, "ret": r["ret"] + direction * edge})
        else:
            p = min(0.999, max(0.001, r["mkt"] + direction * edge))
            out.append({**r, "y": 1 if rng.random() < p else 0})
    return out


# -------------------------------------------------------------------- io


def load(path, time_col, outcome_col, mkt_col, ret_col, features):
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ([time_col, outcome_col] + features) if c not in reader.fieldnames]
        if missing:
            raise SystemExit(f"missing column(s): {missing}\navailable: {reader.fieldnames}")
        for rec in reader:
            try:
                row = {
                    "t": float(rec[time_col]),
                    "y": int(float(rec[outcome_col])),
                    "mkt": float(rec[mkt_col]) if mkt_col else 0.5,
                    "ret": float(rec[ret_col]) if ret_col else 0.0,
                    "f": {name: float(rec[name]) for name in features},
                }
            except (ValueError, TypeError, KeyError):
                continue
            rows.append(row)
    rows.sort(key=lambda r: r["t"])
    return rows


def hygiene(rows, use_offset):
    print("--- data hygiene ---")
    n = len(rows)
    ones = sum(r["y"] for r in rows)
    print(f"  rows: {n}   outcome=1: {ones} ({ones/n:.1%})")
    if ones == 0 or ones == n:
        raise SystemExit("outcome column is constant -- nothing to predict")
    if min(ones, n - ones) / n < 0.02:
        print("  WARNING: severe class imbalance; CIs will be unreliable")

    dupes = n - len({r["t"] for r in rows})
    if dupes:
        print(f"  WARNING: {dupes} duplicate timestamps -- if these are the same "
              f"event counted twice, every interval below is too narrow")
    if use_offset:
        probs = [r["mkt"] for r in rows]
        if min(probs) < 0 or max(probs) > 1:
            raise SystemExit("market-prob column is outside [0,1]")
        brier = sum((r["mkt"] - r["y"]) ** 2 for r in rows) / n
        base = statistics.mean(r["y"] for r in rows)
        brier_base = sum((base - r["y"]) ** 2 for r in rows) / n
        print(f"  market Brier {brier:.4f} vs always-base-rate {brier_base:.4f} "
              f"({'market is informative' if brier < brier_base else 'market adds nothing -- check the column'})")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--time-col", required=True)
    ap.add_argument("--outcome-col", required=True)
    ap.add_argument("--market-prob-col", default=None,
                    help="market's implied probability. Omit only if there is genuinely "
                         "no market price -- then the model is measured against the base "
                         "rate, which is a much weaker claim.")
    ap.add_argument("--return-col", default=None, help="for --mode returns")
    ap.add_argument("--features", required=True, help="comma-separated column names")
    ap.add_argument("--mode", choices=["market", "returns"], default="market")
    ap.add_argument("--friction", type=float, default=0.01,
                    help="cost per entry, in the same units as price/return. MEASURE this; "
                         "do not guess it (default 0.01)")
    ap.add_argument("--thresholds", default="0.005,0.01,0.02,0.03,0.05,0.08")
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()

    features = [s.strip() for s in args.features.split(",") if s.strip()]
    use_offset = args.market_prob_col is not None
    thresholds = [float(x) for x in args.thresholds.split(",")]

    rows = load(Path(args.data), args.time_col, args.outcome_col,
                args.market_prob_col, args.return_col, features)
    if len(rows) < 400:
        print(f"WARNING: only {len(rows)} rows. Below ~400 the holdout is too small "
              f"to conclude anything; treat everything below as exploratory.\n")
    if not rows:
        raise SystemExit("no usable rows")

    hygiene(rows, use_offset)
    if not use_offset:
        print("NOTE: no --market-prob-col given, so the model is being compared against\n"
              "the base rate, not against a price. 'Beats the base rate' does NOT mean\n"
              "tradeable -- if a market exists, supply its price.\n")

    train, val, hold = split(rows)
    print(f"split by time: train {len(train)} / validate {len(val)} / holdout {len(hold)}")
    print(f"friction per entry: {args.friction}\n")

    beta = fit_logistic(train, features, use_offset)
    print("--- coefficients (departure from the market price) ---" if use_offset
          else "--- coefficients (vs base rate) ---")
    print(f"  {'intercept':<24} {beta[0]:+.5f}")
    for name, b in zip(features, beta[1:]):
        print(f"  {name:<24} {b:+.5f}")

    ref = [(r["mkt"] if use_offset else 0.5) for r in val]
    devs = sorted(abs(predict(beta, r, features, use_offset) - m) for r, m in zip(val, ref))
    med = devs[len(devs) // 2]
    print(f"\n|model - market| on validate: median {med:.4f}, p90 {devs[int(0.9*len(devs))]:.4f}")
    if med < args.friction:
        print(f"  NOTE: the model's typical disagreement with the market ({med:.4f}) is\n"
              f"  smaller than the cost of acting on it ({args.friction}). Even if every\n"
              f"  disagreement were correct, this would not pay.")

    print("\n--- validate (look freely) ---")
    best = None
    for t in thresholds:
        res = describe(simulate(val, beta, features, t, args.friction, args.mode,
                                use_offset=use_offset), f"threshold {t:.3f}")
        if res and res["n"] >= args.min_trades and res["lo"] > 0:
            if best is None or res["lo"] > best[1]["lo"]:
                best = (t, res)

    print("\n--- holdout (once) ---")
    if best is None:
        print("  Nothing produced a positive lower bound on validate, so the holdout\n"
              "  stays unused. Spending it now would just be another draw from noise.")
    else:
        describe(simulate(hold, beta, features, best[0], args.friction, args.mode,
                          use_offset=use_offset), f"HOLDOUT thr {best[0]:.3f}")

    print("\n--- zero-friction check ---")
    describe(simulate(val, beta, features, thresholds[0], 0.0, args.mode,
                      use_offset=use_offset), "validate, no friction")
    print("  If this is also flat, the problem is the signal, not execution.")

    print("\n--- detection floor: what could this dataset have found? ---")
    print(f"  {'injected':>10}  {'|model-mkt|':>12}  {'holdout':>34}")
    floor = None
    for edge in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12):
        sample = rows if edge == 0 else reroll(rows, edge, features, args.seed, args.mode)
        try:
            thr, hres, dev, _ = pipeline(sample, features, args.friction, args.mode,
                                         thresholds, args.min_trades, use_offset)
        except SystemExit:
            continue
        if hres is None:
            verdict = "nothing passed validate"
        else:
            verdict = f"{hres['mean']:+.4f} [{hres['lo']:+.4f},{hres['hi']:+.4f}] {hres['verdict']}"
            if hres["verdict"] == "POSITIVE" and floor is None and edge > 0:
                floor = edge
        label = "REAL" if edge == 0 else f"{edge:.3f}"
        print(f"  {label:>10}  {dev:>12.4f}  {verdict:>34}")

    print()
    if floor is None:
        print("DETECTION FLOOR: not reached even at the largest injected edge. This\n"
              "dataset cannot confirm an edge of ANY size -- it is too small, too noisy,\n"
              "or the features are unrelated to the outcome. Get more data before\n"
              "concluding anything, in either direction.")
    else:
        print(f"DETECTION FLOOR: ~{floor:.3f}. Edges smaller than this are invisible here.")
        if floor > args.friction * 2:
            need = (floor / max(args.friction, 1e-9)) ** 2
            print(f"  That floor is well above your {args.friction} cost, so the tradeable\n"
                  f"  range ({args.friction:.3f} to {floor:.3f}) is entirely unmeasurable with\n"
                  f"  this much data. Confirming an edge at the cost threshold needs roughly\n"
                  f"  {need:.0f}x more rows. Decide whether that data is obtainable BEFORE\n"
                  f"  spending more time searching.")


if __name__ == "__main__":
    main()
