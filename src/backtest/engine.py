"""
The backtest loop. This is what should run BEFORE any Kelly fraction is
trusted or EXECUTE is set to true on the live bot.

Three `strategy` modes share the same data, gating on move-size/liquidity/
daily risk caps, and PnL model, so they're a fair ablation:

  "coinflip"        random direction, minimal gating.       isolates: is
                     the opportunity set (fees/spread/vol) itself survivable
  "naive_momentum"  momentum-sign direction, minimal gating. isolates: does
                     momentum direction alone beat a coin flip
  "confluence"      full pipeline: confluence score threshold, EV gate,
                     correlation tracker, Kelly sizing.       isolates: does
                     all the added machinery beat naive momentum

Look-ahead discipline: every value put into market_state is read via
DataProvider's *_as_of(decision_ts) methods, which only ever see data with
timestamp <= decision_ts. Rolling ATR/volume/percentile history is updated
AFTER a window is scored, never before, so window N's gates never see
window N's own outcome.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from src.backtest.fair_value import estimate_vol_per_sqrt_sec, fair_value_prob_up
from src.data.schema import MarketState
from src.risk.correlation_tracker import CorrelationTracker
from src.risk.ev_gate import confluence_score_to_model_prob, should_trade
from src.signal_engine.confluence_scorer import ConfluenceScorer
from src.signal_engine.price_momentum import propose_direction
from src.sizing_engine.kelly_calculator import KellyVolatilitySizer


def _true_range(bar, prev_close):
    if prev_close is None:
        return bar.high - bar.low
    return max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))


def _compute_atr(bars) -> float:
    if len(bars) < 2:
        return 0.0
    trs = []
    prev_close = bars[0].close
    for b in bars[1:]:
        trs.append(_true_range(b, prev_close))
        prev_close = b.close
    return sum(trs) / len(trs) if trs else 0.0


def _percentile_rank(value: float, history) -> float:
    if not history:
        return 50.0
    below_or_equal = sum(1 for h in history if h <= value)
    return 100.0 * below_or_equal / len(history)


def _day_bucket(ts_ms: int) -> int:
    return ts_ms // 86_400_000


@dataclass
class BacktestResult:
    strategy: str
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    skipped_reasons: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def run_backtest(
    provider,
    config: dict,
    strategy: str = "confluence",
    rng_seed: int = 7,
    warmup_windows: int = 20,
    entry_offset_sec: int = 120,
    atr_lookback_windows: int = 20,
    bar_lookback_for_atr: int = 30,
) -> BacktestResult:
    assert strategy in ("coinflip", "naive_momentum", "confluence")
    rng = random.Random(rng_seed)

    weights = config["signal_weights"]
    min_move_usd = config["min_btc_move_usd"]
    min_confluence_score = config.get("min_confluence_score", 0)
    min_volume_percentile = config.get("min_volume_percentile", 0)
    min_realized_vol_5m = config.get("min_realized_vol_5m", 0.0)
    min_book_depth_usdc = config.get("min_book_depth_usdc", 0.0)
    daily_loss_limit_pct = config["daily_loss_limit_pct"]
    max_trades_per_day = config["max_trades_per_day"]
    total_allocation = config["total_allocation_usdc"]
    taker_fee = config.get("taker_fee", 0.0)  # verify against current Polymarket fee schedule
    assumed_pm_spread = config.get("assumed_polymarket_spread", 0.02)  # placeholder, see README
    slippage_buffer = config.get("slippage_buffer", 0.005)
    fallback_fixed_pct = config.get("fallback_fixed_pct", 0.01)

    scorer = ConfluenceScorer(weights=weights, min_move_usd=min_move_usd)
    sizer = KellyVolatilitySizer(config, min_trades_for_kelly=config.get("min_trades_for_kelly", 50))
    corr_tracker = CorrelationTracker(lookback=10)

    atr_history: deque = deque(maxlen=atr_lookback_windows)
    volume_history: deque = deque(maxlen=atr_lookback_windows)

    result = BacktestResult(strategy=strategy)
    skipped = {}

    def skip(reason: str):
        skipped[reason] = skipped.get(reason, 0) + 1

    equity = total_allocation
    day_state = {"day": None, "start_equity": equity, "trades_today": 0, "halted": False}
    completed_trades_for_kelly: list = []  # (pnl, is_win) for rolling win_rate/avg_win/avg_loss

    warned_missing_prob = False

    for i, window in enumerate(provider.resolutions):
        decision_ts = window.window_end_ms - entry_offset_sec * 1000
        if decision_ts < window.window_start_ms:
            decision_ts = window.window_start_ms

        day = _day_bucket(window.window_start_ms)
        if day_state["day"] != day:
            day_state = {"day": day, "start_equity": equity, "trades_today": 0, "halted": False}

        bars_recent = provider.bars_as_of(decision_ts, lookback=bar_lookback_for_atr)
        bars_in_window = [b for b in bars_recent if b.timestamp_ms >= window.window_start_ms]

        if len(bars_recent) < 5 or not bars_in_window:
            skip("insufficient_bar_history")
            continue

        btc_price = bars_in_window[-1].close
        atr_5m = _compute_atr(bars_recent)
        volume_current = sum(b.volume for b in bars_in_window)

        # rolling stats use ONLY prior windows -- updated at the end of the loop body
        atr_median = sorted(atr_history)[len(atr_history) // 2] if atr_history else atr_5m
        volume_median = sorted(volume_history)[len(volume_history) // 2] if volume_history else volume_current
        volume_pctile = _percentile_rank(volume_current, list(volume_history))

        orderbook = provider.orderbook_as_of(decision_ts)
        funding_rate = provider.funding_as_of(decision_ts)

        market_prob_up = window.market_prob_up_at_decision
        market_prob_source = "real"
        if market_prob_up is None:
            # No real Polymarket odds for this window yet. Fall back to the
            # geometry-implied fair value (same idea as synthetic.py) using
            # REAL estimated volatility from the data you do have, instead
            # of a flat 0.5. This means a backtest is still meaningful with
            # BTC data alone, before any Polymarket odds have been collected
            # -- it answers "does momentum beat pure random-walk geometry",
            # which is necessary but not sufficient (you still need real
            # Polymarket prices to know if that edge survives real fees and
            # whatever Polymarket's own pricing already reflects).
            move_so_far = btc_price - window.open_price
            vol_est = estimate_vol_per_sqrt_sec(bars_recent)
            if vol_est and vol_est > 0:
                market_prob_up = fair_value_prob_up(move_so_far, entry_offset_sec, vol_est)
                market_prob_source = "fair_value_fallback"
            else:
                market_prob_up = 0.5
                market_prob_source = "flat_fallback"
            if not warned_missing_prob:
                result.warnings.append(
                    "resolutions.csv had no market_prob_up_at_decision for at least one "
                    "window -- using the geometry-implied fair-value estimate from real "
                    "BTC volatility instead (see fair_value.py). This tests momentum "
                    "against random-walk math, not against real Polymarket pricing -- "
                    "fill in real odds before trusting results as tradeable."
                )
                warned_missing_prob = True

        market_state: MarketState = {
            "timestamp_ms": decision_ts,
            "btc_price": btc_price,
            "window_open_price": window.open_price,
            "seconds_to_close": entry_offset_sec,
            "bars_recent": bars_recent,
            "orderbook": orderbook,
            "funding_rate": funding_rate,
            "atr_5m": atr_5m,
            "atr_median": atr_median,
            "volume_current": volume_current,
            "volume_median": volume_median,
            "market_prob_up": market_prob_up,
        }

        # ---- warm-up: build rolling history, never trade during it ----
        if i < warmup_windows:
            atr_history.append(atr_5m)
            volume_history.append(volume_current)
            skip("warmup_period")
            continue

        # ---- direction + confluence (always compute; needed by all 3 modes for the move gate) ----
        composite, candidate_direction, breakdown = scorer.score(market_state)
        if candidate_direction is None:
            atr_history.append(atr_5m)
            volume_history.append(volume_current)
            skip("move_below_threshold")
            continue

        if strategy == "coinflip":
            candidate_direction = rng.choice(["UP", "DOWN"])

        # ---- shared hard gates (apply to all strategies: real execution constraints) ----
        if atr_5m < min_realized_vol_5m:
            atr_history.append(atr_5m)
            volume_history.append(volume_current)
            skip("volatility_floor")
            continue

        if orderbook is not None:
            bid_notional, ask_notional = orderbook.top_n_notional(3)
            if (bid_notional + ask_notional) < min_book_depth_usdc:
                atr_history.append(atr_5m)
                volume_history.append(volume_current)
                skip("book_too_thin")
                continue

        if day_state["halted"]:
            skip("daily_loss_limit_hit")
            atr_history.append(atr_5m)
            volume_history.append(volume_current)
            continue
        if day_state["trades_today"] >= max_trades_per_day:
            skip("max_trades_per_day")
            atr_history.append(atr_5m)
            volume_history.append(volume_current)
            continue

        # ---- strategy-specific gating ----
        size_usdc = 0.0
        model_prob_for_candidate = None
        ev = None

        if strategy in ("coinflip", "naive_momentum"):
            size_usdc = total_allocation * fallback_fixed_pct

        else:  # confluence
            if volume_pctile < min_volume_percentile:
                atr_history.append(atr_5m)
                volume_history.append(volume_current)
                skip("volume_percentile_gate")
                continue
            if composite is None or composite < min_confluence_score:
                atr_history.append(atr_5m)
                volume_history.append(volume_current)
                skip("confluence_score_gate")
                continue

            model_prob_for_candidate = confluence_score_to_model_prob(composite)
            market_prob_for_candidate = market_prob_up if candidate_direction == "UP" else (1 - market_prob_up)
            edge_ok, ev = should_trade(
                market_prob_for_candidate, model_prob_for_candidate,
                spread=assumed_pm_spread, fees=taker_fee, slippage_buffer=slippage_buffer,
            )
            if not edge_ok:
                atr_history.append(atr_5m)
                volume_history.append(volume_current)
                skip("ev_gate")
                continue

            # should_trade() internally decides WHICH side the edge favors
            # (see its "if model_prob > market_prob" branch). edge_ok=True
            # only means *some* side clears friction -- it can still be the
            # side opposite candidate_direction (the model thinks the move
            # is already over-priced by the market, i.e. a fade setup).
            # Bug fix: earlier versions of this loop ignored that and always
            # traded candidate_direction regardless. This build stays
            # conservative and only trades WITH momentum when the edge
            # genuinely favors it; a fade variant would flip candidate_direction
            # and market_prob_for_candidate here instead of skipping, but
            # that's a materially different, separately-validated strategy,
            # not a one-line change to make casually.
            if model_prob_for_candidate <= market_prob_for_candidate:
                atr_history.append(atr_5m)
                volume_history.append(volume_current)
                skip("ev_favors_fade_not_momentum")
                continue

            corr_adj = corr_tracker.get_adjustment(candidate_direction)
            if corr_adj == 0.0:
                atr_history.append(atr_5m)
                volume_history.append(volume_current)
                skip("correlation_tracker_skip")
                continue

            win_rate = (sum(1 for _, w in completed_trades_for_kelly if w) / len(completed_trades_for_kelly)) if completed_trades_for_kelly else 0.5
            wins = [p for p, w in completed_trades_for_kelly if w]
            losses = [-p for p, w in completed_trades_for_kelly if not w]
            avg_win = (sum(wins) / len(wins)) if wins else 0.0
            avg_loss = (sum(losses) / len(losses)) if losses else 0.0

            sizing = sizer.calculate(
                win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
                current_atr=atr_5m, median_atr=atr_median if atr_median > 0 else atr_5m,
                n_trades_so_far=len(completed_trades_for_kelly),
            )
            size_usdc = sizing["position_size_usdc"] * corr_adj

        if size_usdc <= 0:
            atr_history.append(atr_5m)
            volume_history.append(volume_current)
            skip("zero_size")
            continue

        # ---- resolve trade against ground truth ----
        entry_price = market_prob_up if candidate_direction == "UP" else (1 - market_prob_up)
        entry_price = min(0.99, max(0.01, entry_price))
        won = candidate_direction == window.outcome

        if won:
            gross_payout = size_usdc / entry_price
            pnl = gross_payout - size_usdc
            pnl -= taker_fee * gross_payout  # fee on the payout
        else:
            pnl = -size_usdc

        equity += pnl
        day_state["trades_today"] += 1
        if equity <= day_state["start_equity"] * (1 - daily_loss_limit_pct):
            day_state["halted"] = True

        if strategy == "confluence":
            corr_tracker.record(candidate_direction, pnl)
            completed_trades_for_kelly.append((pnl, won))

        result.trades.append({
            "window_start_ms": window.window_start_ms,
            "side": candidate_direction,
            "confluence_score": composite,
            "model_prob": model_prob_for_candidate,
            "market_prob": market_prob_up,
            "market_prob_source": market_prob_source,
            "ev": ev,
            "size_usdc": size_usdc,
            "entry_price": entry_price,
            "outcome": window.outcome,
            "won": won,
            "pnl_usdc": pnl,
            "equity_after": equity,
        })
        result.equity_curve.append(equity)

        atr_history.append(atr_5m)
        volume_history.append(volume_current)

    result.skipped_reasons = skipped
    return result
