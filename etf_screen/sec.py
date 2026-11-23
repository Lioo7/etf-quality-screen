"""SEC EDGAR companyfacts: filed 10-Q / 10-K values for a missing quarter (#74, D25).

The second network module, next to ``providers.py``. ``YFinanceProvider`` calls it
only when D23 finds a gap in Yahoo's TTM window, to write the quarter's filed value
into that cell instead of falling back to a stale annual figure.

* **Filed values only.** us-gaap facts from 10-Q / 10-K (and amendments) filed on or
  before the run date; for a period reported more than once, the latest filing wins
  (restatements). Nothing comes from estimates.
* **A quarter is a direct 3-month fact, or the difference of two filed YTD facts.**
  10-Q cash flow is year-to-date only, so Q2 = 6M - 3M, Q3 = 9M - 6M; a fiscal Q4 is
  the 10-K year minus the 9-month YTD. Diluted shares are a weighted average and are
  never derived: a direct 3-month fact or nothing.
* **Concept check.** A concept fills a line only when its quarters agree with every
  quarter Yahoo does report within 1%, so a tag with another scope (e.g. ``Revenues``
  including items Yahoo excludes) can't slip in.
* ``SEC_USER_AGENT`` (SEC fair-access policy) is read from the environment and never
  printed, logged or put in an error message. Unset -> no client, no fill.
* Every request has an explicit timeout and the module keeps <= 10 requests/s. Any
  failure is a :class:`SecError` with a short cause; the provider turns it into a
  note and keeps D23's annual fallback.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime

import requests

from .cache import DiskCache

USER_AGENT_ENV = "SEC_USER_AGENT"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
REQUEST_TIMEOUT = 10.0  # seconds per request, whole download (2 requests < the 60 s fetch deadline)
MIN_INTERVAL = 0.12  # seconds between requests: <= 10 req/s (SEC fair access)
FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A"})
#: In the companyfacts cache key: bump when LINES, FORMS or the kept fields change.
CACHE_SCHEMA = "v1"

#: A 3-month duration (end - start): 12- to 16-week quarters, like the D23 window step.
QUARTER_DAYS = (77, 119)
#: Period ends match within this many days (52/53-week years; same as D23's ±10).
MATCH_DAYS = 10
#: SEC and Yahoo must agree within this share on every quarter Yahoo reports.
CONCEPT_TOLERANCE = 0.01

#: Line -> (us-gaap concepts in fallback order, unit, sign vs Yahoo, derivable).
#: Capex is a positive payment in SEC and a negative cash flow in Yahoo.
LINES: dict[str, tuple[tuple[str, ...], str, int, bool]] = {
    "revenue": (("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"),
                "USD", 1, True),
    "net income": (("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                    "ProfitLoss"), "USD", 1, True),
    "operating income (EBIT)": (("OperatingIncomeLoss",), "USD", 1, True),
    "operating cash flow": (("NetCashProvidedByUsedInOperatingActivities",
                             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
                            "USD", 1, True),
    "capex": (("PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"), "USD", -1, True),
    "SBC": (("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
            "USD", 1, True),
    "diluted shares": (("WeightedAverageNumberOfDilutedSharesOutstanding",), "shares", 1,
                       False),
}
#: Lines whose quarter is never <= 0 (SEC sign: capex is a positive payment).
POSITIVE_LINES = frozenset({"revenue", "capex", "SBC", "diluted shares"})
_CONCEPTS = frozenset(c for concepts, *_ in LINES.values() for c in concepts)


class SecError(Exception):
    """An SEC request or response failed; the message is a short, secret-free cause."""


_throttle_lock = threading.Lock()
_last_request = 0.0


def _throttle() -> None:
    """Space requests at least ``MIN_INTERVAL`` apart across threads."""
    global _last_request
    with _throttle_lock:
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


class SecClient:
    """Fetches companyfacts per ticker, cached per day (trimmed to the mapped concepts)."""

    def __init__(self, user_agent: str, cache: DiskCache | None = None,
                 timeout: float = REQUEST_TIMEOUT, session=None):
        self._user_agent = user_agent
        self._cache = cache
        self.timeout = timeout
        self._session = session  # requests-like ``get``; None -> the requests module
        self._ciks: dict[str, int] | None = None
        self._down: str | None = None  # set by a network failure: no more requests

    def __repr__(self) -> str:  # never show the user agent
        return f"SecClient(timeout={self.timeout:g})"

    def _get(self, url: str):
        """GET ``url`` as JSON. The timeout bounds the whole download, not each read.

        After a network failure (timeout, connection error) the client is marked down
        for the rest of the run, so a stalled SEC costs one timeout, not one per name.
        """
        if self._down:
            raise SecError(f"skipped: {self._down} earlier in this run")
        _throttle()
        http = self._session or requests
        t0 = time.monotonic()
        try:
            resp = http.get(url, headers={"User-Agent": self._user_agent,
                                          "Accept-Encoding": "gzip, deflate"},
                            timeout=self.timeout, stream=True)
            if resp.status_code != 200:
                raise SecError(f"HTTP {resp.status_code}")
            chunks = []
            for chunk in resp.iter_content(65536):
                if time.monotonic() - t0 > self.timeout:
                    raise requests.Timeout()
                chunks.append(chunk)
        except requests.Timeout:
            self._down = f"timeout after {self.timeout:g} s"
            raise SecError(self._down) from None
        except requests.RequestException as exc:
            self._down = f"request failed ({type(exc).__name__})"
            raise SecError(self._down) from None
        try:
            return json.loads(b"".join(chunks))
        except ValueError:
            raise SecError("bad JSON") from None

    def _cache_get(self, key: str):
        return self._cache.get(key) if self._cache else None

    def _cache_set(self, key: str, value) -> None:
        """Best-effort: an unwritable cache never fails the fill."""
        if self._cache:
            try:
                self._cache.set(key, value)
            except OSError:
                pass

    def cik(self, ticker: str) -> int:
        """The SEC CIK for a ticker, from the daily ``company_tickers.json``.

        A failed tickers request marks the client down: every later name would fail too.
        """
        if self._ciks is None:
            ciks = self._cache_get("company_tickers")
            if ciks is None:
                try:
                    raw = self._get(TICKERS_URL)
                    ciks = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in raw.values()}
                except SecError as exc:
                    self._down = self._down or f"tickers list: {exc}"
                    raise
                except (AttributeError, KeyError, TypeError, ValueError):
                    self._down = "bad company_tickers JSON"
                    raise SecError(self._down) from None
                self._cache_set("company_tickers", ciks)
            self._ciks = ciks
        cik = self._ciks.get(ticker.upper())
        if cik is None:
            raise SecError(f"unknown CIK for {ticker.upper()}")
        return cik

    def companyfacts(self, ticker: str) -> dict:
        """The ticker's companyfacts, trimmed to the mapped concepts and filing forms."""
        cik = self.cik(ticker)
        key = f"companyfacts_{CACHE_SCHEMA}_{cik:010d}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        facts = _trim(self._get(FACTS_URL.format(cik=cik)))
        self._cache_set(key, facts)
        return facts


