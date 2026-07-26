"""The edge validator -- 530 lines, zero tests, and the B2 deliverable.

PLAN.md makes this the thing that gets sold, and the repo's own history
shows what unguarded change costs here: commit e968b3a records that a
publicly shipped example generator built its "genuine edge" from white noise
that predicted its own outcome, which is operationally a data leak. It was
caught by a hand-run eval, months later, not by a test.

The tests that matter most are the two end-to-end controls at the bottom:
uninformative data must not produce a POSITIVE verdict, and leaked data must
trip the implausibility check. Everything above them exists so that when one
of those fails, the reason is findable.
"""

from __future__ import annotations

import importlib.util
import math
import random
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "edge-validation" / "scripts" / "validate_edge.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_edge", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ve = _load()


def row(t, y, mkt=0.5, ret=0.0, **features):
    return {"t": t, "y": y, "mkt": mkt, "ret": ret, "f": features}


# --------------------------------------------------------------- basics


def test_logit_and_sigmoid_are_inverses():
    for p in (0.01, 0.1, 0.5, 0.9, 0.99):
        assert ve.sigmoid(ve.logit(p)) == pytest.approx(p)


def test_logit_clamps_instead_of_diverging():
    """p of exactly 0 or 1 appears whenever a market prices a near-certainty.
    log(0) would end the run."""
    assert math.isfinite(ve.logit(0.0))
    assert math.isfinite(ve.logit(1.0))
    assert ve.logit(0.0) < 0 < ve.logit(1.0)


def test_sigmoid_does_not_overflow_on_large_input():
    assert ve.sigmoid(-10_000) == pytest.approx(0.0)
    assert ve.sigmoid(10_000) == pytest.approx(1.0)


def test_solve_recovers_a_known_solution():
    assert ve.solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0]) == pytest.approx([1.0, 3.0])


def test_solve_returns_none_on_a_singular_matrix():
    """Returned, not raised: fit_logistic treats None as 'stop iterating'
    and keeps the coefficients it already has."""
    assert ve.solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]) is None


def test_split_is_by_time_and_contiguous():
    rows = [row(t, 0) for t in range(100)]
    train, val, hold = ve.split(rows)
    assert (len(train), len(val), len(hold)) == (50, 25, 25)
    assert max(r["t"] for r in train) < min(r["t"] for r in val)
    assert max(r["t"] for r in val) < min(r["t"] for r in hold)


def test_split_never_loses_or_duplicates_a_row():
    for n in (0, 1, 3, 7, 99, 400):
        rows = [row(t, 0) for t in range(n)]
        train, val, hold = ve.split(rows)
        assert len(train) + len(val) + len(hold) == n


def test_wrap_preserves_every_word():
    text = "a b c " * 40
    assert " ".join(ve._wrap(text, 20)).split() == text.split()


# ---------------------------------------------------------- fit_logistic


def test_fit_recovers_a_positive_coefficient_for_a_predictive_feature():
    """Also the guard on the Newton step's sign. The docstring warns that
    flipping the Hessian's sign does not error -- it diverges to ~1e15
    coefficients that still 'fit'. A sane, finite, correctly-signed estimate
    is the observable difference."""
    rng = random.Random(0)
    rows = []
    for t in range(1500):
        x = rng.gauss(0, 1)
        p = ve.sigmoid(1.5 * x)
        rows.append(row(t, 1 if rng.random() < p else 0, x=x))
    beta = ve.fit_logistic(rows, ["x"], use_offset=False)
    assert all(math.isfinite(b) for b in beta)
    assert beta[1] == pytest.approx(1.5, rel=0.25)
    assert abs(beta[0]) < 0.3, "intercept should be near zero on balanced data"


def test_fit_recovers_a_negative_coefficient_too():
    rng = random.Random(1)
    rows = []
    for t in range(1500):
        x = rng.gauss(0, 1)
        p = ve.sigmoid(-1.5 * x)
        rows.append(row(t, 1 if rng.random() < p else 0, x=x))
    beta = ve.fit_logistic(rows, ["x"], use_offset=False)
    assert beta[1] == pytest.approx(-1.5, rel=0.25)


