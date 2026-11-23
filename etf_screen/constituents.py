"""Resolve the current holdings of an ETF to a list of tickers — key-less.

A named ``--universe`` never silently uses a stale list (#8, D26):

* **QQQ, ARKK** read ``holdings/<etf>-holdings.csv``. Its age is the file's own
  as-of date, else its modification time, never today. A missing file, or one
  older than ``max_age_days``, is refreshed: ARKK from ARK's public holdings
  CSV, QQQ from the Nasdaq-100 index list (Nasdaq API; Invesco's download
  endpoint refuses scripts, and QQQ fully replicates the index). The download
  is validated, written atomically, and the previous file is kept as ``.bak``.
  Any failure raises with manual download steps.
* **SPY** is a live Wikipedia fetch with a constituent-count check; a failure
  raises.
* The **bundled static list** is used only with ``allow_stale=True`` and is
  marked stale everywhere it is shown.

Every resolution records its ``source`` and ``as_of`` date — the index
rebalances, so provenance matters.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable

USER_AGENT = "etf-quality-screen/0.1 (https://github.com; research tool)"
# ARK's CDN and the Nasdaq API reject non-browser user agents.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

HOLDINGS_DIR = Path("holdings")
DEFAULT_MAX_AGE_DAYS = 14
ARKK_URL = ("https://assets.ark-funds.com/fund-documents/funds-etf-csv/"
            "ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv")
NASDAQ100_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
_SOURCE_PREFIX = "# source: "


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
    count: tuple[int, int] = (50, 10_000)  # accepted constituent count (inclusive)


# Registry of ETF -> Wikipedia index table. QQQ left: the Nasdaq-100 page lost
# its constituents table (2026-11), so QQQ is a local-file universe below.
_WIKI: dict[str, _WikiSource] = {
    "SPY": _WikiSource(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ("Symbol", "Ticker"),
        ("Security", "Company"),
        (495, 510),
    ),
}


@dataclass
class _FileSource:
    """A universe read from ``holdings/<etf>-holdings.csv`` and refreshed from ``url``."""

    url: str
    count: tuple[int, int]  # accepted constituent count (inclusive)
    manual: str  # where a person downloads the file by hand
    to_csv: Callable[[str], tuple[str, str]]  # payload -> (csv text, source label)


def _ark_to_csv(payload: str) -> tuple[str, str]:
    return payload, "ARK ARKK holdings CSV (ark-funds.com)"


def _nasdaq_to_csv(payload: str) -> tuple[str, str]:
    """Nasdaq API JSON -> a ``Date,Fund,Name,Ticker`` CSV; any schema drift raises."""
    try:
        data = json.loads(payload)["data"]
        as_of = datetime.strptime(data["date"].strip(), "%b %d, %Y").date()
        rows = data["data"]["rows"]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date", "Fund", "Name", "Ticker"])
        for row in rows:
            w.writerow([as_of.strftime("%m/%d/%Y"), "QQQ",
                        str(row.get("companyName") or "").strip(),
                        _required_symbol(row)])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"unexpected Nasdaq API response ({type(exc).__name__}: {exc})")
    label = f"Nasdaq-100 index constituents (Nasdaq API, as-of {as_of.isoformat()})"
    return out.getvalue(), label


def _required_symbol(row: dict) -> str:
    sym = row["symbol"]
    if not isinstance(sym, str) or not sym.strip():
        raise ValueError(f"row without a symbol: {row!r}")
    return sym.strip()


_FILES: dict[str, _FileSource] = {
    "QQQ": _FileSource(
        NASDAQ100_URL, (90, 110),
        "the Nasdaq-100 constituents (Nasdaq API or nasdaq.com), or the Invesco QQQ "
        "holdings CSV from invesco.com",
        _nasdaq_to_csv,
    ),
    "ARKK": _FileSource(
        ARKK_URL, (20, 70),
        f"the ARKK holdings CSV from ark-funds.com ({ARKK_URL})",
        _ark_to_csv,
    ),
}

# Dated static fallback (as_of below). Used only with allow_stale=True.
# Source: live Wikipedia Nasdaq-100 table fetched 2026-07-09 (103 tickers;
# SPCX added to the index effective 2026-07-07). Prefer --holdings with the
# official Invesco QQQ CSV when refreshing the example run.
_STATIC_AS_OF = "2026-07-09"
_STATIC: dict[str, list[str]] = {
    "QQQ": [
        "AAPL", "ABNB", "ADBE", "ADI", "ADP", "ADSK", "AEP", "ALAB",
        "ALNY", "AMAT", "AMD", "AMGN", "AMZN", "APP", "ARM", "ASML",
        "AVGO", "AXON", "BKNG", "BKR", "CCEP", "CDNS", "CEG", "CMCSA",
        "COST", "CPRT", "CRWD", "CRWV", "CSCO", "CSX", "CTAS", "DASH",
        "DDOG", "DXCM", "EA", "EXC", "FANG", "FAST", "FER", "FTNT",
        "GEHC", "GILD", "GOOG", "GOOGL", "HON", "HONA", "IDXX", "INTC",
        "INTU", "ISRG", "KDP", "KHC", "KLAC", "LIN", "LITE", "LRCX",
        "MAR", "MCHP", "MDLZ", "MELI", "META", "MNST", "MPWR", "MRVL",
        "MSFT", "MSTR", "MU", "NBIS", "NFLX", "NVDA", "NXPI", "ODFL",
        "ORLY", "PANW", "PAYX", "PCAR", "PDD", "PEP", "PLTR", "PYPL",
        "QCOM", "REGN", "RKLB", "ROP", "ROST", "SBUX", "SHOP", "SNDK",
        "SNPS", "SPCX", "STX", "TER", "TMUS", "TRI", "TSLA", "TTWO",
        "TXN", "VRTX", "WBD", "WDAY", "WDC", "WMT", "XEL",
    ],
}


def resolve(
    etf: str,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    allow_stale: bool = False,
    holdings_dir: Path | None = None,
    fetch: Callable[[str], str] | None = None,
    today: date | None = None,
) -> Holdings:
    """Resolve ``etf`` holdings; never a silently stale list (see module docstring).

    Raises ``ValueError`` (with manual steps) when no fresh list can be had and
    ``allow_stale`` is off.
    """
    etf = etf.upper()
    today = today or date.today()
    if etf in _FILES:
        directory = holdings_dir if holdings_dir is not None else HOLDINGS_DIR
        try:
            return _from_file(etf, _FILES[etf], Path(directory), max_age_days, today,
                              fetch or _http_get)
        except _StaleFile as stale:
            if not allow_stale:
                raise ValueError(str(stale)) from None
            print(f"WARNING: {stale}", file=sys.stderr)
            if stale.holdings is not None:
                return _mark_stale(stale.holdings)
            return _from_static(etf)
    wiki = _WIKI.get(etf)
    if wiki is not None:
        try:
            tickers, names = _from_wikipedia(wiki)
            lo, hi = wiki.count
            if not lo <= len(tickers) <= hi:
                raise ValueError(f"{len(tickers)} tickers, expected {lo}-{hi}")
            return Holdings(
                etf, tickers,
                f"Wikipedia (live fetch; page freshness not verified): {wiki.url}",
                today.isoformat(), False, names)
        except Exception as exc:  # network/parse/count failure -> loud
            msg = f"could not resolve {etf} from Wikipedia ({exc})."
            if not allow_stale:
                raise ValueError(
                    f"{msg} Retry later, or pass --tickers / --holdings with a list "
                    f"you downloaded (e.g. the SSGA SPY daily holdings file).") from None
            print(f"WARNING: {msg}", file=sys.stderr)
    return _from_static(etf)  # allow_stale, or an ETF with no live source (raises)


class _StaleFile(Exception):
    """No fresh file could be had; carries the stale local file, if any."""

    def __init__(self, msg: str, holdings: Holdings | None):
        super().__init__(msg)
        self.holdings = holdings


def _from_file(etf: str, src: _FileSource, directory: Path, max_age_days: int,
               today: date, fetch: Callable[[str], str]) -> Holdings:
    path = directory / f"{etf.lower()}-holdings.csv"
    current: Holdings | None = None
    unreadable = ""
    if path.exists():
        try:
            current = _read_labelled(path)
        except ValueError as exc:  # corrupt / not a holdings file: refresh it
            unreadable = f" ({exc})"
        else:
            if _is_fresh(current.as_of, today, max_age_days):
                return current
    try:
        return _refresh(etf, src, path, max_age_days, today, fetch)
    except Exception as exc:
        if current is not None:
            state = f"{path} is stale (as-of {current.as_of})"
        elif unreadable:
            state = f"{path} is unreadable{unreadable}"
        else:
            state = f"{path} is missing"
        raise _StaleFile(
            f"could not refresh {etf} from {src.url} ({exc}); {state}. "
            f"Download {src.manual}, save it as {path}, and rerun "
            f"(or use --holdings PATH; --allow-stale accepts an old list).",
            current,
        ) from None


def _refresh(etf: str, src: _FileSource, path: Path, max_age_days: int, today: date,
             fetch: Callable[[str], str]) -> Holdings:
    """Download, validate, then atomically replace ``path`` (old file -> ``.bak``)."""
    text, label = src.to_csv(fetch(src.url))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(f"{_SOURCE_PREFIX}{label}\n{text}", encoding="utf-8")
        h = holdings_from_csv(tmp)
        lo, hi = src.count
        if not lo <= len(h.tickers) <= hi:
            raise ValueError(f"{len(h.tickers)} tickers, expected {lo}-{hi}")
        if "file modified date" in h.source:  # mtime of our own tmp file = now
            raise ValueError("downloaded list carries no as-of date")
        if not _is_fresh(h.as_of, today, max_age_days):
            raise ValueError(f"downloaded list is as-of {h.as_of}, not within "
                             f"{max_age_days} days")
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)  # one atomic swap; the live path always exists
    finally:
        tmp.unlink(missing_ok=True)
    print(f"{etf}: refreshed from {src.url}, as-of {h.as_of}", file=sys.stderr)
    return _read_labelled(path)


def _read_labelled(path: Path) -> Holdings:
    """``holdings_from_csv`` plus the source label a refresh wrote on line 1."""
    h = holdings_from_csv(path)
    with path.open(encoding="utf-8-sig") as fh:
        first = fh.readline().strip()
    if first.startswith(_SOURCE_PREFIX):
        label = first[len(_SOURCE_PREFIX):]
        h.source = label if "as-of" in label else f"{label}, as-of {h.as_of}"
    h.etf = path.name.split("-holdings")[0].upper()
    return h


def _mark_stale(h: Holdings) -> Holdings:
    h.is_stale = True
    return h


def _is_fresh(as_of: str, today: date, max_age_days: int) -> bool:
    """0 <= age <= max; an unparseable or future date is never fresh."""
    try:
        age = (today - date.fromisoformat(as_of)).days
    except (TypeError, ValueError):
        return False
    return 0 <= age <= max_age_days


def _http_get(url: str, user_agent: str = _BROWSER_UA) -> str:
    """GET ``url`` as text: the module's only network call."""
    import requests

    resp = requests.get(url, headers={"User-Agent": user_agent,
                                      "Accept": "application/json, text/csv, */*"},
                        timeout=30)
    resp.raise_for_status()
    return resp.text


