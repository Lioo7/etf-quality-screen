"""Tests for the generic issuer-holdings-CSV loader (constituents.holdings_from_csv).

Covers the messy realities of real issuer exports: a trailing disclaimer row,
blank-ticker / derivative rows, duplicate listings, dotted symbols, and (for
iShares-style files) preamble lines above the header.
"""

import pytest

from etf_screen.constituents import holdings_from_csv

# An ARK-style export: header on line 1, a footer disclaimer, a blank-ticker
# row, a duplicate, and a dotted symbol — mirroring the real ARKK file.
_ARK_CSV = (
    "date,fund,company,ticker,cusip,shares,market value ($),weight (%)\n"
    '06/18/2026,ARKK,TESLA INC,TSLA,88160R101,"1,633,138","$647,343,240.44",9.50%\n'
    '06/18/2026,ARKK,BERKSHIRE B,BRK.B,084670702,"1","$1.00",0.01%\n'
    '06/18/2026,ARKK,TESLA AGAIN,TSLA,88160R101,"1","$1.00",0.01%\n'
    '06/18/2026,ARKK,BRERA HOLDINGS WTS,,BREADUMMY,"431,626","$1,445,946.10",0.02%\n'
    '"Investors should carefully consider the objectives and risks before investing."\n'
)

# An iShares-style export: several preamble lines above the real header, and no
# per-row date or fund column.
_ISHARES_CSV = (
    "iShares Core S&P 500 ETF,,,,\n"
    'Fund Holdings as of,"Jun 18, 2026",,,\n'
    ",,,,\n"
    "Ticker,Name,Sector,Asset Class,Weight (%)\n"
    "AAPL,APPLE INC,Information Technology,Equity,7.50\n"
    "MSFT,MICROSOFT CORP,Information Technology,Equity,6.80\n"
)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_ark_style_csv_parsed_and_cleaned(tmp_path):
    h = holdings_from_csv(_write(tmp_path, "ARKK_HOLDINGS.csv", _ARK_CSV))
    # footer + blank-ticker row dropped, duplicate collapsed, dot -> dash.
    assert h.tickers == ["TSLA", "BRK-B"]
    assert h.names["TSLA"] == "TESLA INC"
    assert h.as_of == "2026-06-18"          # MM/DD/YYYY -> ISO
    assert h.etf == "ARKK"                  # from the fund column
    assert "ARKK_HOLDINGS.csv" in h.source
    assert h.is_stale is False


def test_ishares_style_csv_with_preamble(tmp_path):
    h = holdings_from_csv(_write(tmp_path, "iShares_holdings.csv", _ISHARES_CSV))
    assert h.tickers == ["AAPL", "MSFT"]        # header found below preamble
    assert h.names["MSFT"] == "MICROSOFT CORP"
    assert h.etf == "ISHARES_HOLDINGS"          # no fund column -> filename stem


def test_missing_ticker_column_raises(tmp_path):
    bad = "company,weight\nApple,7.5\nMicrosoft,6.8\n"
    with pytest.raises(ValueError, match="no ticker column"):
        holdings_from_csv(_write(tmp_path, "bad.csv", bad))


def test_header_present_but_no_valid_tickers_raises(tmp_path):
    empty = "ticker,name\n,Nothing\n,AlsoNothing\n"
    with pytest.raises(ValueError, match="no valid tickers"):
        holdings_from_csv(_write(tmp_path, "empty.csv", empty))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        holdings_from_csv(tmp_path / "does_not_exist.csv")
