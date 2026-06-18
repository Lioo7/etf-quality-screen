"""Resolve the current holdings of an ETF to a list of tickers — key-less.

Strategy, keyed by ETF ticker (QQQ, SPY, ...):

1. **Wikipedia index table** (primary) — free, no API key, reliably parseable.
2. **Bundled static list** (fallback) — a dated snapshot; emits a loud
   "stale / as-of" warning so a stale universe is never mistaken for live data.

The issuer's official holdings file (Invesco for QQQ, SSGA for SPY) is the most
authoritative source but its download endpoints are brittle/undocumented, so it
is intentionally *not* a hard dependency here. The Wikipedia path covers the
common case; the static list guarantees the tool still runs fully offline.

Every resolution records and surfaces its ``source`` and ``as_of`` date — the
index rebalances, so provenance matters.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from datetime import date

USER_AGENT = "qqq-quality-screen/0.1 (https://github.com; research tool)"


@dataclass
class Holdings:
    """A resolved constituent list with provenance."""

    etf: str
    tickers: list[str]
    source: str       # human-readable origin (URL or "bundled static list")
    as_of: str        # ISO date the data is current as of
    is_stale: bool     # True when served from the bundled fallback
    names: dict[str, str] = field(default_factory=dict)  # ticker -> company name


@dataclass
class _WikiSource:
    url: str
    ticker_columns: tuple[str, ...]  # candidate column names holding the symbol
    name_columns: tuple[str, ...] = ()  # candidate column names holding the name


# Registry of ETF -> Wikipedia index table.
_WIKI: dict[str, _WikiSource] = {
    "QQQ": _WikiSource(
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        ("Ticker", "Symbol"),
        ("Company", "Security"),
    ),
    "SPY": _WikiSource(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ("Symbol", "Ticker"),
        ("Security", "Company"),
    ),
}

# Dated static fallback (as_of below). Trimmed to a representative slice of the
# Nasdaq-100 by weight; refresh periodically. Used only when Wikipedia fails.
_STATIC_AS_OF = "2026-06-01"
_STATIC: dict[str, list[str]] = {
    "QQQ": [
        "AAPL", "MSFT", "NVDA", "AMZN", "AVGO", "META", "GOOGL", "GOOG",
        "TSLA", "COST", "NFLX", "PLTR", "AMD", "CSCO", "TMUS", "INTC",
        "INTU", "PEP", "ADBE", "TXN", "QCOM", "AMGN", "ISRG", "BKNG",
        "HON", "AMAT", "CRWD", "PANW", "ADP", "GILD", "VRTX", "ADI",
        "MU", "SBUX", "LRCX", "MELI", "KLAC", "REGN", "CEG", "PYPL",
        "SNPS", "CDNS", "MAR", "ASML", "ORLY", "CSX", "MRVL", "FTNT",
        "ABNB", "WDAY", "CTAS", "ADSK", "NXPI", "PCAR", "ROP", "MNST",
        "AEP", "PAYX", "CPRT", "FANG", "KDP", "ROST", "ODFL", "CHTR",
        "DDOG", "BKR", "EA", "VRSK", "KHC", "EXC", "GEHC", "CCEP",
        "LULU", "FAST", "CTSH", "XEL", "TTWO", "IDXX", "ON", "ZS",
        "DXCM", "ANSS", "WBD", "MCHP", "GFS", "TEAM", "CDW", "BIIB",
        "ARM", "MRNA", "DASH", "SMCI", "TTD", "MDB", "CSGP", "ILMN",
        "WBA", "MDLZ", "LIN", "PDD",
    ],
}


def resolve(etf: str) -> Holdings:
    """Resolve ``etf`` holdings, preferring Wikipedia and falling back to static."""
    etf = etf.upper()
    wiki = _WIKI.get(etf)
    if wiki is not None:
        try:
            tickers, names = _from_wikipedia(wiki)
            if tickers:
                return Holdings(
                    etf, tickers, wiki.url, date.today().isoformat(), False, names)
        except Exception as exc:  # network/parse failure -> fall back
            print(
                f"WARNING: could not resolve {etf} from Wikipedia ({exc}); "
                f"falling back to bundled static list.",
                file=sys.stderr,
            )
    return _from_static(etf)


def _from_wikipedia(wiki: _WikiSource) -> tuple[list[str], dict[str, str]]:
    """Fetch and parse constituent symbols (and names) from a Wikipedia table."""
    import pandas as pd
    import requests

    resp = requests.get(wiki.url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    for table in tables:
        tcol = next((c for c in wiki.ticker_columns if c in table.columns), None)
        if tcol is None:
            continue
        ncol = next((c for c in wiki.name_columns if c in table.columns), None)
        tickers: list[str] = []
        names: dict[str, str] = {}
        for _, row in table.iterrows():
            sym = str(row[tcol]).strip().upper()
            if not _looks_like_ticker(sym):
                continue
            norm = _normalize(sym)
            tickers.append(norm)
            if ncol is not None:
                names[norm] = str(row[ncol]).strip()
        if len(tickers) >= 50:  # guard against grabbing the wrong table
            return tickers, names
    return [], {}


def _from_static(etf: str) -> Holdings:
    tickers = _STATIC.get(etf)
    if not tickers:
        raise ValueError(
            f"No static fallback for ETF '{etf}'. Supported: {sorted(_STATIC)}. "
            f"Use --tickers to screen an explicit list instead."
        )
    print(
        f"WARNING: using STALE bundled {etf} constituents (as-of {_STATIC_AS_OF}). "
        f"The index rebalances — verify before relying on these results.",
        file=sys.stderr,
    )
    return Holdings(etf, list(tickers), "bundled static list", _STATIC_AS_OF, True)


def _looks_like_ticker(s: str) -> bool:
    return bool(s) and 1 <= len(s) <= 6 and s.replace(".", "").isalpha()


def _normalize(symbol: str) -> str:
    """Normalize Wikipedia symbols to the form Yahoo/FMP expect (e.g. BRK.B -> BRK-B)."""
    return symbol.replace(".", "-")
