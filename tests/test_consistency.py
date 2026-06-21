"""Tests for the multi-year consistency gate (annual durability filter).

The gate layers on top of the UNCHANGED four-filter / two-track snapshot logic.
These fixtures build raw annual histories that exercise every branch — PASS,
FAIL, and INSUFFICIENT — for both tracks, plus the bypass and bucketing rules.
"""

import pytest

from etf_screen.export import rule40_hist
from etf_screen.screen import (
    AnnualPeriod, Company, ConsistencyStatus, ScreenStatus, _fmt_trajectory,
    _period_metrics, _rule40_consistent, evaluate, track_a_consistency,
    track_b_consistency,
)


# --- company builders: snapshot numbers chosen to clear a single track --------
def track_a_company(history, **kw):
    """A profitable name that clears snapshot Track A; history drives the gate."""
    base = dict(
        ticker="TA", revenue_ttm=1000, revenue_ttm_prior=700,
        ocf_ttm=400, capex_ttm=50, sbc_ttm=50,
        diluted_shares_now=1000, diluted_shares_prior=1000,
        market_cap=5000, forward_pe=30.0, forward_eps_growth=20.0,
        net_income_ttm=200,
    )
    base.update(kw)
    return Company(history=history, **base)


def track_b_company(history, **kw):
    """A GAAP-unprofitable name that clears snapshot Track B; history drives the gate."""
    base = dict(
        ticker="TB", revenue_ttm=3500, revenue_ttm_prior=2400,
        ocf_ttm=1100, capex_ttm=120, sbc_ttm=500,
        diluted_shares_now=245, diluted_shares_prior=235,
        market_cap=70000, forward_pe=None, forward_eps_growth=30.0,
        net_income_ttm=-150,
    )
    base.update(kw)
    return Company(history=history, **base)


# Track A history with every windowed Rule of 40 >= 40 (a durable compounder).
DURABLE_HISTORY = [
    AnnualPeriod("2021", 500, 200, 25, 25),
    AnnualPeriod("2022", 650, 260, 30, 30),
    AnnualPeriod("2023", 845, 340, 40, 40),
    AnnualPeriod("2024", 1100, 440, 50, 50),
]
# ALNY-like: a windowed year decelerates below 40 (2022 ~= 33.6).
DECEL_HISTORY = [
    AnnualPeriod("2021", 600, 240, 30, 30),
    AnnualPeriod("2022", 630, 240, 30, 30),
    AnnualPeriod("2023", 800, 320, 40, 40),
    AnnualPeriod("2024", 1000, 400, 50, 50),
]
# Track B history, adjusted FCF > 0 every year.
TRACK_B_PASS_HISTORY = [
    AnnualPeriod("2022", 1600, 500, 60, 250),
    AnnualPeriod("2023", 2400, 800, 90, 380),
    AnnualPeriod("2024", 3500, 1100, 120, 500),
]
# Track B history with one negative adjusted-FCF year (2022 = -100).
TRACK_B_FAIL_HISTORY = [
    AnnualPeriod("2022", 1600, 200, 50, 250),
    AnnualPeriod("2023", 2400, 600, 80, 300),
    AnnualPeriod("2024", 3500, 1100, 120, 500),
]


# --- the shared per-period helper must mirror the snapshot math ---------------
def test_period_metrics_matches_snapshot():
    c = track_a_company(DURABLE_HISTORY)
    r = evaluate(c)  # snapshot only (default consistency_years=0)
    growth, adj_fcf, adj_margin, rule40 = _period_metrics(
        c.revenue_ttm, c.revenue_ttm_prior, c.ocf_ttm, c.capex_ttm, c.sbc_ttm
    )
    assert growth == pytest.approx(r.growth)
    assert adj_fcf == pytest.approx(r.adj_fcf)
    assert adj_margin == pytest.approx(r.adj_margin)
    assert rule40 == pytest.approx(r.rule40)


