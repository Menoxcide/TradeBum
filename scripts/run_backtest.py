#!/usr/bin/env python3
"""
Usage:
  # smoke test on fake data -- proves the pipeline runs, tells you nothing
  # about the real strategy (see src/data/synthetic.py docstring)
  python scripts/run_backtest.py --synthetic --profile conservative

  # real backtest once you have CSVs (see README.md for the format + where
  # to source them)
  python scripts/run_backtest.py --data-dir ./data/historical --profile conservative

  # add --sensitivity to see how much results move when signal_weights are
  # perturbed +/-20% -- if the answer changes a lot, the weights are
  # overfit / not trustworthy yet
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.backtest.engine import run_backtest
from src.backtest.stats import print_summary, summarize
from src.data.provider import DataProvider
from src.data.synthetic import InMemoryProvider, generate_synthetic_dataset


def load_config(config_path: str, profile: str) -> dict:
    with open(config_path) as f:
        full = yaml.safe_load(f)
    return full["profiles"][profile]


def run_all_strategies(provider, config: dict):
    results = {}
    for strategy in ("coinflip", "naive_momentum", "confluence"):
        res = run_backtest(provider, config, strategy=strategy)
        results[strategy] = res
    return results


def run_sensitivity(provider, config: dict, n_perturbations: int = 6, seed: int = 3):
    """Reruns the confluence strategy with each signal weight perturbed
    +/-20% one at a time, to show how sensitive the trade selection is to
    the (currently hand-set) weights."""
    rng = random.Random(seed)
    base_weights = config["signal_weights"]
    print("\n=== weight sensitivity (confluence strategy) ===")
    base_res = run_backtest(provider, config, strategy="confluence")
    base_summary = summarize(base_res.trades, base_res.equity_curve, "base")
    print(f"base: n={base_summary.n_trades} win_rate={base_summary.win_rate:.1%} "
          f"mean_pnl=${base_summary.mean_pnl_per_trade:,.2f}")

    for name in base_weights:
        for direction, mult in (("up", 1.2), ("down", 0.8)):
            perturbed_cfg = copy.deepcopy(config)
            perturbed_cfg["signal_weights"][name] = base_weights[name] * mult
            res = run_backtest(provider, perturbed_cfg, strategy="confluence")
            s = summarize(res.trades, res.equity_curve, f"{name}_{direction}")
            delta_wr = s.win_rate - base_summary.win_rate
            delta_n = s.n_trades - base_summary.n_trades
            print(f"  {name} {direction} 20%: n={s.n_trades} ({delta_n:+d})  "
                  f"win_rate={s.win_rate:.1%} ({delta_wr*100:+.1f}pp)  "
                  f"mean_pnl=${s.mean_pnl_per_trade:,.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="conservative", choices=["conservative", "aggressive"])
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config" / "btc_5m_profiles.yaml"))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", action="store_true", help="run the fake-data smoke test")
    src.add_argument("--data-dir", help="directory with bars.csv/orderbook.csv/funding.csv/resolutions.csv")
    ap.add_argument("--n-windows", type=int, default=3000, help="synthetic dataset size (windows)")
    ap.add_argument("--sensitivity", action="store_true", help="also run weight sensitivity analysis")
    ap.add_argument("--output", default=None, help="write full JSON report here")
    args = ap.parse_args()

    config = load_config(args.config, args.profile)

    if args.synthetic:
        print(f"*** SYNTHETIC DATA -- {args.n_windows} fake random-walk windows ***")
        print("*** This tests the harness's plumbing only. It has no real edge ***")
        print("*** in it by construction -- see README.md before drawing any   ***")
        print("*** conclusion about the actual strategy from these numbers.    ***\n")
        bars, obs, funding, resolutions = generate_synthetic_dataset(n_windows=args.n_windows)
        provider = InMemoryProvider(bars, obs, funding, resolutions)
    else:
        provider = DataProvider(args.data_dir)
        print(f"Loaded {len(provider.resolutions)} resolved windows, "
              f"{len(provider.bars)} bars from {args.data_dir}")

    results = run_all_strategies(provider, config)
    summaries = {}
    for strategy, res in results.items():
        s = summarize(res.trades, res.equity_curve, strategy)
        summaries[strategy] = s
        print_summary(s)
        if res.warnings:
            for w in res.warnings:
                print(f"  WARNING: {w}")

    print("\n=== skip reasons (confluence strategy) ===")
    for reason, count in sorted(results["confluence"].skipped_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

    if args.sensitivity:
        run_sensitivity(provider, config)

    if args.output:
        out = {
            strategy: {
                "n_trades": s.n_trades, "win_rate": s.win_rate, "win_rate_ci": s.win_rate_ci,
                "total_pnl": s.total_pnl, "mean_pnl_per_trade": s.mean_pnl_per_trade,
                "mean_pnl_ci": s.mean_pnl_ci, "sharpe_per_trade": s.sharpe_per_trade,
                "max_drawdown_pct": s.max_drawdown_pct, "notes": s.notes,
            }
            for strategy, s in summaries.items()
        }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