def client_from_env(cache: DiskCache | None = None) -> SecClient | None:
    """A client when ``SEC_USER_AGENT`` is set, else None (the fill is then off)."""
    ua = os.environ.get(USER_AGENT_ENV, "").strip()
    return SecClient(ua, cache=cache) if ua else None


def _trim(raw) -> dict:
    """Keep only the mapped us-gaap concepts and 10-Q / 10-K facts (smaller cache).

    ``accn`` (the filing's accession number) stays so a filled value is traceable.
    """
    if not isinstance(raw, dict):
        raise SecError("bad JSON")
    gaap = (raw.get("facts") or {}).get("us-gaap") or {}
    out = {}
    for concept in _CONCEPTS & set(gaap):
        units = {}
        for unit, rows in ((gaap[concept] or {}).get("units") or {}).items():
            kept = [{k: f.get(k) for k in ("start", "end", "val", "form", "filed", "accn")}
                    for f in rows if isinstance(f, dict) and f.get("form") in FORMS
                    and f.get("start")]  # duration facts only: instants have no start
            if kept:
                units[unit] = kept
        if units:
            out[concept] = {"units": units}
    return {"facts": {"us-gaap": out}}


# --- pure fact selection -------------------------------------------------------------
def _periods(facts: dict, concept: str, unit: str, today: date) -> dict:
    """``{(start, end): [(filed, val, form), ...]}`` filed by ``today``, newest filing first.

    Every version is kept: a direct or YTD value uses the latest filing (restatements),
    while the subtracted YTD uses the latest version filed by the minuend's filing, so
    both sides of a difference are on the basis of one point in time.
    Raises KeyError / TypeError / ValueError on a malformed fact (the caller reports it).
    """
    rows = ((facts.get("facts") or {}).get("us-gaap") or {}).get(concept, {})
    out: dict = {}
    for f in (rows.get("units") or {}).get(unit, []):
        if f["form"] not in FORMS or not f.get("start") or f["filed"] > today.isoformat():
            continue
        key = (date.fromisoformat(f["start"]), date.fromisoformat(f["end"]))
        out.setdefault(key, []).append((f["filed"], float(f["val"]), f["form"]))
    for versions in out.values():
        versions.sort(reverse=True)
    return out


