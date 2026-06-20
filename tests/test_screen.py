"""Acceptance + unit tests for the screening core.

The acceptance fixtures lock in the validated behavior of the four-filter /
two-track methodology. The `PriceyButProfitable` case is the most important: it
guards against a profitable name that fails PEG slipping into Track B.
"""

import math

import pytest

from etf_screen.screen import Company, evaluate, rank


def make(ticker, rev, prior, ocf, capex, sbc, shares_now, shares_prior,
         mcap, fwd_pe, fwd_growth, net_income=1000):
    """Build a Company from the acceptance-table columns (values in $M).

    ``net_income`` gates routing (>0 -> Track A only, <=0 -> Track B eligible);
    it defaults positive so the divide-by-zero edge fixtures stay profitable.
    """
    return Company(
        ticker=ticker,
        revenue_ttm=rev, revenue_ttm_prior=prior,
        ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
        diluted_shares_now=shares_now, diluted_shares_prior=shares_prior,
        market_cap=mcap, forward_pe=fwd_pe, forward_eps_growth=fwd_growth,
        net_income_ttm=net_income,
    )


# --- acceptance fixtures ---------------------------------------------------
# ticker, rev, prior, ocf, capex, sbc, shares_now, shares_prior, mcap, fwd_pe, fwd_growth
ACCEPTANCE = {
    "CleanCompounder": make(
        "CleanCompounder", 30000, 24000, 12000, 1500, 1500, 1000, 1010, 270000, 28, 22,
        net_income=6000),
    "HyperGrowthNoProfit": make(
        "HyperGrowthNoProfit", 3000, 2000, 600, 150, 400, 500, 520, 54000, None, 40,
        net_income=-200),
    "PriceyButProfitable": make(
        "PriceyButProfitable", 20000, 16000, 7000, 1000, 1200, 1500, 1510, 200000, 60, 12,
        net_income=4000),
    "OverpricedSlowGrower": make(
        "OverpricedSlowGrower", 50000, 46300, 18000, 3000, 2000, 2000, 2000, 600000, 24, 9,
        net_income=9000),
    "DilutionTrap": make(
        "DilutionTrap", 5000, 4000, 1400, 200, 1100, 800, 880, 60000, 35, 30,
        net_income=300),
    "BorderlineForty": make(
        "BorderlineForty", 10000, 8800, 2600, 600, 700, 900, 905, 70000, 30, 18,
        net_income=1200),
}


def test_clean_compounder_passes_track_a():
    r = evaluate(ACCEPTANCE["CleanCompounder"])
    assert r.passed and r.track == "Track A"


def test_hypergrowth_passes_track_b():
    r = evaluate(ACCEPTANCE["HyperGrowthNoProfit"])
    assert r.passed and r.track == "Track B"
    assert r.peg is None  # GAAP-unprofitable -> Track B eligible, PEG exempt


def test_pricey_but_profitable_is_rejected():
    """The critical guard: profitable + failing PEG must NOT reach Track B."""
    r = evaluate(ACCEPTANCE["PriceyButProfitable"])
    assert not r.passed
    assert not r.track_b
    assert r.peg is not None and r.peg == pytest.approx(5.0)


def test_profitable_with_nonpositive_forward_growth_is_rejected():
    """Routing-leak guard: a profitable name whose PEG is N/A only because
    forward EPS growth is <=0 must be REJECTED, never routed to Track B."""
    # net_income > 0, forward P/E present, but forward EPS growth <= 0 -> peg None.
    # Strong trailing growth + positive adj FCF so every other filter would pass.
    c = make("ProfitNoFwdGrowth", 3000, 2000, 900, 100, 100, 500, 500, 6000, 25, -5,
             net_income=400)
    r = evaluate(c)
    assert r.peg is None
    assert not r.track_b
    assert not r.passed


def test_profitable_without_forward_pe_is_rejected():
    """A profitable name with no forward P/E coverage still must clear Track A;
    PEG being N/A does not open Track B for a profitable company."""
    c = make("ProfitNoFwdPE", 3000, 2000, 900, 100, 100, 500, 500, 6000, None, 20,
             net_income=400)
    r = evaluate(c)
    assert r.peg is None
    assert not r.track_b
    assert not r.passed


def test_overpriced_slow_grower_rejected():
    r = evaluate(ACCEPTANCE["OverpricedSlowGrower"])
    assert not r.passed
    assert not r.pass_rule40 and not r.pass_ps and not r.pass_peg


def test_dilution_trap_rejected_on_sbc():
    r = evaluate(ACCEPTANCE["DilutionTrap"])
    assert not r.passed
    assert not r.pass_sbc
    assert r.sbc_pct == pytest.approx(22.0)


def test_borderline_forty_rejected():
    r = evaluate(ACCEPTANCE["BorderlineForty"])
    assert not r.passed
    assert r.rule40 == pytest.approx(26.64, abs=0.05)


# --- edge cases in isolation ----------------------------------------------
def test_divide_by_zero_guard_zero_prior_revenue():
    c = make("ZeroRev", 1000, 0, 500, 50, 10, 100, 100, 5000, 20, 15)
    r = evaluate(c)
    assert not r.evaluable
    assert not r.passed
    assert math.isnan(r.growth)


def test_divide_by_zero_guard_zero_prior_shares():
    c = make("ZeroShares", 1000, 800, 500, 50, 10, 100, 0, 5000, 20, 15)
    r = evaluate(c)
    assert not r.evaluable
    assert not r.passed
    assert math.isnan(r.dilution)


def test_negative_growth_guardrail_fails_any_positive_ps():
    # Revenue shrank -> guardrail ceiling is negative -> any positive P/S fails.
    c = make("Shrinking", 9000, 10000, 4000, 200, 100, 100, 100, 30000, None, 0,
             net_income=-50)
    r = evaluate(c)
    assert r.growth < 0
    assert not r.pass_ps
    assert not r.passed


def test_peg_na_routes_to_track_b_when_quality_gate_holds():
    # GAAP-unprofitable (forward_pe=None) but strong: should reach Track B.
    c = make("NAtoB", 3000, 2000, 600, 150, 400, 500, 520, 54000, None, 40,
             net_income=-100)
    r = evaluate(c)
    assert r.peg is None
    assert r.track == "Track B" and r.passed


def test_peg_na_but_quality_gate_fails_is_rejected():
    # Unprofitable; dilution (>53%) >= growth (50%) -> quality gate fails -> reject.
    c = make("NAfail", 3000, 2000, 600, 150, 400, 800, 520, 54000, None, 40,
             net_income=-100)
    r = evaluate(c)
    assert r.peg is None
    assert not r.quality_gate
    assert not r.passed


def test_rank_orders_by_rule40_desc():
    results = [evaluate(c) for c in ACCEPTANCE.values()]
    ranked = rank(results)
    rule40s = [r.rule40 for r in ranked]
    assert rule40s == sorted(rule40s, reverse=True)
    assert all(r.passed for r in ranked)
