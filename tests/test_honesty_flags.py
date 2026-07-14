"""Informational honesty flags — never gates, never changes rank.

1. ``otc_adr_data_suspect`` — 5-letter OTC ADR convention (ends in Y or F);
   Yahoo routinely publishes broken caps/statements for these lines.
2. ``financial_sector_note`` — Financial Services names are balance-sheet
   businesses; leverage/FCF/GM metrics are structurally unreliable.
"""

from etf_screen.export import COLUMNS, build_rows
from etf_screen.screen import (
    FINANCIAL_SECTOR_NOTE,
    FINANCIAL_SERVICES_SECTOR,
    ScreenStatus,
    evaluate,
    is_otc_adr_ticker,
    rank,
)
from test_screen import ACCEPTANCE, make


def test_otc_adr_ticker_convention():
    assert is_otc_adr_ticker("KYCCF")
    assert is_otc_adr_ticker("NTDOY")
    assert is_otc_adr_ticker("kyccf")  # case-insensitive
    assert not is_otc_adr_ticker("MSFT")       # 4 letters
    assert not is_otc_adr_ticker("OPAQUE")     # 6 letters
    assert not is_otc_adr_ticker("BRK-B")      # hyphenated, not OTC ADR pattern
    assert not is_otc_adr_ticker("AAPL")
    assert not is_otc_adr_ticker("SONY")       # 4-letter listed ADR, not the OTC pattern


def test_otc_adr_data_suspect_on_result():
    r = evaluate(make("KYCCF", 1000, 700, 400, 50, 50, 1000, 1000, 5000, None, 0.0,
                      net_income=200))
    assert r.otc_adr_data_suspect
    assert r.status is ScreenStatus.NEEDS_DATA  # still the PEG-untestable path
    # Flag is informational — does not change the verdict shape.
    assert not r.passed and r.track == "—"


def test_otc_adr_flag_absent_on_ordinary_ticker():
    r = evaluate(ACCEPTANCE["CleanCompounder"])
    assert not r.otc_adr_data_suspect


def test_financial_sector_note_on_cof_like():
    # COF-like: a Financial Services survivor fixture — annotation attaches,
    # verdict unchanged from the non-financial twin.
    fin = make("COF", 30000, 24000, 12000, 1500, 1500, 1000, 1010, 270000, 28, 22,
               net_income=6000)
    fin.sector = FINANCIAL_SERVICES_SECTOR
    twin = make("COFX", 30000, 24000, 12000, 1500, 1500, 1000, 1010, 270000, 28, 22,
                net_income=6000)
    twin.sector = "Technology"

    r_fin = evaluate(fin)
    r_twin = evaluate(twin)

    assert r_fin.financial_sector_note == FINANCIAL_SECTOR_NOTE
    assert r_twin.financial_sector_note is None
    # Verdicts identical — the annotation never gates.
    assert r_fin.status is r_twin.status is ScreenStatus.PASS
    assert r_fin.track == r_twin.track == "Track A"
    assert r_fin.passed and r_twin.passed


def test_financial_sector_note_never_changes_rejection():
    fin = make("AMP", 50000, 46300, 18000, 3000, 2000, 2000, 2000, 600000, 24, 9,
               net_income=9000)
    fin.sector = FINANCIAL_SERVICES_SECTOR
    twin = make("AMPX", 50000, 46300, 18000, 3000, 2000, 2000, 2000, 600000, 24, 9,
                net_income=9000)
    twin.sector = "Technology"
    r_fin, r_twin = evaluate(fin), evaluate(twin)
    assert r_fin.financial_sector_note == FINANCIAL_SECTOR_NOTE
    assert r_fin.status is r_twin.status is ScreenStatus.REJECTED
    assert not r_fin.passed and not r_twin.passed


def test_honesty_flags_never_enter_rank_logic():
    # A financial NEEDS_DATA-shaped OTC ADR still stays out of rank().
    c = make("KYCCF", 1000, 700, 400, 50, 50, 1000, 1000, 5000, None, 0.0,
             net_income=200)
    c.sector = FINANCIAL_SERVICES_SECTOR
    r = evaluate(c)
    assert r.otc_adr_data_suspect and r.financial_sector_note
    assert rank([r]) == []


def test_honesty_flags_exported():
    assert "otc_adr_data_suspect" in COLUMNS
    assert "financial_sector_note" in COLUMNS
    fin = make("COF", 30000, 24000, 12000, 1500, 1500, 1000, 1010, 270000, 28, 22,
               net_income=6000)
    fin.sector = FINANCIAL_SERVICES_SECTOR
    otc = make("KYCCF", 1000, 700, 400, 50, 50, 1000, 1000, 5000, None, 0.0,
               net_income=200)
    rows = {row["ticker"]: row for row in build_rows([evaluate(fin), evaluate(otc)], [], {})}
    assert rows["COF"]["financial_sector_note"] == FINANCIAL_SECTOR_NOTE
    assert rows["COF"]["otc_adr_data_suspect"] is False
    assert rows["KYCCF"]["otc_adr_data_suspect"] is True
    assert rows["KYCCF"]["financial_sector_note"] == ""
