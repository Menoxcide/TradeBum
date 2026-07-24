"""
Statistics for judging a backtest result honestly. The point of this file
is to make it hard to accidentally read noise as edge: everything here
reports a range, not just a point estimate, and results are always shown
next to the coinflip/naive baselines so "better than X%" has a reference.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a win rate. Better-behaved than a
    normal approximation when n is small or the rate is near 0/1 -- both
    common in an early backtest."""
    if n == 0:
        return (0.0, 1.0)
    phat = wins / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def bootstrap_ci(values: list[float], stat_fn, n_resamples: int = 2000, ci: float = 0.95, seed: int = 11) -> tuple[float, float]:
    """Percentile bootstrap CI for any statistic computed over a list of
    per-trade values (e.g. pnl per trade -> mean, or -> Sharpe)."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    boot = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        boot.append(stat_fn(sample))
    boot.sort()
    lo = boot[int((1 - ci) / 2 * n_resamples)]
    hi = boot[min(n_resamples - 1, int((1 + ci) / 2 * n_resamples))]
    return (lo, hi)


def max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for x in equity_curve:
        peak = max(peak, x)
        if peak > 0:
            worst = max(worst, (peak - x) / peak)
    return worst


def per_trade_sharpe(pnls: list[float]) -> float:
    if len(pnls) < 2:
        return 0.0
    mean = statistics.mean(pnls)
    std = statistics.pstdev(pnls)
    return mean / std if std > 0 else 0.0


@dataclass
class Summary:
    strategy: str
    n_trades: int
    wins: int
    win_rate: float
    win_rate_ci: tuple
    total_pnl: float
    mean_pnl_per_trade: float
    mean_pnl_ci: tuple
    sharpe_per_trade: float
    max_drawdown_pct: float
    notes: list = field(default_factory=list)


def summarize(trades: list[dict], equity_curve: list[float], strategy: str) -> Summary:
    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    pnls = [t["pnl_usdc"] for t in trades]
    win_rate = wins / n if n else 0.0
    w_ci = wilson_ci(wins, n)
    total_pnl = sum(pnls)
    mean_pnl = statistics.mean(pnls) if pnls else 0.0
    mean_ci = bootstrap_ci(pnls, statistics.mean) if pnls else (0.0, 0.0)
    sharpe = per_trade_sharpe(pnls)
    mdd = max_drawdown(equity_curve)

    notes = []
    if n < 100:
        notes.append(f"only {n} trades -- confidence intervals will be wide; treat point estimates as unreliable below ~100-200 trades")
    # NOTE: win rate is deliberately NOT compared to 50% here. On a market
    # that prices in probability (like Polymarket shares), a strategy that
    # only enters already-likely-to-win positions will show a win rate well
    # above 50% even with ZERO edge, because it also pays more for those
    # positions. Mean pnl per trade (below) is the metric that actually
    # reflects edge -- see fair_value.py and README.md.
    if mean_ci[0] < 0 < mean_ci[1]:
        notes.append("mean-pnl-per-trade 95% CI includes 0 -- edge is not yet statistically distinguishable from zero")

    return Summary(
        strategy=strategy, n_trades=n, wins=wins, win_rate=win_rate, win_rate_ci=w_ci,
        total_pnl=total_pnl, mean_pnl_per_trade=mean_pnl, mean_pnl_ci=mean_ci,
        sharpe_per_trade=sharpe, max_drawdown_pct=mdd, notes=notes,
    )


def print_summary(s: Summary) -> None:
    print(f"\n=== {s.strategy} ===")
    print(f"trades: {s.n_trades}  wins: {s.wins}")
    print(f"win rate: {s.win_rate:.1%}  (95% CI: {s.win_rate_ci[0]:.1%} - {s.win_rate_ci[1]:.1%})")
    print(f"total pnl: ${s.total_pnl:,.2f}   mean pnl/trade: ${s.mean_pnl_per_trade:,.2f} "
          f"(95% CI: ${s.mean_pnl_ci[0]:,.2f} to ${s.mean_pnl_ci[1]:,.2f})")
    print(f"sharpe (per-trade): {s.sharpe_per_trade:.3f}")
    print(f"max drawdown: {s.max_drawdown_pct:.1%}")
    for note in s.notes:
        print(f"  NOTE: {note}")
