"""Tests for the sector-context layer.

Sector context is **informational only**: it must never change a pass/fail
verdict, track routing, or the default (absolute) ranking. The regression test
at the bottom enforces that HARD INVARIANT.
"""

from dataclasses import replace
from statistics import median

import pytest

from etf_screen.export import COLUMNS, build_rows
from etf_screen.providers import MockProvider
from etf_screen.screen import (
    SECTOR_MIN_PEERS,
    Company,
    annotate_sector_context,
    evaluate,
    rank,
    sector_medians,
)


def _co(ticker, sector, rev, prior, ocf, capex, sbc, mcap,
        fwd_pe=25.0, fwd_growth=20.0, net_income=500.0):
    """Build a sectored Company; share counts fixed so dilution is benign."""
    return Company(
        ticker=ticker, revenue_ttm=rev, revenue_ttm_prior=prior,
        ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
        diluted_shares_now=1000, diluted_shares_prior=1000,
        market_cap=mcap, forward_pe=fwd_pe, forward_eps_growth=fwd_growth,
        net_income_ttm=net_income, sector=sector,
    )


def _sector_set():
    """Five Tech names, four Healthcare, six Unknown — to exercise every branch."""
    cos = []
    # 5 Tech peers (>= min) with distinct revenues -> distinct metrics.
    for i, rev in enumerate([1000, 1200, 1500, 1800, 2200]):
        cos.append(_co(f"T{i}", "Technology", rev, rev * 0.8,
                       rev * 0.4, rev * 0.05, rev * 0.05, rev * 6))
    # 4 Healthcare peers (< min) -> no median.
    for i, rev in enumerate([900, 1100, 1300, 1600]):
        cos.append(_co(f"H{i}", "Healthcare", rev, rev * 0.85,
                       rev * 0.3, rev * 0.04, rev * 0.04, rev * 5))
    # 6 Unknown-sector peers -> excluded despite being numerous.
    for i, rev in enumerate([800, 1000, 1200, 1400, 1600, 1800]):
        cos.append(_co(f"U{i}", "Unknown", rev, rev * 0.8,
                       rev * 0.35, rev * 0.05, rev * 0.05, rev * 5))
    return [evaluate(c) for c in cos]


# --- 1. median computation --------------------------------------------------
def test_sector_medians_match_statistics_median():
    results = _sector_set()
    stats = sector_medians(results)
    assert set(stats) == {"Technology"}          # only sector with >= 5 peers
    st = stats["Technology"]
    assert st.n == 5
    tech = [r for r in results if r.sector == "Technology"]
    for metric, attr in [("rule40", "rule40"), ("p_s", "p_s"),
                         ("growth", "growth"), ("adj_margin", "adj_margin"),
                         ("sbc_pct", "sbc_pct")]:
        assert st.medians[metric] == pytest.approx(
            median(getattr(r, attr) for r in tech))


def test_min_peers_guard_excludes_small_sector():
    results = _sector_set()
    stats = sector_medians(results)
    assert "Healthcare" not in stats          # only 4 peers, below SECTOR_MIN_PEERS
    assert SECTOR_MIN_PEERS == 5


def test_unknown_sector_excluded_from_medians():
    results = _sector_set()
    stats = sector_medians(results)
    assert "Unknown" not in stats             # excluded even with 6 peers


def test_peg_median_ignores_na_names():
    # 5 Tech peers; two are GAAP-unprofitable (forward_pe=None) -> PEG N/A.
    cos = [
        _co("A", "Technology", 1000, 800, 400, 50, 50, 6000, fwd_pe=20),
        _co("B", "Technology", 1100, 880, 440, 55, 55, 6600, fwd_pe=40),
        _co("C", "Technology", 1200, 960, 480, 60, 60, 7200, fwd_pe=60),
        _co("D", "Technology", 1300, 1040, 520, 65, 65, 7800, fwd_pe=None),
        _co("E", "Technology", 1400, 1120, 560, 70, 70, 8400, fwd_pe=None),
    ]
    results = [evaluate(c) for c in cos]
    st = sector_medians(results)["Technology"]
    pegs = [r.peg for r in results if r.peg is not None]
    assert len(pegs) == 3
    assert st.medians["peg"] == pytest.approx(median(pegs))


# --- 2. annotation ----------------------------------------------------------
def test_annotation_sets_deltas_for_covered_sector():
    results = _sector_set()
    annotate_sector_context(results)
    tech = next(r for r in results if r.sector == "Technology")
    ctx = tech.sector_context
    assert ctx.available and ctx.peers == 5
    assert ctx.deltas["rule40"] == pytest.approx(tech.rule40 - ctx.medians["rule40"])


def test_annotation_flags_too_few_peers_and_unknown():
    results = _sector_set()
    annotate_sector_context(results)
    hc = next(r for r in results if r.sector == "Healthcare")
    un = next(r for r in results if r.sector == "Unknown")
    assert not hc.sector_context.available and "too few peers" in hc.sector_context.note
    assert not un.sector_context.available and "unknown" in un.sector_context.note.lower()


# --- 3. HARD INVARIANT: verdicts + default rank unchanged -------------------
def test_sector_feature_does_not_change_verdicts_or_rank():
    with_sectors = _sector_set()
    # Identical companies with sector stripped -> the "without the feature" run.
    without = [evaluate(replace(r.company, sector="Unknown")) for r in with_sectors]

    # Verdicts (passed + track) identical name-for-name.
    assert [(r.ticker, r.passed, r.track) for r in with_sectors] == \
           [(r.ticker, r.passed, r.track) for r in without]

    # Annotating must not perturb the default absolute ranking.
    before = [r.ticker for r in rank(with_sectors)]
    annotate_sector_context(with_sectors)
    after = [r.ticker for r in rank(with_sectors)]
    assert before == after == [r.ticker for r in rank(without)]


def test_sector_relative_rank_keeps_same_survivor_set():
    results = _sector_set()
    annotate_sector_context(results)
    absolute = {r.ticker for r in rank(results, mode="rule40")}
    relative = {r.ticker for r in rank(results, mode="sector-relative")}
    assert absolute == relative          # only the order may differ, not the set


# --- 4. provider + export ---------------------------------------------------
def test_mock_provider_populates_sector():
    c = MockProvider().fetch("MSFT")
    assert c.sector == "Technology" and c.industry


def test_export_includes_sector_columns():
    results = _sector_set()
    annotate_sector_context(results)
    rows = build_rows(results, [], {})
    assert "sector" in COLUMNS and "sector_med_rule40" in COLUMNS
    tech = next(row for row in rows if row["ticker"].startswith("T"))
    assert tech["sector"] == "Technology"
    assert tech["sector_med_rule40"] != "N/A"   # covered sector has a median