# --- Track A ------------------------------------------------------------------
def test_track_a_pass():
    r = evaluate(track_a_company(DURABLE_HISTORY), consistency_years=3,
                 consistency_mode="strict")
    assert r.passed and r.track == "Track A"
    assert r.consistency.status is ConsistencyStatus.PASS
    assert not r.insufficient_history


def test_track_a_fail_is_demoted_by_gate_not_snapshot():
    c = track_a_company(DECEL_HISTORY)
    # Passes the SNAPSHOT alone...
    assert evaluate(c, consistency_years=0).passed
    # ...but is rejected once the STRICT gate runs (mirrors the ALNY anomaly).
    r = evaluate(c, consistency_years=3, consistency_mode="strict")
    assert not r.passed and r.track == "—"
    assert r.consistency.status is ConsistencyStatus.FAIL
    assert not r.insufficient_history
    assert any("consistency gate" in reason for reason in r.reasons)
    assert any("Rule40 Hist" in reason for reason in r.reasons)


def test_track_a_insufficient():
    # Only 2 periods cannot form 3 windowed growth-years (need 4).
    r = evaluate(track_a_company(DURABLE_HISTORY[-2:]), consistency_years=3,
                 consistency_mode="strict")
    assert not r.passed and r.track == "—"
    assert r.insufficient_history
    assert r.consistency.status is ConsistencyStatus.INSUFFICIENT
    assert r.consistency.years_available == 2 and r.consistency.years_required == 4


# --- Track B ------------------------------------------------------------------
def test_track_b_pass():
    r = evaluate(track_b_company(TRACK_B_PASS_HISTORY), consistency_years=3)
    assert r.passed and r.track == "Track B"
    assert r.consistency.status is ConsistencyStatus.PASS


def test_track_b_fail():
    c = track_b_company(TRACK_B_FAIL_HISTORY)
    assert evaluate(c, consistency_years=0).passed  # snapshot clears it
    r = evaluate(c, consistency_years=3)
    assert not r.passed and r.track == "—"
    assert r.consistency.status is ConsistencyStatus.FAIL
    assert any("AdjFCF Hist" in reason for reason in r.reasons)


def test_track_b_insufficient():
    # Floor is min(2, 3) = 2; a single year is below it.
    r = evaluate(track_b_company(TRACK_B_PASS_HISTORY[-1:]), consistency_years=3)
    assert not r.passed
    assert r.insufficient_history
    assert r.consistency.status is ConsistencyStatus.INSUFFICIENT
    assert r.consistency.years_required == 2 and r.consistency.years_available == 1


# --- bypass and bucketing -----------------------------------------------------
def test_bypass_reproduces_snapshot_verdict():
    c = track_a_company(DECEL_HISTORY)  # would FAIL the gate, but it's off
    snap = evaluate(c, consistency_years=0)
    assert snap.passed and snap.track == "Track A"
    assert snap.consistency is None and not snap.insufficient_history


def test_snapshot_failer_without_history_is_normal_rejection():
    # Fails the snapshot on P/S; empty history must NOT make it INSUFFICIENT.
    c = track_a_company([], market_cap=50000)  # P/S 50 >> guardrail
    r = evaluate(c, consistency_years=3)
    assert not r.passed
    assert not r.insufficient_history
    assert r.consistency is None  # gate never ran — history is moot


# --- trajectory rendering -----------------------------------------------------
def test_trajectory_is_oldest_to_newest():
    res = track_a_consistency(DURABLE_HISTORY, 3)
    labels = [label for label, _ in res.rule40_by_year]
    assert labels == ["2022", "2023", "2024"]  # oldest -> newest


def test_trajectory_formatting():
    pairs = [("2022", 55.1), ("2023", 42.0), ("2024", 40.5)]
    assert _fmt_trajectory(pairs) == "[55.1 -> 42.0 -> 40.5]"


