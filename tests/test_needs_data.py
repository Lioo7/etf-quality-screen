"""Tests for the NEEDS_DATA classification (v0.2.1 Fix 3).

"PEG untestable" is not "PEG failed": a profitable name whose ONLY blocker is
an uncomputable PEG (no forward estimates — the unsponsored-ADR case, e.g.
Keyence/KYCCF) while every testable gate passes is set aside as NEEDS_DATA
with an overrides.json hint — never rejected as if a test had failed, never in
the shortlist. The PriceyButProfitable invariant is untouched: a *computable*
failed PEG keeps its v0.2 behavior (WATCHLIST / REJECTED), and Track B stays
barred for profitable names either way.
"""

from etf_screen.screen import ScreenStatus, evaluate, rank
from test_screen import ACCEPTANCE, make


def _keyence_like(**kw):
    """Profitable, every filter passing — except PEG, which is N/A."""
    return make("KYC", 1000, 700, 400, 50, 50, 1000, 1000, 5000, None, 0.0,
                net_income=200, **kw)


def test_peg_untestable_all_else_passing_is_needs_data():
    r = evaluate(_keyence_like())
    assert r.status is ScreenStatus.NEEDS_DATA and r.needs_data
    assert not r.passed and not r.watchlist and r.track == "—"
    assert any("overrides.json" in reason for reason in r.reasons)
    assert any("market_cap" in reason for reason in r.reasons)
    # Quality cleared -> the advisory annotations attach (like WATCHLIST)...
    assert r.roic_history is not None and r.margin_profile is not None
    # ...but there is no unlock price: no valuation filter failed.
    assert r.unlock_ps_price is None and r.unlock_peg_price is None


def test_computable_failed_peg_keeps_v02_behavior():
    # The PriceyButProfitable archetype fails a COMPUTABLE PEG -> WATCHLIST
    # (still not passed, still barred from Track B), never NEEDS_DATA.
    r = evaluate(ACCEPTANCE["PriceyButProfitable"])
    assert r.status is ScreenStatus.WATCHLIST
    assert not r.needs_data and not r.passed and not r.track_b


def test_quality_gate_failer_with_peg_na_stays_plain_rejected():
    # Goodwill 60% of assets: more estimate data could not have saved it, so
    # the hint would mislead -> plain REJECTED, hypothetical gates unattached.
    r = evaluate(_keyence_like(goodwill=1200, total_assets=2000))
    assert r.status is ScreenStatus.REJECTED and not r.needs_data
    assert not any("overrides.json" in reason for reason in r.reasons)
    assert r.consistency is None and r.roic_history is None


def test_ps_failer_with_peg_na_stays_plain_rejected():
    # P/S fails alongside the untestable PEG: a REAL valuation failure exists,
    # so this is an ordinary rejection, not a data problem.
    r = evaluate(make("KYC", 1000, 700, 400, 50, 50, 1000, 1000, 30000, None, 0.0,
                      net_income=200))
    assert r.status is ScreenStatus.REJECTED and not r.needs_data
    assert any("P/S" in reason for reason in r.reasons)


def test_unprofitable_peg_na_is_never_needs_data():
    # PEG is structurally N/A on Track B; an unprofitable name is routed and
    # judged there as always (here dilution >= growth fails the quality gate
    # -> plain rejection, no "supply estimates" hint).
    r = evaluate(make("BURNY", 1000, 700, 400, 50, 50, 1500, 1000, 5000, None, 0.0,
                      net_income=-100))
    assert r.status is ScreenStatus.REJECTED and not r.needs_data
    assert not any("overrides.json" in reason for reason in r.reasons)


def test_insufficient_history_is_still_needs_data():
    # The live KYCCF profile: an unsponsored ADR pairs missing estimates WITH
    # thin annual history. Neither is a failed test, so the name stays
    # NEEDS_DATA (the attached consistency result reports INSUFFICIENT) —
    # only a real gate FAIL rejects the candidacy (next test). This is
    # deliberately looser than WATCHLIST, which claims "every quality gate
    # passed" and therefore requires a consistency PASS.
    r = evaluate(_keyence_like(), consistency_years=3)
    assert r.status is ScreenStatus.NEEDS_DATA
    assert r.consistency is not None
    assert r.consistency.status.name == "INSUFFICIENT"


def test_consistency_fail_blocks_needs_data():
    # A real historical failure (a sub-40 windowed year under strict) is a
    # verdict, not a data gap -> estimates could not have saved it -> REJECTED.
    from etf_screen.screen import AnnualPeriod
    c = _keyence_like()
    c.history = [
        AnnualPeriod("2021", 600, 240, 30, 30),
        AnnualPeriod("2022", 630, 240, 30, 30),   # decel year, Rule40 < 40
        AnnualPeriod("2023", 800, 320, 40, 40),
        AnnualPeriod("2024", 1000, 400, 50, 50),
    ]
    r = evaluate(c, consistency_years=3, consistency_mode="strict")
    assert r.status is ScreenStatus.REJECTED and not r.needs_data


def test_needs_data_never_enters_rank():
    r = evaluate(_keyence_like())
    assert rank([r]) == []


def test_recent_ipo_override_wins_over_needs_data():
    from datetime import date
    c = _keyence_like()
    c.ipo_date = "2026-03-01"
    r = evaluate(c, today=date(2026, 7, 8))
    assert r.status is ScreenStatus.RECENT_IPO and not r.needs_data


def test_needs_data_rows_sort_after_watchlist_before_rejected():
    from etf_screen.export import build_rows
    needs = evaluate(_keyence_like())
    watch = evaluate(ACCEPTANCE["PriceyButProfitable"])
    plain = evaluate(ACCEPTANCE["OverpricedSlowGrower"])
    rows = build_rows([plain, needs, watch], [], {})
    assert [row["status"] for row in rows] == ["WATCHLIST", "NEEDS_DATA", "REJECTED"]


def test_mock_opaque_ticker_is_needs_data():
    from etf_screen.providers import MockProvider
    r = evaluate(MockProvider().fetch("OPAQUE"))
    assert r.status is ScreenStatus.NEEDS_DATA
