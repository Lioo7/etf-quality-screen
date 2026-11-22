"""Tests for WATCHLIST mode (Feature 5, v0.2).

Names that pass every quality gate but fail ONLY valuation (P/S and/or a
computable-but-failed PEG) are classified WATCHLIST with pre-computed unlock
prices — the buy trigger computed on a calm day. Critical boundaries: quality
failers stay plain REJECTED, a profitable name with PEG N/A stays barred (no
price fixes missing estimates), and WATCHLIST never enters the survivor rank.
"""

from dataclasses import replace

import pytest

from etf_screen.screen import (
    UNLOCK_IMPLIED_NOTE, AnnualPeriod, ScreenStatus, evaluate, rank,
)
from test_screen import ACCEPTANCE, make


def _wonderful_but_pricey(mcap=30000, fwd_pe=90.0, **kw):
    """DURABLE's fundamentals at a silly price: P/S 30 (guardrail 21.4), PEG 4.5."""
    return make("WNDR", 1000, 700, 400, 50, 50, 1000, 1000, mcap, fwd_pe, 20,
                net_income=200, **kw)


GOOD_EQ_HISTORY = [
    AnnualPeriod("2021", 500, 200, 25, 25, net_income=150),
    AnnualPeriod("2022", 650, 260, 30, 30, net_income=180),
    AnnualPeriod("2023", 845, 340, 40, 40, net_income=220),
    AnnualPeriod("2024", 1100, 440, 50, 50, net_income=260),
]


# --- classification boundaries ------------------------------------------------
def test_valuation_only_failure_is_watchlist():
    r = evaluate(_wonderful_but_pricey())
    assert r.status is ScreenStatus.WATCHLIST and r.watchlist
    assert not r.passed and r.track == "—"
    # Both valuation filters failed; both reasons reported.
    assert any("P/S" in reason for reason in r.reasons)
    assert any("PEG" in reason for reason in r.reasons)


def test_quality_and_valuation_failure_stays_plain_rejected():
    # Same pricey snapshot but goodwill 60% of assets -> quality gate fails too.
    r = evaluate(_wonderful_but_pricey(goodwill=1200, total_assets=2000))
    assert r.status is ScreenStatus.REJECTED and not r.watchlist
    # Per the fixed architecture a snapshot failer's hypothetical gates are
    # moot: only the valuation reasons are reported, nothing is attached.
    assert not any("Goodwill" in reason for reason in r.reasons)
    assert r.consistency is None and r.roic_history is None


def test_profitable_peg_na_is_not_watchlist():
    # PEG N/A (no forward P/E): no price fixes missing estimates -> plain reject.
    r = evaluate(_wonderful_but_pricey(fwd_pe=None))
    assert r.status is ScreenStatus.REJECTED and not r.watchlist
    assert any("PEG not computable" in reason for reason in r.reasons)


def test_pricey_but_profitable_acceptance_name_is_the_canonical_watchlist():
    # The PriceyButProfitable archetype (fails ONLY PEG) is exactly the case
    # this feature exists for. The critical invariant is untouched: it does not
    # pass, and it does not slip into Track B.
    r = evaluate(ACCEPTANCE["PriceyButProfitable"])
    assert r.status is ScreenStatus.WATCHLIST
    assert not r.passed and not r.track_b
    assert r.unlock_peg_price is not None
    assert r.unlock_ps_price is None   # P/S already passes; no unlock needed


def test_unprofitable_track_b_like_ps_failure_is_watchlist_without_peg_unlock():
    # Track-B quality (unprofitable, quality gate OK) at a too-high P/S.
    c = make("TBWL", 3000, 2000, 600, 150, 400, 500, 520, 90000, None, 40,
             net_income=-200)
    r = evaluate(c)
    assert r.status is ScreenStatus.WATCHLIST
    assert r.unlock_ps_price is not None
    assert r.unlock_peg_price is None   # Track B has no PEG concept


def test_watchlist_gated_by_balance_sheet_quality():
    # Valuation-only snapshot failure BUT 4x levered -> not watchlist material.
    r = evaluate(_wonderful_but_pricey(total_debt=1400, cash_and_equivalents=0,
                                       operating_income=300))
    assert r.status is ScreenStatus.REJECTED and not r.watchlist


def test_watchlist_requires_consistency_pass_when_gate_enabled():
    c = _wonderful_but_pricey()
    c.history = GOOD_EQ_HISTORY
    r = evaluate(c, consistency_years=3, consistency_mode="strict")
    assert r.status is ScreenStatus.WATCHLIST  # durable history -> still watchlist
    # Too little history -> candidacy fails -> plain REJECTED (never a
    # misleading INSUFFICIENT_HISTORY for a name that failed the snapshot).
    c2 = _wonderful_but_pricey()
    c2.history = GOOD_EQ_HISTORY[-2:]
    r2 = evaluate(c2, consistency_years=3, consistency_mode="strict")
    assert r2.status is ScreenStatus.REJECTED and not r2.insufficient_history


# --- unlock-price math ----------------------------------------------------------
def _priced(c, price):
    return replace(c, current_price=price)


def test_unlock_ps_price_uses_same_share_and_revenue_base():
    # Traded price 30 == mcap / shares (30000 / 1000): an ordinary US share.
    r = evaluate(_priced(_wonderful_but_pricey(), 30.0))
    # At the unlock price the recomputed P/S must equal the guardrail exactly.
    unlock_mcap = r.unlock_ps_price * 1000            # price x diluted shares
    assert unlock_mcap / 1000 == pytest.approx(r.ps_guardrail * 1)  # P/S == ceiling
    assert r.unlock_ps_price == pytest.approx(30 * (r.ps_guardrail / r.p_s))
    assert r.unlock_basis == "price"


