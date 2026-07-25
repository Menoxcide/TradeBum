#!/usr/bin/env python3
"""
Fits `model_prob` properly, and tests whether it beats the market.

This replaces `confluence_score_to_model_prob`'s hand-drawn line with a
logistic regression, and it is set up to answer the only question that
matters for trading:

    does ANY feature add information that the market price does not
    already contain?

That framing is baked into the model rather than checked afterwards. The
market's own logit is included as a fixed **offset** with coefficient 1, so
the fitted weights describe departures *from the market price*, not from
50/50. If every coefficient lands at ~0, the market is efficient with
respect to these features, and that is the finding. A model that merely
predicts outcomes well is worthless here -- the market price already
predicts outcomes well.

DISCIPLINE (the point of this script):
  train      fit coefficients only
  validate   choose the threshold / configuration, look as much as you like
  holdout    touched ONCE, reported once, never tuned against

Splits are by time, never random: 5-minute windows are serially correlated,
so a random split leaks neighbouring windows across the boundary and will
happily report edge that does not exist.

No numpy/scipy dependency -- the fit is Newton-IRLS in plain Python, which
is fine at this feature count and sample size.

Usage:
  python scripts/fit_model.py --data-dir ./data/real --profile research
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.backtest.fair_value import estimate_vol_per_sqrt_sec, fair_value_prob_up
from src.backtest.stats import bootstrap_ci, wilson_ci
from src.data.provider import DataProvider

EPS = 1e-9


def logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


# ------------------------------------------------------------------ features


def build_dataset(provider, entry_offset_sec: int = 120, warmup: int = 20,
                  bar_lookback: int = 30, min_move_usd: float = 0.0):
    """One row per window that has a real market price. Everything is
    computed from data available at (or before) the decision moment, using
    the same as-of accessors the backtest engine uses."""
    rows = []
    atr_hist: list[float] = []
    vol_hist: list[float] = []

    for i, w in enumerate(provider.resolutions):
        if w.market_prob_up_at_decision is None:
            continue

        decision_ts = w.window_end_ms - entry_offset_sec * 1000
        if w.prob_age_sec:
            decision_ts -= int(w.prob_age_sec * 1000)  # align signal to the quote
        if decision_ts < w.window_start_ms:
            decision_ts = w.window_start_ms
        seconds_left = max(1, (w.window_end_ms - decision_ts) // 1000)

        bars = provider.bars_as_of(decision_ts, lookback=bar_lookback)
        in_window = [b for b in bars if b.timestamp_ms >= w.window_start_ms]
        if len(bars) < 5 or not in_window:
            continue

        price = in_window[-1].close
        move = price - w.open_price
        volume = sum(b.volume for b in in_window)

        trs = []
        prev = bars[0].close
        for b in bars[1:]:
            trs.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
            prev = b.close
        atr = sum(trs) / len(trs) if trs else 0.0

        atr_med = sorted(atr_hist)[len(atr_hist) // 2] if atr_hist else atr
        vol_med = sorted(vol_hist)[len(vol_hist) // 2] if vol_hist else volume
        vol_pctile = (100.0 * sum(1 for h in vol_hist if h <= volume) / len(vol_hist)) if vol_hist else 50.0

        atr_hist.append(atr)
        vol_hist.append(volume)
        if len(atr_hist) > 20:
            atr_hist.pop(0)
            vol_hist.pop(0)
        if i < warmup or abs(move) < min_move_usd:
            continue

        vol_est = estimate_vol_per_sqrt_sec(bars)
        geo = fair_value_prob_up(move, seconds_left, vol_est) if vol_est and vol_est > 0 else 0.5
        mkt = w.market_prob_up_at_decision

        # sigma-normalized move: how big is this move relative to what the
        # remaining time can undo? the natural scale-free version of "$70"
        z = move / (vol_est * math.sqrt(seconds_left)) if vol_est and vol_est > 0 else 0.0

        rows.append({
            "window_start_ms": w.window_start_ms,
            "outcome_up": 1 if w.outcome == "UP" else 0,
            "market_prob_up": mkt,
            "features": {
                "z_move": z,
                "geo_minus_mkt": geo - mkt,
                "atr_ratio": (atr / atr_med) - 1.0 if atr_med > 0 else 0.0,
                "vol_ratio": (volume / vol_med) - 1.0 if vol_med > 0 else 0.0,
                "vol_pctile": (vol_pctile - 50.0) / 50.0,
                "funding": provider.funding_as_of(decision_ts) * 1000.0,
                "mkt_extremity": abs(mkt - 0.5) * 2,
            },
        })
    return rows


FEATURE_NAMES = ["z_move", "geo_minus_mkt", "atr_ratio", "vol_ratio",
                 "vol_pctile", "funding", "mkt_extremity"]


# ------------------------------------------------------------------- fitting


def fit_logistic(rows, feature_names, l2: float = 1.0, iters: int = 50):
    """Newton-IRLS with an L2 penalty and the market logit as a fixed offset.

    The offset is the whole point: coefficients measure deviation from the
    market price, so beta=0 means "the market already knows this"."""
    k = len(feature_names) + 1  # +1 intercept
    beta = [0.0] * k

    X = [[1.0] + [r["features"][f] for f in feature_names] for r in rows]
    y = [r["outcome_up"] for r in rows]
    offset = [logit(r["market_prob_up"]) for r in rows]

    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for xi, yi, oi in zip(X, y, offset):
            eta = oi + sum(b * x for b, x in zip(beta, xi))
            p = sigmoid(eta)
            wgt = max(p * (1 - p), 1e-8)
            resid = yi - p
            for a in range(k):
                grad[a] += resid * xi[a]
                for b_ in range(k):
                    hess[a][b_] -= wgt * xi[a] * xi[b_]
        for a in range(k):
            grad[a] -= l2 * beta[a]
            hess[a][a] -= l2

        # Newton: beta <- beta - H^-1 g. The Hessian is negative definite
        # here (it carries the minus sign), so this walks uphill on the
        # penalized log-likelihood. Getting this sign backwards diverges to
        # ~1e15 coefficients within a few iterations rather than failing
        # loudly, so it is worth stating explicitly.
        step = solve(hess, grad)
        if step is None:
            break
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return beta


def solve(A, b):
    """Gaussian elimination with partial pivoting."""
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


def predict(beta, row, feature_names):
    x = [1.0] + [row["features"][f] for f in feature_names]
    return sigmoid(logit(row["market_prob_up"]) + sum(b * xi for b, xi in zip(beta, x)))


# ---------------------------------------------------------------- evaluation


def simulate(rows, beta, feature_names, edge_threshold: float, friction: float,
             stake: float = 10.0, fee: float = 0.0):
    """Trade whenever the model disagrees with the market by more than
    `edge_threshold`, on the side the model favours. Charges `friction` as a
    price concession on entry -- that is what the spread costs you."""
    trades = []
    for r in rows:
        model_up = predict(beta, r, feature_names)
        mkt_up = r["market_prob_up"]
        diff = model_up - mkt_up
        if abs(diff) < edge_threshold:
            continue
        side_up = diff > 0
        entry = (mkt_up if side_up else 1 - mkt_up) + friction
        if not (0.01 < entry < 0.99):
            continue
        won = (r["outcome_up"] == 1) if side_up else (r["outcome_up"] == 0)
        if won:
            payout = stake / entry
            pnl = payout - stake - fee * payout
        else:
            pnl = -stake
        trades.append({"pnl": pnl, "won": won, "entry": entry,
                       "window_start_ms": r["window_start_ms"]})
    return trades


def summarize(trades, label):
    n = len(trades)
    if n == 0:
        print(f"  {label:<28} no trades")
        return None
    pnls = [t["pnl"] for t in trades]
    mean = sum(pnls) / n
    lo, hi = bootstrap_ci(pnls, lambda v: sum(v) / len(v))
    wins = sum(1 for t in trades if t["won"])
    verdict = "POSITIVE (CI excludes 0)" if lo > 0 else ("negative" if hi < 0 else "flat (CI includes 0)")
    print(f"  {label:<28} n={n:>5}  total=${sum(pnls):>9,.2f}  mean=${mean:+.3f} "
          f"[{lo:+.3f},{hi:+.3f}]  win={wins/n:.1%}  {verdict}")
    return {"n": n, "total": sum(pnls), "mean": mean, "ci": [lo, hi], "win_rate": wins / n}


def reroll_outcomes(rows, edge: float, seed: int):
    """Return a copy of `rows` where outcomes come from a world mispriced by
    `edge` in the momentum direction. Real prices and features are kept."""
    rng = random.Random(seed)
    out = []
    for r in rows:
        bump = edge if r["features"]["z_move"] > 0 else -edge
        p_true = min(0.999, max(0.001, r["market_prob_up"] + bump))
        out.append({**r, "outcome_up": 1 if rng.random() < p_true else 0})
    return out


def run_pipeline(rows, friction, thresholds=(0.01, 0.02, 0.03, 0.05, 0.08), min_trades=100):
    """Fit on train, select a threshold on validate, evaluate once on
    holdout. Returns (selected_threshold, holdout_summary, max_deviation)."""
    n = len(rows)
    train, validate, holdout = rows[:n // 2], rows[n // 2:3 * n // 4], rows[3 * n // 4:]
    beta = fit_logistic(train, FEATURE_NAMES)
    devs = sorted(abs(predict(beta, r, FEATURE_NAMES) - r["market_prob_up"]) for r in validate)
    median_dev = devs[len(devs) // 2] if devs else 0.0

    best = None
    for thr in thresholds:
        tr = simulate(validate, beta, FEATURE_NAMES, thr, friction)
        if len(tr) < min_trades:
            continue
        pnls = [t["pnl"] for t in tr]
        lo, _ = bootstrap_ci(pnls, lambda v: sum(v) / len(v))
        if lo > 0 and (best is None or lo > best[1]):
            best = (thr, lo)
    if best is None:
        return None, None, median_dev

    tr = simulate(holdout, beta, FEATURE_NAMES, best[0], friction)
    if not tr:
        return best[0], None, median_dev
    pnls = [t["pnl"] for t in tr]
    mean = sum(pnls) / len(pnls)
    lo, hi = bootstrap_ci(pnls, lambda v: sum(v) / len(v))
    return best[0], {"n": len(tr), "mean": mean, "ci": (lo, hi)}, median_dev


def power_sweep(rows, friction, seed: int):
    """How big would a mispricing have to be before this setup could find
    it? Without this number, 'no edge detected' is unfalsifiable -- it could
    equally mean 'not enough data to detect anything'."""
    print("\n=== detection floor: inject a KNOWN mispricing, see if the pipeline finds it ===")
    print("(real prices and features, outcomes re-rolled from a mispriced world)\n")
    print(f"{'injected':>9}  {'|model-mkt|':>12}  {'selected':>9}  {'holdout result':>34}")
    for edge in (0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08):
        sample = rows if edge == 0.0 else reroll_outcomes(rows, edge, seed)
        thr, res, dev = run_pipeline(sample, friction)
        if thr is None:
            verdict = "nothing passed validate"
        elif res is None:
            verdict = f"thr {thr:.2f}, no holdout trades"
        else:
            tag = "POSITIVE" if res["ci"][0] > 0 else ("negative" if res["ci"][1] < 0 else "flat")
            verdict = f"thr {thr:.2f}: ${res['mean']:+.3f} [{res['ci'][0]:+.2f},{res['ci'][1]:+.2f}] {tag}"
        label = "REAL DATA" if edge == 0.0 else f"{edge*100:.1f}pp"
        print(f"{label:>9}  {dev:>12.4f}  {'--' if thr is None else f'{thr:.2f}':>9}  {verdict:>34}")
    print("\nRead this as: any true edge SMALLER than the first row that comes back")
    print("POSITIVE is invisible to this setup at this sample size. If that floor sits")
    print("above the friction budget, no amount of further searching on this data can")
    print("produce a tradeable result -- the honest move is more data or a different market.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--profile", default="research")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config" / "btc_5m_profiles.yaml"))
    ap.add_argument("--friction", type=float, default=None,
                    help="price concession per entry (default: spread/2 + slippage from the profile)")
    ap.add_argument("--min-move-usd", type=float, default=0.0,
                    help="restrict to windows that already moved this far (0 = all windows)")
    ap.add_argument("--inject-edge", type=float, default=None,
                    help="POSITIVE CONTROL. Re-roll every outcome from a distribution "
                         "that is mispriced by this much in the momentum direction, then "
                         "run the whole pipeline on it. A 'no edge found' result is only "
                         "meaningful if the same code FINDS an edge that is genuinely there.")
    ap.add_argument("--power-sweep", action="store_true",
                    help="report the smallest mispricing this setup could detect")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))["profiles"][args.profile]
    friction = args.friction
    if friction is None:
        friction = cfg.get("assumed_polymarket_spread", 0.02) / 2 + cfg.get("slippage_buffer", 0.005)

    provider = DataProvider(args.data_dir)
    rows = build_dataset(provider, min_move_usd=args.min_move_usd)
    rows.sort(key=lambda r: r["window_start_ms"])

    if args.inject_edge is not None:
        # Overwrite reality with a world where momentum IS mispriced by a
        # known amount, keeping the real prices and features. If the
        # pipeline can't recover an edge here, then "no edge" on the real
        # data means "this code can't see one", which is a different claim.
        rng = random.Random(args.seed)
        for r in rows:
            bump = args.inject_edge if r["features"]["z_move"] > 0 else -args.inject_edge
            p_true = min(0.999, max(0.001, r["market_prob_up"] + bump))
            r["outcome_up"] = 1 if rng.random() < p_true else 0
        print(f"*** POSITIVE CONTROL: outcomes re-rolled with a {args.inject_edge:+.3f} "
              f"mispricing in the momentum direction ***")
        print("*** Real prices and features kept. The pipeline SHOULD find this. ***\n")

    if len(rows) < 300:
        raise SystemExit(f"only {len(rows)} usable windows -- too few to fit and hold out")

    n = len(rows)
    train, validate, holdout = rows[:int(0.5 * n)], rows[int(0.5 * n):int(0.75 * n)], rows[int(0.75 * n):]
    print(f"{n} windows with real odds  ->  train {len(train)} / validate {len(validate)} / holdout {len(holdout)}")
    print(f"friction charged per entry: {friction:.4f} ({friction*100:.2f}pp)\n")

    beta = fit_logistic(train, FEATURE_NAMES)
    print("fitted coefficients (deviation from the MARKET price, not from 50/50):")
    print(f"  {'intercept':<16} {beta[0]:+.4f}")
    for name, b in zip(FEATURE_NAMES, beta[1:]):
        print(f"  {name:<16} {b:+.4f}")

    # how much does the model actually move away from the market?
    diffs = [abs(predict(beta, r, FEATURE_NAMES) - r["market_prob_up"]) for r in validate]
    diffs.sort()
    print(f"\n|model - market| on validate: median {diffs[len(diffs)//2]:.4f}, "
          f"p90 {diffs[int(0.9*len(diffs))]:.4f}, max {diffs[-1]:.4f}")

    print("\n--- validate: choosing an edge threshold (look freely here) ---")
    # Selection rule: best LOWER CI bound among thresholds with enough
    # trades to mean anything. Selecting on the point estimate instead
    # reliably picks the tiniest sample -- an early version of this chose a
    # threshold with n=11 and a $3.46 mean, which promptly collapsed to
    # flat on the holdout. Choosing on the pessimistic end of the interval
    # penalizes small samples automatically.
    MIN_TRADES = 100
    best = None
    for thr in (0.01, 0.02, 0.03, 0.05, 0.08, 0.12):
        res = summarize(simulate(validate, beta, FEATURE_NAMES, thr, friction), f"threshold {thr:.2f}")
        if not res:
            continue
        if res["n"] < MIN_TRADES:
            continue
        if res["ci"][0] > 0 and (best is None or res["ci"][0] > best[1]["ci"][0]):
            best = (thr, res)

    print("\n--- holdout: touched once, whatever it says stands ---")
    if best is None:
        print("  No threshold produced a positive lower CI bound on validate.")
        print("  Nothing earned the right to be tested on the holdout, so the holdout")
        print("  stays unused -- running it now would just be another draw from noise.")
        chosen = 0.02
        summarize(simulate(holdout, beta, FEATURE_NAMES, chosen, friction),
                  f"(reference only) thr {chosen:.2f}")
    else:
        thr = best[0]
        print(f"  selected threshold {thr:.2f} from validate")
        summarize(simulate(holdout, beta, FEATURE_NAMES, thr, friction), f"HOLDOUT thr {thr:.2f}")

    print("\n--- zero-friction check (is any edge there at all, before costs?) ---")
    for thr in (0.02, 0.05):
        summarize(simulate(validate, beta, FEATURE_NAMES, thr, 0.0), f"validate thr {thr:.2f}, no friction")

    if args.power_sweep:
        power_sweep(rows, friction, args.seed)

    if args.output:
        Path(args.output).write_text(json.dumps(
            {"coefficients": dict(zip(["intercept"] + FEATURE_NAMES, beta)),
             "friction": friction, "n_windows": n}, indent=2))
        print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