def _from_wikipedia(wiki: _WikiSource) -> tuple[list[str], dict[str, str]]:
    """Fetch and parse constituent symbols (and names) from a Wikipedia table."""
    import pandas as pd

    tables = pd.read_html(io.StringIO(_http_get(wiki.url, USER_AGENT)))
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


# Candidate column names across common issuer holdings exports (ARK, iShares,
# SSGA/SPDR, Vanguard, Invesco). Matched case-insensitively; first hit wins.
_CSV_TICKER_COLS = ("ticker", "symbol", "ticker symbol", "holding ticker", "identifier")
_CSV_NAME_COLS = ("company", "name", "security", "issuer name", "holding",
                  "description", "security name")
_CSV_DATE_COLS = ("date", "as of", "as of date")
_CSV_FUND_COLS = ("fund", "fund name", "fund ticker")


def holdings_from_csv(path: str | Path) -> Holdings:
    """Resolve an ETF's constituents from an *already downloaded* issuer holdings CSV.

    Issuer holdings files (ARK, iShares, SSGA, Vanguard, ...) are the most
    authoritative constituent source, which is why this exists alongside the
    Wikipedia and static paths — it lets the tool screen *any* ETF, not just the
    ones with a curated index table. It is deliberately generic: no network, no
    per-issuer schema. A ticker column is located by name (tolerating preamble
    rows above the header), and anything that isn't a ticker — blank rows, footer
    disclaimers, cash/derivative lines — is discarded via the same
    :func:`_looks_like_ticker` guard the rest of the module uses. Provenance
    (``as_of``, fund name) is read from the file's own columns when present.
    """
    p = Path(path)
    if not p.exists():
        raise ValueError(f"holdings CSV not found: {p}")
    with p.open(newline="", encoding="utf-8-sig") as fh:
        rows = [row for row in csv.reader(fh) if row]

    header_idx, header = _find_header(rows)
    if header_idx is None:
        raise ValueError(
            f"{p.name}: no ticker column found (looked for any of {list(_CSV_TICKER_COLS)})"
        )
    t_i = _pick_col(header, _CSV_TICKER_COLS)
    n_i = _pick_col(header, _CSV_NAME_COLS)
    d_i = _pick_col(header, _CSV_DATE_COLS)
    f_i = _pick_col(header, _CSV_FUND_COLS)

    tickers: list[str] = []
    names: dict[str, str] = {}
    seen: set[str] = set()
    as_of: str | None = None
    fund: str | None = None
    for row in rows[header_idx + 1:]:
        sym = _cell(row, t_i).upper()
        if not _looks_like_ticker(sym):
            continue
        norm = _normalize(sym)
        if norm in seen:  # an ETF can list the same name twice (e.g. share classes)
            continue
        seen.add(norm)
        tickers.append(norm)
        name = _cell(row, n_i)
        if name:
            names[norm] = name
        if as_of is None:
            as_of = _parse_csv_date(_cell(row, d_i))
        if fund is None:
            fund = _cell(row, f_i) or None

    if not tickers:
        raise ValueError(f"{p.name}: header found but no valid tickers in the file")

    etf = (fund or p.stem).upper()
    source = f"holdings CSV: {p.name}"
    if as_of is None:  # no date column: the file's own age, never today (#8)
        as_of = date.fromtimestamp(p.stat().st_mtime).isoformat()
        source += " (as-of = file modified date)"
    return Holdings(
        etf=etf, tickers=tickers, source=source,
        as_of=as_of, is_stale=False, names=names,
    )