def test_offset_makes_an_already_priced_feature_read_as_zero():
    """The single most important property of the tool. When the market has
    already priced the feature, the coefficient measured against the price
    must collapse to ~0 -- a real finding -- even though the same feature
    fits strongly against a coin flip."""
    rng = random.Random(2)
    rows = []
    for t in range(3000):
        x = rng.gauss(0, 1)
        p = ve.sigmoid(1.2 * x)
        rows.append(row(t, 1 if rng.random() < p else 0, mkt=p, x=x))

    with_offset = ve.fit_logistic(rows, ["x"], use_offset=True)
    without_offset = ve.fit_logistic(rows, ["x"], use_offset=False)
    assert abs(with_offset[1]) < 0.2, "nothing is added on top of the price"
    assert without_offset[1] > 0.9, "against a coin flip the same feature looks strong"


def test_offset_leaves_a_genuinely_unpriced_feature_visible():
    """The complement: an edge the market does NOT price must survive the
    offset, or the tool could never report a positive result at all."""
    rng = random.Random(3)
    rows = []
    for t in range(3000):
        x = rng.gauss(0, 1)
        true_p = ve.sigmoid(1.2 * x)
        rows.append(row(t, 1 if rng.random() < true_p else 0, mkt=0.5, x=x))
    beta = ve.fit_logistic(rows, ["x"], use_offset=True)
    assert beta[1] > 0.8


def test_perfect_separation_does_not_reach_the_divergence_guard():
    """Why separation_check() has to exist as a separate check.

    The guard aborts at |coefficient| > 1e6. Perfect separation never gets
    there: the IRLS weight floor (w = max(p*(1-p), 1e-8)) throttles the
    Newton step, so 60 iterations land near 22 and even 1000 only reach ~25
    -- large, finite, and entirely plausible-looking in the coefficient
    table. The guard covers the runaway-iteration case it was written for;
    separation is caught by accuracy instead."""
    rows = [row(t, 1 if t % 2 else 0, x=1.0 if t % 2 else -1.0) for t in range(400)]
    beta = ve.fit_logistic(rows, ["x"], use_offset=False, l2=0.0)
    assert math.isfinite(beta[1])
    assert beta[1] > 10.0, "separation drives the coefficient high"
    assert abs(beta[1]) < 1e6, "...but never far enough to trip the guard"


def test_separation_check_flags_a_feature_containing_the_answer(capsys):
    """Perfect separation is the fingerprint of a leak. It is now called out
    by name, at the point where the coefficient table is printed, rather
    than left to be inferred from a suspiciously large coefficient."""
    rows = [row(t, 1 if t % 2 else 0, x=1.0 if t % 2 else -1.0) for t in range(400)]
    beta = ve.fit_logistic(rows, ["x"], use_offset=False)
    ve.separation_check(rows, beta, ["x"], use_offset=False)
    out = capsys.readouterr().out
    assert "PERFECT SEPARATION" in out
    assert "lag every feature" in out.lower(), "must state the decisive next test"


def test_separation_check_is_quiet_on_an_ordinary_signal(capsys):
    """A genuinely predictive but noisy feature must not be flagged, or the
    warning becomes noise and gets ignored."""
    rng = random.Random(21)
    rows = []
    for t in range(1500):
        x = rng.gauss(0, 1)
        rows.append(row(t, 1 if rng.random() < ve.sigmoid(1.0 * x) else 0, x=x))
    beta = ve.fit_logistic(rows, ["x"], use_offset=False)
    ve.separation_check(rows, beta, ["x"], use_offset=False)
    assert capsys.readouterr().out == ""