def _as_date(d) -> date:
    """A plain date (frame columns are pandas Timestamps, a datetime subclass)."""
    return d.date() if isinstance(d, datetime) else d


def _months(days: int) -> int:
    return round(days / 30.44)


def quarter_value(periods: dict, end: date, derivable: bool) -> tuple[float, str] | None:
    """``(value, method)`` for the fiscal quarter ending near ``end``, or None.

    A direct 3-month fact wins (the latest-filed one). Otherwise, when ``derivable``,
    a YTD fact B ending at ``end`` minus the YTD fact A with the same start ending one
    quarter earlier, A as filed on or before B's filing (a later recast of A alone
    would mix bases). Both are filed values. The shortest such B wins.
    """
    lo, hi = QUARTER_DAYS
    near = [(s, e, vs[0]) for (s, e), vs in periods.items() if abs((e - end).days) <= MATCH_DAYS]
    direct = [ver for s, e, ver in near if lo <= (e - s).days <= hi]
    if direct:
        _, v, form = max(direct)
        return v, f"SEC {form}"
    if not derivable:
        return None
    for s, e, (filed, v, form) in sorted((n for n in near if (n[1] - n[0]).days > hi),
                                         key=lambda n: (n[1] - n[0]).days):
        prior = []
        for (ps, pe), versions in periods.items():
            if (abs((ps - s).days) <= MATCH_DAYS and lo <= (e - pe).days <= hi
                    and (pe - ps).days >= lo):
                ver = next((x for x in versions if x[0] <= filed), None)
                if ver is not None:
                    prior.append((pe, ps, ver[1]))
        if not prior:
            continue
        pe, ps, pv = max(prior)
        mb, ma = _months((e - s).days), _months((pe - ps).days)
        if form.startswith("10-K") and mb == 12 and ma == 9:
            return v - pv, f"SEC {form} FY - 9M YTD"
        return v - pv, f"SEC {form} YTD difference ({mb}M - {ma}M)"
    return None


def fill_value(facts: dict, line: str, end: date, reported: dict, today: date
               ) -> tuple[float | None, str]:
    """``(Yahoo-signed value, method)`` for ``line`` at ``end``, or ``(None, reason)``.

    ``reported`` maps the dates Yahoo does report for this line to its values: the
    concept check. Concepts are tried in order; the first that agrees with Yahoo on
    every comparable quarter (at least one) and has the target quarter fills it. A
    non-positive value for a line that can't be (revenue, capex, SBC, shares) is
    rejected: a derived quarter that comes out <= 0 means the two YTDs don't fit.
    """
    concepts, unit, sign, derivable = LINES[line]
    end = _as_date(end)
    reported = {_as_date(d): v for d, v in reported.items()}
    rejected = []
    for concept in concepts:
        periods = _periods(facts, concept, unit, today)
        if not periods:
            continue
        compared = 0
        mismatch = None
        for d, yv in sorted(reported.items(), reverse=True):
            got = quarter_value(periods, d, derivable)
            if got is None:
                continue
            compared += 1
            sv = sign * got[0]
            if abs(sv - yv) > CONCEPT_TOLERANCE * abs(yv) or (yv == 0 and sv != 0):
                pct = 100 * (sv / yv - 1) if yv else float("inf")
                mismatch = f"concept check: {concept} {pct:+.1f}% vs source at {d:%Y-%m-%d}"
                break
        if mismatch:
            rejected.append(mismatch)
            continue
        if not compared:
            continue  # nothing to cross-check this concept against
        got = quarter_value(periods, end, derivable)
        if got is None:
            rejected.append(f"{concept} has no filed value for the quarter")
        elif line in POSITIVE_LINES and got[0] <= 0:
            rejected.append(f"{concept} quarter is {got[0]:,.0f} ({got[1]}), not positive")
        else:
            return sign * got[0], got[1]
    return None, "; ".join(rejected)