def tickers_from_file(path: str | Path) -> list[str]:
    """Read a curated ticker-list file: one ticker per line, ``#`` comments allowed.

    The plain-text universe format for thesis-driven lists (see ``universes/``):
    screening whole foreign exchanges multiplies data-quality problems for
    negligible yield, so coverage is extended through deliberate, curated ADR
    lists instead. Deliberately minimal — no network, no schema:

    * blank lines and ``#`` comments (full-line or trailing) are ignored;
    * tickers are uppercased and de-duplicated (a note is printed on duplicates);
    * anything that doesn't look like a ticker raises (a typo in a hand-curated
      file should fail loudly, not silently screen the wrong universe);
    * an empty resulting list is an error, never a silent empty run.
    """
    p = Path(path)
    if not p.exists():
        raise ValueError(f"tickers file not found: {p}")
    tickers: list[str] = []
    seen: set[str] = set()
    dupes: list[str] = []
    for lineno, raw in enumerate(p.read_text().splitlines(), start=1):
        sym = raw.split("#", 1)[0].strip().upper()
        if not sym:
            continue
        if not _looks_like_ticker(sym.replace("-", "")):
            raise ValueError(f"{p.name}:{lineno}: '{sym}' does not look like a ticker")
        norm = _normalize(sym)
        if norm in seen:
            dupes.append(norm)
            continue
        seen.add(norm)
        tickers.append(norm)
    if dupes:
        print(f"NOTE: {p.name}: dropped duplicate ticker(s): {', '.join(dupes)}",
              file=sys.stderr)
    if not tickers:
        raise ValueError(f"{p.name}: no tickers found (file is empty or all comments)")
    return tickers


def _find_header(rows: list[list[str]]) -> tuple[int | None, list[str]]:
    """Locate the header row; issuer files often carry preamble lines above it."""
    for i, row in enumerate(rows):
        if _pick_col(row, _CSV_TICKER_COLS) is not None:
            return i, row
    return None, []


def _pick_col(header: list[str], candidates: tuple[str, ...]) -> int | None:
    """Index of the first header cell matching a candidate name (case-insensitive)."""
    lowered = [c.strip().lower() for c in header]
    for cand in candidates:
        if cand in lowered:
            return lowered.index(cand)
    return None


def _cell(row: list[str], idx: int | None) -> str:
    """Safe, trimmed cell access; '' when the column is absent or the row is short."""
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _parse_csv_date(raw: str) -> str | None:
    """Best-effort ISO date from a holdings-file date cell, else the raw string."""
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _looks_like_ticker(s: str) -> bool:
    return bool(s) and 1 <= len(s) <= 6 and s.replace(".", "").isalpha()


def _normalize(symbol: str) -> str:
    """Normalize Wikipedia symbols to the form Yahoo/FMP expect (e.g. BRK.B -> BRK-B)."""
    return symbol.replace(".", "-")