def test_separation_check_is_quiet_on_a_legitimate_long_shot_market(capsys):
    """Regression: accuracy alone is dominated by the majority class.

    On a market resolving the same way 99.4% of the time -- ordinary for
    long-shot prediction markets, and hygiene() only warns about imbalance,
    never rejects it -- a model that mostly predicts the majority scores
    99.4% with no leak at all. An absolute 0.99 threshold flagged exactly
    that as PERFECT SEPARATION, so the check compared against the baseline
    a majority-class guess achieves."""
    rng = random.Random(3)
    rows = []
    for t in range(2000):
        x = rng.gauss(0, 1)
        p = min(0.999, max(0.001, 0.99 + 0.005 * x))
        rows.append(row(t, 1 if rng.random() < p else 0, mkt=p, x=x))

    ones = sum(r["y"] for r in rows)
    assert max(ones, len(rows) - ones) / len(rows) > 0.98, "fixture must be skewed"

    beta = ve.fit_logistic(rows, ["x"], use_offset=True)
    ve.separation_check(rows, beta, ["x"], use_offset=True)
    assert capsys.readouterr().out == "", "flagged a skewed but honest market as a leak"


def test_separation_check_still_fires_on_a_leak_in_a_skewed_market(capsys):
    """The complement: skew must not become a blanket exemption. A genuine
    leak drives errors to zero regardless of the base rate."""
    rng = random.Random(4)
    rows = []
    for t in range(2000):
        p = min(0.999, max(0.001, rng.gauss(0.97, 0.02)))
        y = 1 if rng.random() < p else 0
        rows.append(row(t, y, mkt=p, x=(5.0 if y else -5.0)))
    beta = ve.fit_logistic(rows, ["x"], use_offset=True)
    ve.separation_check(rows, beta, ["x"], use_offset=True)
    assert "PERFECT SEPARATION" in capsys.readouterr().out


def test_separation_check_ignores_samples_too_small_to_judge(capsys):
    """Perfect accuracy on 20 rows is unremarkable and would fire
    constantly on exploratory slices."""
    rows = [row(t, 1 if t % 2 else 0, x=1.0 if t % 2 else -1.0) for t in range(20)]
    beta = ve.fit_logistic(rows, ["x"], use_offset=False)
    ve.separation_check(rows, beta, ["x"], use_offset=False)
    assert capsys.readouterr().out == ""


def test_separation_check_catches_the_leaky_dataset_end_to_end(capsys):
    """The realistic case: a feature carrying a sliver of the outcome, not a
    perfectly copied label."""
    rows = make_leaky(n=3000, seed=31)
    strong_leak = [dict(r, f={**r["f"], "momentum": (3.0 if r["y"] else -3.0)}) for r in rows]
    beta = ve.fit_logistic(strong_leak, ["momentum", "volume_ratio"], use_offset=True)
    ve.separation_check(strong_leak, beta, ["momentum", "volume_ratio"], use_offset=True)
    assert "PERFECT SEPARATION" in capsys.readouterr().out


def test_perfect_separation_is_caught_by_the_magnitude_check(capsys):
    """The backstop that does work. A feature that perfectly separates the
    outcome pushes the model's disagreement with the market to ~0.46, far
    past the 0.15 implausibility threshold."""
    rng = random.Random(12)
    rows = []
    for t in range(2000):
        p = min(0.95, max(0.05, rng.gauss(0.5, 0.15)))
        y = 1 if rng.random() < p else 0
        rows.append(row(t, y, mkt=p, x=1.0 if y else -1.0))

    _, holdout, med_dev, _ = ve.pipeline(
        rows, ["x"], friction=0.01, mode="market",
        thresholds=THRESHOLDS, min_trades=100, use_offset=True,
    )
    assert med_dev > 0.15
    ve.implausibility_check(med_dev, holdout, "market", use_offset=True)
    assert "IMPLAUSIBLE MAGNITUDE" in capsys.readouterr().out


def test_l2_shrinks_coefficients_toward_zero():
    rng = random.Random(4)
    rows = []
    for t in range(800):
        x = rng.gauss(0, 1)
        rows.append(row(t, 1 if rng.random() < ve.sigmoid(2.0 * x) else 0, x=x))
    light = ve.fit_logistic(rows, ["x"], use_offset=False, l2=0.1)
    heavy = ve.fit_logistic(rows, ["x"], use_offset=False, l2=500.0)
    assert abs(heavy[1]) < abs(light[1])