def test_rule40_hist_export_blank_without_gate():
    r = evaluate(track_a_company(DURABLE_HISTORY), consistency_years=0)
    assert rule40_hist(r) == ""
    r_gated = evaluate(track_a_company(DURABLE_HISTORY), consistency_years=3)
    assert rule40_hist(r_gated).startswith("[") and "->" in rule40_hist(r_gated)


# --- history dict coercion (cache load path) ----------------------------------
def test_history_dicts_coerced_to_annual_period():
    c = Company(
        "X", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30.0, 20.0, 200,
        history=[{"fiscal_label": "2024", "revenue": 1000, "ocf": 400,
                  "capex": 50, "sbc": 50}],
    )
    assert isinstance(c.history[0], AnnualPeriod)
    assert c.history[0].fiscal_label == "2024"


def test_track_b_consistency_direct_floor():
    # Direct call: floor is min(2, years); 1 available year -> INSUFFICIENT.
    res = track_b_consistency(TRACK_B_PASS_HISTORY[-1:], 3)
    assert res.status is ConsistencyStatus.INSUFFICIENT


# --- strict vs trend decision (windowed Rule-of-40 values, oldest -> newest) ---
@pytest.mark.parametrize("vals", [
    [-0.2, 127.9, 138.1],   # APP-like: sub-floor launch year (i==0) forgiven
    [37.2, 38.9, 43.8],     # ADBE-like: never < 40 in strict terms, but trends up
    [30.2, 38.9, 43.6],     # SHOP-like: averages well, ends strong
    [-5.0, 60.0, 70.0],     # launch year forgiven, then compounding
])
def test_trend_pass(vals):
    assert _rule40_consistent(vals, "trend")


@pytest.mark.parametrize("vals", [
    [66.4, 9.0, 68.3],      # ALNY-like: lumpy sub-floor collapse (9.0 from 66.4)
    [22.0, 24.0, 41.0],     # chronic weakness: mean 29 < 30 backstop
    [50.0, 24.0, 41.0],     # lumpy collapse mid-window (24 < 25 and < prior 50)
])
def test_trend_reject(vals):
    assert not _rule40_consistent(vals, "trend")


def test_trend_recency_anchor_requires_latest_year():
    # Strong history but the latest year slips below 40 -> trend FAIL.
    assert not _rule40_consistent([60.0, 70.0, 38.0], "trend")


def test_strict_is_stricter_than_trend():
    vals = [37.2, 38.9, 43.8]            # a dip below 40 that trend forgives
    assert not _rule40_consistent(vals, "strict")
    assert _rule40_consistent(vals, "trend")


def test_decel_history_passes_under_trend_but_fails_strict():
    c = track_a_company(DECEL_HISTORY)
    assert not evaluate(c, consistency_years=3, consistency_mode="strict").passed
    r = evaluate(c, consistency_years=3, consistency_mode="trend")
    assert r.passed and r.track == "Track A"
    assert r.consistency.status is ConsistencyStatus.PASS


def test_track_b_ignores_consistency_mode():
    # Track B keys on adjusted FCF only; the mode flag must not change its verdict.
    c = track_b_company(TRACK_B_PASS_HISTORY)
    strict = evaluate(c, consistency_years=3, consistency_mode="strict")
    trend = evaluate(c, consistency_years=3, consistency_mode="trend")
    assert strict.passed and trend.passed and strict.track == trend.track == "Track B"


# --- status precedence: a balance-sheet failure beats a history shortfall -----
def test_balance_sheet_failure_precedes_insufficient_history():
    # Goodwill 60% AND only 2 annual years: the definitive balance-sheet failure
    # wins, so the name is REJECTED, never bucketed as INSUFFICIENT_HISTORY.
    c = track_a_company(DURABLE_HISTORY[-2:], goodwill=1200, total_assets=2000)
    r = evaluate(c, consistency_years=3, consistency_mode="strict")
    assert r.status is ScreenStatus.REJECTED
    assert not r.insufficient_history
    assert any("Goodwill/Assets" in reason for reason in r.reasons)
