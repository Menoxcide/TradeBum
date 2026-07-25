#!/usr/bin/env python3
"""
Builds the three pre-registered Track A datasets in the validator's format.

Each hypothesis becomes one CSV of opportunities: a timestamp, a realized
outcome, and the features that are supposed to predict it. They then go
through the identical protocol -- time splits, lower-bound selection, single
holdout, positive control, detection floor -- so the three are directly
comparable and none of them gets a bespoke test built around its result.

`mode=returns` throughout: on a perpetual there is no market probability to
use as an offset, so the benchmark is zero expected return. That is a weaker
null than the Polymarket case and worth stating plainly -- "predicts a nonzero
return" is a lower bar than "adds information beyond a traded price".

Look-ahead discipline: every feature is computed from bars strictly BEFORE the
decision bar, and the outcome is measured strictly AFTER it. The one-bar gap
is deliberate; a feature that touches the decision bar's close is reading the
price it would be trading at.
"""

from __future__ import annotations

import csv
import statistics
from bisect import bisect_right
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = Path(__file__).resolve().parent / "datasets"


def load_bars(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append((int(r["timestamp_ms"]), float(r["open"]), float(r["high"]),
                         float(r["low"]), float(r["close"]), float(r["volume"])))
    rows.sort()
    return rows


def write(name, header, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name:<24} {len(rows):>7,} rows")


# ------------------------------------------------------------------ H1


def build_h1(bars, funding_path):
    """Funding carry: does funding at T predict the return over the NEXT
    funding interval? Entry one bar after the funding stamp, because the rate
    is only observable once it prints."""
    fund = []
    with open(funding_path, newline="") as f:
        for r in csv.DictReader(f):
            fund.append((int(r["timestamp_ms"]), float(r["funding_rate"])))
    fund.sort()

    ts = [b[0] for b in bars]
    close = {b[0]: b[4] for b in bars}

    def price_at_or_after(t):
        i = bisect_right(ts, t - 1)
        return close[ts[i]] if i < len(ts) else None

    rates = [r for _, r in fund]
    if not rates:
        return []
    med_abs = statistics.median(abs(r) for r in rates) or 1e-9

    rows = []
    for (t, rate), (t_next, _) in zip(fund, fund[1:]):
        entry = price_at_or_after(t + 60_000)      # one bar after the print
        exit_ = price_at_or_after(t_next)
        if entry is None or exit_ is None or entry <= 0:
            continue
        ret = (exit_ - entry) / entry
        rows.append([t, 1 if ret > 0 else 0, round(ret, 8),
                     round(rate / med_abs, 6),                 # scaled funding
                     round(abs(rate) / med_abs, 6)])           # magnitude
    write("h1_funding.csv", ["ts", "won", "ret", "funding_z", "funding_abs"], rows)
    return rows


# ------------------------------------------------------------------ H2


def build_h2(okx, coinbase, horizon_min=5):
    """Cross-venue lead-lag: does the OKX-minus-Coinbase gap at T predict OKX's
    return over the next `horizon_min` minutes?"""
    cb = {b[0]: b[4] for b in coinbase}
    ts = [b[0] for b in okx]
    close = {b[0]: b[4] for b in okx}

    gaps = []
    for t in ts:
        if t in cb and cb[t] > 0:
            gaps.append((close[t] - cb[t]) / cb[t] * 10000)  # bps
    if not gaps:
        return []
    sd = statistics.pstdev(gaps) or 1.0

    rows = []
    step = horizon_min * 60_000
    for i, t in enumerate(ts):
        if t not in cb or cb[t] <= 0:
            continue
        gap = (close[t] - cb[t]) / cb[t] * 10000
        t_exit = t + step
        if t_exit not in close:
            continue
        entry = close[t]
        ret = (close[t_exit] - entry) / entry
        # 1-minute momentum as a control, from bars strictly before t
        prev = close.get(t - 60_000)
        mom = ((entry - prev) / prev * 10000) if prev else 0.0
        rows.append([t, 1 if ret > 0 else 0, round(ret, 8),
                     round(gap / sd, 6), round(mom / 100.0, 6)])
    write("h2_basis.csv", ["ts", "won", "ret", "gap_z", "mom_1m"], rows)
    return rows


# ------------------------------------------------------------------ H3


def build_h3(bars, lookback=20, horizon_min=15):
    """Volatility-regime reversal: after a large adverse move on elevated
    volume, is the subsequent return positive? Features use bars strictly
    before the decision bar."""
    ts = [b[0] for b in bars]
    close = {b[0]: b[4] for b in bars}
    vol = {b[0]: b[5] for b in bars}

    rows = []
    step = horizon_min * 60_000
    for i in range(lookback + 1, len(bars) - horizon_min - 1):
        t = ts[i]
        window = bars[i - lookback:i]                     # strictly before t
        if len(window) < lookback:
            continue
        rets = []
        for a, b in zip(window, window[1:]):
            if a[4] > 0:
                rets.append((b[4] - a[4]) / a[4])
        if len(rets) < 5:
            continue
        sd = statistics.pstdev(rets) or 1e-9
        med_vol = statistics.median(w[5] for w in window) or 1e-9

        prev = close[ts[i - 1]]
        move = (prev - close[ts[i - lookback]]) / close[ts[i - lookback]]
        move_z = move / (sd * (lookback ** 0.5))
        vol_ratio = vol[ts[i - 1]] / med_vol

        t_exit = t + step
        if t_exit not in close:
            continue
        entry = close[t]
        ret = (close[t_exit] - entry) / entry
        # reversal signal: fade the move, so flip its sign
        rows.append([t, 1 if ret > 0 else 0, round(ret, 8),
                     round(-move_z, 6), round(vol_ratio - 1.0, 6),
                     round(-move_z * max(0.0, vol_ratio - 1.0), 6)])
    write("h3_reversal.csv",
          ["ts", "won", "ret", "fade_z", "vol_excess", "fade_x_vol"], rows)
    return rows


if __name__ == "__main__":
    okx = load_bars(DATA / "okx" / "perp_1m.csv")
    print(f"loaded {len(okx):,} OKX perp bars\n")
    print("building pre-registered datasets:")
    build_h1(okx, DATA / "okx" / "funding.csv")
    cb_path = DATA / "alt" / "coinbase_bars.csv"
    if cb_path.exists():
        build_h2(okx, load_bars(cb_path))
    else:
        print("  h2 skipped -- no coinbase bars")
    build_h3(okx)