# -------------------------------------------------------------- simulate


def test_no_trades_when_the_model_agrees_with_the_market():
    rows = [row(t, 1, mkt=0.5, x=0.0) for t in range(100)]
    beta = [0.0, 0.0]  # predicts exactly the market
    assert ve.simulate(rows, beta, ["x"], threshold=0.01, friction=0.0, mode="market") == []


def test_friction_always_worsens_the_entry():
    """Charged as a worse price, not a flat fee, because in a binary market
    the cost of crossing scales with how far from 0.5 you enter."""
    rows = [row(t, 1, mkt=0.5, x=1.0) for t in range(200)]
    beta = [1.0, 0.0]
    free = ve.simulate(rows, beta, ["x"], 0.01, 0.0, "market")
    charged = ve.simulate(rows, beta, ["x"], 0.01, 0.05, "market")
    assert free and charged
    assert sum(t["pnl"] for t in charged) < sum(t["pnl"] for t in free)


def test_market_mode_payout_is_the_binary_contract():
    rows = [row(0, 1, mkt=0.4, x=1.0)]
    beta = [1.0, 0.0]  # pushes the prediction above the market -> buy YES at 0.4
    trades = ve.simulate(rows, beta, ["x"], threshold=0.01, friction=0.0, mode="market")
    assert len(trades) == 1
    assert trades[0]["won"]
    assert trades[0]["pnl"] == pytest.approx(1.0 / 0.4 - 1.0)


def test_market_mode_loss_is_the_whole_stake():
    rows = [row(0, 0, mkt=0.4, x=1.0)]
    trades = ve.simulate(rows, [1.0, 0.0], ["x"], 0.01, 0.0, "market")
    assert trades[0]["pnl"] == pytest.approx(-1.0)


def test_entries_outside_the_tradeable_band_are_dropped():
    """An entry at 0.999 implies a 1000x payout on a rounding error."""
    rows = [row(0, 1, mkt=0.999, x=1.0), row(1, 1, mkt=0.001, x=1.0)]
    assert ve.simulate(rows, [5.0, 0.0], ["x"], 0.001, 0.0, "market") == []


def test_returns_mode_takes_the_side_the_model_favours():
    long_row = [row(0, 1, mkt=0.5, ret=0.05, x=1.0)]
    trades = ve.simulate(long_row, [1.0, 0.0], ["x"], 0.01, 0.0, "returns", use_offset=False)
    assert trades[0]["pnl"] == pytest.approx(0.05)

    short = ve.simulate(long_row, [-1.0, 0.0], ["x"], 0.01, 0.0, "returns", use_offset=False)
    assert short[0]["pnl"] == pytest.approx(-0.05), "a short must invert the return"


def test_threshold_filters_out_small_disagreements():
    rng = random.Random(5)
    rows = [row(t, 1, mkt=0.5, x=rng.gauss(0, 1)) for t in range(500)]
    beta = [0.0, 0.5]
    counts = [len(ve.simulate(rows, beta, ["x"], thr, 0.0, "market")) for thr in (0.001, 0.05, 0.2)]
    assert counts == sorted(counts, reverse=True)


# -------------------------------------------------------------- describe


def test_describe_of_no_trades_is_none():
    assert ve.describe([], "empty", quiet=True) is None


def test_describe_verdicts_follow_the_confidence_bound():
    """POSITIVE requires the whole interval above zero -- the discipline the
    tool exists to enforce. A positive mean alone is not enough."""
    strong = ve.describe([{"pnl": 1.0, "won": True}] * 200, "strong", quiet=True)
    losing = ve.describe([{"pnl": -1.0, "won": False}] * 200, "losing", quiet=True)
    noisy = ve.describe([{"pnl": 1.0, "won": True}, {"pnl": -1.0, "won": False}] * 100,
                        "noisy", quiet=True)
    assert strong["verdict"] == "POSITIVE"
    assert losing["verdict"] == "negative"
    assert noisy["verdict"] == "flat"