def test_unlock_peg_price_scales_to_peg_ceiling():
    r = evaluate(_priced(_wonderful_but_pricey(), 30.0))
    assert r.peg == pytest.approx(4.5)
    assert r.unlock_peg_price == pytest.approx(30 * (2.0 / 4.5))


# --- unlock from the traded price (#62, D20) ---------------------------------------
def test_adr_unlock_is_on_the_traded_price_scale():
    # 8 ordinary shares per ADS (BABA, BIDU): cap / ordinary shares implies 30, but
    # the ADS trades at 240. P/S and PEG scale linearly with price, so the unlock is
    # the traded price x (ceiling / current value), with no share-count assumption.
    r = evaluate(_priced(_wonderful_but_pricey(), 240.0))
    assert r.status is ScreenStatus.WATCHLIST and r.unlock_basis == "price"
    assert r.unlock_peg_price == pytest.approx(240 * (2.0 / 4.5))
    assert r.unlock_ps_price == pytest.approx(240 * (r.ps_guardrail / r.p_s))
    assert UNLOCK_IMPLIED_NOTE not in r.low_confidence_notes


def test_same_currency_unlock_is_unchanged_within_rounding():
    base = ACCEPTANCE["PriceyButProfitable"]
    old = evaluate(base)  # no traded price: the old market cap / shares formula
    new = evaluate(_priced(base, base.market_cap / base.diluted_shares_now))
    assert new.unlock_basis == "price" and old.unlock_basis == "implied"
    assert new.unlock_peg_price == pytest.approx(old.unlock_peg_price, rel=1e-9)


def test_no_traded_price_falls_back_to_the_implied_price_with_a_note():
    r = evaluate(_wonderful_but_pricey())  # current_price None
    assert r.unlock_basis == "implied"
    assert r.unlock_peg_price == pytest.approx(30 * (2.0 / 4.5))
    assert UNLOCK_IMPLIED_NOTE in r.low_confidence_notes
    assert UNLOCK_IMPLIED_NOTE not in r.company.low_confidence  # display only


def test_unlock_pct_from_price_is_the_ceiling_ratio():
    r = evaluate(_priced(_wonderful_but_pricey(), 240.0))
    assert r.unlock_peg_pct == pytest.approx(100 * (2.0 / 4.5 - 1))
    assert r.unlock_ps_pct == pytest.approx(100 * (r.ps_guardrail / r.p_s - 1))
    assert r.unlock_peg_pct == pytest.approx(100 * (r.unlock_peg_price / 240 - 1))


def test_console_shows_the_valuation_pass_price_and_pct_from_price():
    from etf_screen.cli import _unlocks
    r = evaluate(_priced(_wonderful_but_pricey(), 240.0))
    text = _unlocks(r)
    assert text.startswith("valuation-pass price:")
    assert f"PEG ${240 * 2.0 / 4.5:,.2f} ({100 * (2.0 / 4.5 - 1):+.0f}%)" in text


def test_export_carries_pct_columns_and_the_label_in_the_header(tmp_path):
    from etf_screen.export import COLUMNS, Provenance, build_rows, export
    r = evaluate(_priced(_wonderful_but_pricey(), 240.0))
    row = build_rows([r], [], {})[0]
    i = COLUMNS.index("unlock_peg_price")
    assert COLUMNS[i - 1:i + 2] == ["unlock_ps_pct", "unlock_peg_price", "unlock_peg_pct"]
    assert row["unlock_peg_pct"] == f"{round(100 * (2.0 / 4.5 - 1), 2)}"
    path = tmp_path / "x.csv"
    export(path, "csv", Provenance("mock", "qqq", "static", "today", "today"), [row])
    assert "not a price target (D10)" in path.read_text().splitlines()[6]


def test_traded_price_never_moves_the_verdict():
    base = _wonderful_but_pricey()
    ok = evaluate(make("OK", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20,
                       net_income=200))
    for price in (None, 30.0, 240.0):
        r = evaluate(_priced(base, price))
        assert (r.status, r.track, r.passed, r.track_a, r.track_b) == (
            ScreenStatus.WATCHLIST, "—", False, False, False)
        assert rank([r, ok]) == [ok]


def test_zero_growth_watchlist_has_na_unlock():
    # Flat revenue, fat margins: rule40 = 44 via margin alone; guardrail is 0 so
    # P/S can never pass at any price -> unlock N/A, quality must re-qualify.
    c = make("FLAT", 1000, 1000, 500, 30, 30, 1000, 1000, 8000, 20, 15,
             net_income=300)
    r = evaluate(c)
    assert r.status is ScreenStatus.WATCHLIST
    assert r.unlock_ps_price is None and r.unlock_peg_price is None


# --- reporting boundaries -------------------------------------------------------
def test_watchlist_never_in_rank():
    wl = evaluate(_wonderful_but_pricey())
    ok = evaluate(make("OK", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20,
                       net_income=200))
    survivors = rank([wl, ok])
    assert [r.ticker for r in survivors] == ["OK"]


def test_watchlist_carries_gate_evidence():
    # A successful candidacy attaches the (passing) consistency + annotations,
    # so the watchlist row is as auditable as a survivor row.
    c = _wonderful_but_pricey()
    c.history = GOOD_EQ_HISTORY
    r = evaluate(c, consistency_years=3, consistency_mode="trend")
    assert r.consistency is not None
    assert r.roic_history is not None and r.margin_profile is not None
    assert r.earnings_quality_applicable and r.pass_earnings_quality