def test_describe_reports_the_sample_mean():
    stats = ve.describe([{"pnl": 2.0, "won": True}, {"pnl": -1.0, "won": False}], "x", quiet=True)
    assert stats["mean"] == pytest.approx(0.5)
    assert stats["n"] == 2


# --------------------------------------------------------- injection_grid


def test_injection_grid_is_anchored_on_friction():
    """The fix recorded in the module docstring: a fixed grid is sensibly
    scaled for probabilities and absurd for returns. Multiples of cost read
    directly as 'could I have detected an edge worth trading'."""
    grid = ve.injection_grid([], friction=0.01, mode="market")
    assert [m for m, _ in grid] == [0.25, 0.5, 1, 2, 4, 8, 16]
    assert dict(grid)[1] == pytest.approx(0.01)
    assert dict(grid)[4] == pytest.approx(0.04)


def test_injection_grid_scales_with_the_friction_given():
    coarse = dict(ve.injection_grid([], friction=0.05, mode="market"))
    fine = dict(ve.injection_grid([], friction=0.001, mode="market"))
    assert coarse[1] == pytest.approx(0.05)
    assert fine[1] == pytest.approx(0.001)


def test_injection_grid_without_friction_falls_back_to_dispersion():
    rows = [row(t, 1, ret=0.02) for t in range(50)]
    returns_grid = dict(ve.injection_grid(rows, friction=0.0, mode="returns"))
    market_grid = dict(ve.injection_grid(rows, friction=0.0, mode="market"))
    assert returns_grid[1] == pytest.approx(0.02 * 0.25)
    assert market_grid[1] == pytest.approx(0.01)


# ---------------------------------------------------------------- reroll


def test_reroll_injects_edge_along_the_first_feature():
    rows = [row(t, 0, mkt=0.5, x=1.0 if t % 2 else -1.0) for t in range(4000)]
    rerolled = ve.reroll(rows, edge=0.3, features=["x"], seed=1, mode="market")
    positive = [r["y"] for r in rerolled if r["f"]["x"] > 0]
    negative = [r["y"] for r in rerolled if r["f"]["x"] < 0]
    assert sum(positive) / len(positive) == pytest.approx(0.8, abs=0.05)
    assert sum(negative) / len(negative) == pytest.approx(0.2, abs=0.05)


def test_reroll_in_returns_mode_shifts_the_return():
    rows = [row(0, 1, ret=0.01, x=1.0), row(1, 1, ret=0.01, x=-1.0)]
    out = ve.reroll(rows, edge=0.02, features=["x"], seed=1, mode="returns")
    assert out[0]["ret"] == pytest.approx(0.03)
    assert out[1]["ret"] == pytest.approx(-0.01)


def test_reroll_does_not_mutate_the_original_rows():
    rows = [row(0, 1, mkt=0.5, ret=0.01, x=1.0)]
    ve.reroll(rows, 0.2, ["x"], seed=1, mode="market")
    ve.reroll(rows, 0.2, ["x"], seed=1, mode="returns")
    assert rows[0]["y"] == 1 and rows[0]["ret"] == pytest.approx(0.01)


# ------------------------------------------------- implausibility_check


def test_implausible_deviation_is_flagged(capsys):
    ve.implausibility_check(median_dev=0.30, holdout=None, mode="market", use_offset=True)
    assert "IMPLAUSIBLE MAGNITUDE" in capsys.readouterr().out


def test_implausible_holdout_return_is_flagged(capsys):
    ve.implausibility_check(median_dev=0.01, holdout={"mean": 0.5}, mode="market", use_offset=True)
    out = capsys.readouterr().out
    assert "IMPLAUSIBLE MAGNITUDE" in out
    assert "lag every feature" in out.lower(), "must tell the user the decisive next test"


def test_plausible_results_are_not_flagged(capsys):
    ve.implausibility_check(median_dev=0.02, holdout={"mean": 0.01}, mode="market", use_offset=True)
    assert capsys.readouterr().out == ""


def test_deviation_check_is_skipped_without_a_market_price(capsys):
    """Without an offset the deviation is measured against 0.5, so a large
    gap says nothing about mispricing."""
    ve.implausibility_check(median_dev=0.40, holdout=None, mode="market", use_offset=False)
    assert capsys.readouterr().out == ""


def test_returns_mode_uses_a_stricter_return_limit(capsys):
    """0.15 per unit staked is unremarkable for a binary contract and absurd
    as a per-trade return."""
    ve.implausibility_check(0.01, {"mean": 0.15}, mode="returns", use_offset=True)
    assert "IMPLAUSIBLE" in capsys.readouterr().out
    ve.implausibility_check(0.01, {"mean": 0.15}, mode="market", use_offset=True)
    assert capsys.readouterr().out == ""


# -------------------------------------------------------------- hygiene


def test_constant_outcome_is_fatal():
    with pytest.raises(SystemExit, match="constant"):
        ve.hygiene([row(t, 1) for t in range(100)], use_offset=False)


def test_market_probability_outside_zero_one_is_fatal():
    rows = [row(t, t % 2, mkt=1.5) for t in range(100)]
    with pytest.raises(SystemExit, match=r"\[0,1\]"):
        ve.hygiene(rows, use_offset=True)


def test_duplicate_timestamps_are_warned_about(capsys):
    rows = [row(0, t % 2) for t in range(100)]
    ve.hygiene(rows, use_offset=False)
    assert "duplicate timestamps" in capsys.readouterr().out


def test_class_imbalance_is_warned_about(capsys):
    rows = [row(t, 1 if t < 1 else 0) for t in range(200)]
    ve.hygiene(rows, use_offset=False)
    assert "class imbalance" in capsys.readouterr().out


def test_hygiene_reports_whether_the_market_is_informative(capsys):
    rng = random.Random(6)
    informative = [row(t, 1 if rng.random() < p else 0, mkt=p)
                   for t, p in ((t, 0.2 if t % 2 else 0.8) for t in range(600))]
    ve.hygiene(informative, use_offset=True)
    assert "market is informative" in capsys.readouterr().out

    uninformative = [row(t, t % 2, mkt=0.5) for t in range(600)]
    ve.hygiene(uninformative, use_offset=True)
    assert "market adds nothing" in capsys.readouterr().out


# ----------------------------------------------------------------- load


def test_load_rejects_missing_columns(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("ts,won\n1,0\n")
    with pytest.raises(SystemExit, match="missing column"):
        ve.load(path, "ts", "won", None, None, ["absent"])


def test_load_sorts_by_time_and_skips_unparseable_rows(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("ts,won,mkt,x\n3,1,0.5,1\n1,0,0.5,2\nbad,1,0.5,3\n2,1,,4\n")
    rows = ve.load(path, "ts", "won", "mkt", None, ["x"])
    assert [r["t"] for r in rows] == [1.0, 3.0], "unparseable rows dropped, rest sorted"


def test_load_defaults_market_prob_when_no_column_is_given(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("ts,won,x\n1,1,0.5\n")
    assert ve.load(path, "ts", "won", None, None, ["x"])[0]["mkt"] == 0.5


# ------------------------------------------------------- end-to-end controls


def make_null(n=4000, seed=7):
    """A well-calibrated market and features carrying nothing. The honest
    answer is 'no edge'."""
    rng = random.Random(seed)
    rows = []
    for t in range(n):
        p = min(0.95, max(0.05, rng.gauss(0.5, 0.15)))
        rows.append(row(t, 1 if rng.random() < p else 0, mkt=p,
                        momentum=rng.gauss(0, 1), volume_ratio=rng.gauss(0, 1)))
    return rows


def make_leaky(n=4000, seed=8):
    """`momentum` contains a sliver of the outcome -- look-ahead. The
    validator cannot tell this from a discovery statistically; it is
    supposed to catch it on magnitude."""
    rng = random.Random(seed)
    rows = []
    for t in range(n):
        p = min(0.95, max(0.05, rng.gauss(0.5, 0.15)))
        y = 1 if rng.random() < p else 0
        rows.append(row(t, y, mkt=p,
                        momentum=(1.0 if y else -1.0) + rng.gauss(0, 0.4),
                        volume_ratio=rng.gauss(0, 1)))
    return rows


THRESHOLDS = [0.005, 0.01, 0.02, 0.03, 0.05, 0.08]


def test_uninformative_data_does_not_produce_a_positive_verdict():
    """THE false-positive control. If this ever fails, the tool is telling
    paying clients that noise is edge."""
    _, holdout, med_dev, _ = ve.pipeline(
        make_null(), ["momentum", "volume_ratio"], friction=0.01, mode="market",
        thresholds=THRESHOLDS, min_trades=100, use_offset=True,
    )
    assert holdout is None or holdout["verdict"] != "POSITIVE", (
        f"found edge in pure noise: {holdout}"
    )
    assert med_dev < 0.15, "the model should barely disagree with a fair market"


def test_leaked_outcome_is_caught_by_the_magnitude_check(capsys):
    """The leaky case must be flagged. No statistical test can separate a
    leak from a discovery -- the data really does contain the future -- so
    the magnitude check is the only thing standing between a client and a
    confident wrong answer."""
    _, holdout, med_dev, _ = ve.pipeline(
        make_leaky(), ["momentum", "volume_ratio"], friction=0.01, mode="market",
        thresholds=THRESHOLDS, min_trades=100, use_offset=True,
    )
    assert med_dev > 0.15, "a leak should produce an enormous model-market gap"
    ve.implausibility_check(med_dev, holdout, "market", use_offset=True)
    assert "IMPLAUSIBLE MAGNITUDE" in capsys.readouterr().out


def test_the_pipeline_finds_an_edge_it_injects_itself():
    """The positive control. Without it, 'no edge found' could equally mean
    'this code cannot find edge', and there would be no way to tell."""
    rows = ve.reroll(make_null(), edge=0.25, features=["momentum"], seed=3, mode="market")
    _, holdout, _, _ = ve.pipeline(
        rows, ["momentum", "volume_ratio"], friction=0.01, mode="market",
        thresholds=THRESHOLDS, min_trades=100, use_offset=True,
    )
    assert holdout is not None and holdout["verdict"] == "POSITIVE", (
        "the pipeline failed to detect a large edge it injected itself -- "
        "every negative result it reports is therefore uninterpretable"
    )


def test_holdout_is_untouched_when_nothing_passes_validation():
    """Discipline, not statistics: if no threshold produced a positive lower
    bound on validate, spending the holdout is just another draw from noise."""
    threshold, holdout, _, _ = ve.pipeline(
        make_null(), ["momentum", "volume_ratio"], friction=0.5, mode="market",
        thresholds=THRESHOLDS, min_trades=100, use_offset=True,
    )
    assert (threshold, holdout) == (None, None)


def test_detection_floor_rises_as_the_data_gets_noisier():
    """Bigger injected edges must be at least as detectable as smaller ones.
    A non-monotonic floor would make the headline number meaningless."""
    base = make_null(n=3000, seed=11)
    detected = []
    for edge in (0.0, 0.05, 0.30):
        sample = base if edge == 0 else ve.reroll(base, edge, ["momentum"], 3, "market")
        _, holdout, _, _ = ve.pipeline(
            sample, ["momentum", "volume_ratio"], friction=0.01, mode="market",
            thresholds=THRESHOLDS, min_trades=50, use_offset=True,
        )
        detected.append(holdout is not None and holdout["verdict"] == "POSITIVE")
    assert detected[-1], "the largest injected edge must be detected"
    assert not detected[0], "unmodified noise must not be detected"
