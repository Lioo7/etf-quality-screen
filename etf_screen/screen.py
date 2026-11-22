"""Core screening logic — no network access lives here.

This module defines the :class:`Company` input record, the :func:`evaluate`
function that applies the validated four-filter / two-track methodology, and
helpers for ranking and rendering results.

The thresholds and routing here are a *fixed, already-validated spec* — see the
acceptance tests in ``tests/test_screen.py``. Routing keys solely on trailing
GAAP profitability (``net_income_ttm > 0``): a profitable name goes through
Track A only (it must clear the PEG filter; if PEG is not computable it is
rejected, never routed to Track B), while a GAAP-unprofitable name is eligible
for the PEG-exempt Track B (subject to the quality gate). ``forward_pe`` is
irrelevant to routing.

Verdict pipeline (``evaluate``), in order:

0. Pre-revenue firewall: ``revenue_ttm <= 0`` returns ``PRE_REVENUE`` before any metric
   math. Current revenue > 0 with a zero *prior* year stays a ``NOT_EVALUABLE``
   divide-by-zero case.
1. The four snapshot filters (Rule of 40, PEG <= 2, P/S guardrail, SBC <= 15%) and
   two-track routing: the authoritative, fixed core.
2. A snapshot qualifier then faces the quality gates: leverage (net debt / FCF <= 3),
   goodwill (<= 40% of assets), ROIC (>= 10%, Track A under ``strict`` only; advisory
   when not computable), earnings quality (cumulative 3-year AdjFCF / NI >= 0.8; N/A,
   never a fail, when cumulative NI <= 0 or no NI history), and the multi-year
   consistency gate (``track_a_consistency`` / ``track_b_consistency`` over
   ``Company.history``). A balance-sheet failure is definitive (REJECTED) and wins over
   the historical gate; otherwise INSUFFICIENT_HISTORY, then a consistency FAIL, else PASS.
3. A snapshot failer blocked only by valuation (P/S and/or a computable-but-failed PEG)
   has the same gates run on its would-be track: all clear -> ``WATCHLIST`` with
   ``_unlock_prices()`` buy triggers (requires a consistency PASS). A profitable failer
   whose only blocker is an untestable PEG (estimates absent, non-positive or in another
   currency; P/S passing) -> ``NEEDS_DATA`` with an overrides.json hint naming the cause
   and no unlock price. Consensus EPS growth <= 0 is a FAILED PEG, never NEEDS_DATA or
   WATCHLIST: no price or data fixes it (tolerates INSUFFICIENT history, still
   rejects on a consistency FAIL). Any gate fail -> plain REJECTED with valuation reasons
   only. Candidacies and the real track share the ONE ``_run_quality_gates`` closure;
   never re-assemble the gate set inline.
4. LAST, the unconditional IPO-seasoning override: a parseable ``ipo_date`` inside the
   window forces ``RECENT_IPO`` whatever the gates decided (would-be-WATCHLIST unlock
   prices are shed). An unknown date is never guessed; the name is flagged
   ``ipo_date_unknown``.

``ScreenStatus`` (PASS / REJECTED / PRE_REVENUE / INSUFFICIENT_HISTORY / NOT_EVALUABLE /
RECENT_IPO / WATCHLIST / NEEDS_DATA) is the single verdict source; ``Result.passed``,
``evaluable``, ``insufficient_history``, ``recent_ipo``, ``watchlist`` and ``needs_data``
are read-only properties derived from it. Add an outcome by extending the enum and its
properties, not a parallel boolean. WATCHLIST and NEEDS_DATA never enter ``rank()``.

Consistency modes (Track A only; Track B never changes): ``strict`` = Rule of 40 >= 40 in
every windowed year; ``trend`` = latest >= 40 + mean >= 30 + no lumpy sub-floor collapse
(a launch year is forgiven). The window is N+1 contiguous annual periods in both modes.

Informational-only layers (never gate, never rank, never change ``passed``/``track``):
sector context (``sector_medians()``, ``annotate_sector_context()``), ROIC history
(``roic_history()`` -> ``roic_durable``), gross-margin profile (``margin_profile()`` ->
``pricing_power`` / ``gm_not_applicable``), entry timing (``_entry_timing()`` ->
``pct_off_ath``, ``three_month_return``, ``HOT``), the EQ capex-intensity note
(``eq_capex_intensity``), the OTC ADR flag (``otc_adr_data_suspect``) and the Financial
Services asterisk (``financial_sector_note``). Per-year ``AnnualPeriod`` fields that are
missing skip that year for that metric only; they never truncate the consistency run,
which keys solely on revenue/OCF/capex/SBC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from math import isnan, nan
from calendar import monthrange
from datetime import date
from statistics import mean, median, pstdev

# --- thresholds (the validated spec) ---------------------------------------
RULE40_MIN = 40.0       # revenue growth% + adjusted FCF margin%
PEG_MAX = 2.0           # Track A only
SBC_MAX_PCT = 15.0      # SBC as % of revenue
PS_GUARDRAIL_FACTOR = 0.5  # P/S must be <= factor * revenue growth%

# --- trend-mode consistency (Track A only; strict mode is the all-years rule) ---
TREND_FLOOR = 25.0      # a windowed Rule-of-40 below this is "sub-floor"
TREND_AVG_MIN = 30.0    # mean windowed Rule of 40 must clear this (chronic-weakness backstop)

# --- balance-sheet gates (current snapshot; raw lines on Company) -----------
NET_DEBT_TO_FCF_MAX = 3.0      # net debt must be serviceable within 3 years of FCF
GOODWILL_TO_ASSETS_MAX = 0.40  # goodwill-bloated balance sheet ceiling
ROIC_MIN = 0.10               # pre-tax return on invested capital floor (Track A strict)

# --- ROIC history (advisory; multi-year durable-moat proxy) -----------------
ROIC_DURABLE_MIN = 0.15   # median ROIC bar for the durability annotation
ROIC_DURABLE_YEARS = 5    # minimum years of usable data to claim durability
ROIC_HISTORY_MIN_YEARS = 3  # below this, the median is flagged as noisy/thin

# --- earnings quality (rejecting; balance-sheet-gate family) ----------------
EARNINGS_QUALITY_WINDOW = 3    # cumulative window, years
EARNINGS_QUALITY_MIN = 0.8     # cumulative adjusted FCF / cumulative net income floor
# Capex/revenue over the SAME window above which a failed EQ gate carries the
# "may be reinvestment, not accruals" annotation (NVDA/TSM-style growth capex).
# Informational only: the gate's arithmetic cannot tell rot from reinvestment,
# so the threshold never changes — the annotation flags the ambiguity instead.
EQ_CAPEX_INTENSITY_NOTE_MIN = 15.0

# --- gross-margin level & stability (advisory; pricing-power proxy) --------
PRICING_POWER_GM_MIN = 40.0       # gross margin % floor
PRICING_POWER_STDDEV_MAX = 5.0    # gross margin stddev ceiling, percentage points
PRICING_POWER_MIN_YEARS = 5       # minimum usable years to claim pricing power

# --- IPO seasoning (classification; keeps unseasoned listings off the shortlist) ---
IPO_SEASONING_MONTHS = 12   # default; CLI-configurable via --ipo-seasoning-months

# --- entry timing (INFORMATIONAL ONLY — never touches verdicts or ranking) --
HOT_3MO_RETURN_MIN = 50.0    # 3-month total return above this is parabolic
HOT_ATH_PROXIMITY_PCT = 20.0  # ... and within this % of the all-time high -> HOT

# --- sector context (INFORMATIONAL ONLY — never gates a verdict) -----------
SECTOR_MIN_PEERS = 5    # don't compute a sector median below this many peers
# Result attributes whose per-sector median we report (adj_fcf_margin == adj_margin).
_SECTOR_METRICS = ("rule40", "p_s", "peg", "growth", "adj_margin", "sbc_pct")

# --- honesty flags (INFORMATIONAL ONLY — never gates a verdict) ------------
# 5-letter OTC ADR convention: unsponsored F-shares / sponsored Y-ADRs. Yahoo
# routinely publishes broken market caps and thin statements for these lines.
_OTC_ADR_SUFFIXES = frozenset({"Y", "F"})
# Balance-sheet businesses: net-debt/FCF, OCF, and GM are structurally unreliable.
FINANCIAL_SERVICES_SECTOR = "Financial Services"
FINANCIAL_SECTOR_NOTE = (
    "balance-sheet business — leverage/FCF/GM metrics unreliable; "
    "analyze via sector-appropriate measures"
)
#: Informational note (#53): consensus EPS falls while consensus revenue grows.
EPS_DECLINE_NOTE = "EPS decline despite revenue growth: check FY0 for one-offs"
NEEDS_DATA_HINT = (
    "PEG untestable: {reason} — supply forward_pe (= price / FY1 consensus EPS) "
    "and forward_eps_growth (= 100 x (FY1 / FY0 - 1)) in overrides.json to "
    "complete the screen (also a verified market_cap when the Yahoo cap looks "
    "wrong — an uncorrected OTC/ADR cap would finish the screen against a "
    "fictional valuation)"
)


def peg_untestable_reason(c: Company) -> str:
    """Why PEG could not be administered, read from the raw consensus inputs."""
    if c.eps_fy0 is None or c.eps_fy1 is None:
        return "no consensus EPS estimates"
    if c.eps_fy0 <= 0 or c.eps_fy1 <= 0:
        return (f"non-positive consensus EPS (FY0 {c.eps_fy0:.2f}, "
                f"FY1 {c.eps_fy1:.2f})")
    # Both EPS are positive: the provider recorded the specific cause (currency
    # mismatch, no price) as a "PEG untestable:" low-confidence note.
    prefix = "PEG untestable: "
    for note in c.low_confidence:
        if note.startswith(prefix):
            return note[len(prefix):]
    ccy = f" (estimates in {c.estimate_currency})" if c.estimate_currency else ""
    return f"forward P/E or growth unavailable{ccy}: no price, or price currency differs"


def needs_data_hint(c: Company) -> str:
    """The NEEDS_DATA reason: the actual cause plus the basis for override values."""
    return NEEDS_DATA_HINT.format(reason=peg_untestable_reason(c))


def is_otc_adr_ticker(ticker: str) -> bool:
    """True for the 5-letter OTC ADR convention (ticker ends in Y or F).

    Informational only — never a gate. These lines routinely carry broken
    caps/statements on Yahoo (e.g. Keyence/KYCCF).
    """
    t = (ticker or "").upper().replace("-", "").replace(".", "")
    return len(t) == 5 and t.isalpha() and t[-1] in _OTC_ADR_SUFFIXES


def pct(part: float, whole: float) -> float:
    """Return ``part/whole`` as a percentage, or NaN when ``whole`` is falsy.

    The NaN result is the divide-by-zero guard: callers treat a NaN metric as
    "could not evaluate" and skip the name rather than crashing.
    """
    return (part / whole) * 100 if whole else nan


class ScreenStatus(Enum):
    """The consolidated verdict for one screened company.

    ``passed`` / ``evaluable`` / ``insufficient_history`` on :class:`Result` are
    read-only properties derived from this single value, so there is one source
    of truth for a name's outcome.
    """

    PASS = auto()                  # cleared every gate
    REJECTED = auto()              # failed a snapshot filter, balance-sheet gate, or consistency
    PRE_REVENUE = auto()           # revenue_ttm <= 0 — metric math never ran
    INSUFFICIENT_HISTORY = auto()  # snapshot qualifier demoted for too little annual history
    NOT_EVALUABLE = auto()         # a divide-by-zero guard tripped (missing/zero denominator)
    RECENT_IPO = auto()            # listed within the seasoning window — never in the shortlist
    WATCHLIST = auto()             # every quality gate passed; ONLY valuation blocked the pass
    NEEDS_DATA = auto()            # every testable gate passed; PEG was UNTESTABLE (no estimates)


@dataclass
class AnnualPeriod:
    """One fiscal year of RAW statement lines ($millions), for the consistency gate.

    Raw lines only — never pre-computed ratios — so the per-year math runs through
    the same :func:`_period_metrics` helper as the live snapshot, with zero drift.

    ``revenue``/``ocf``/``capex``/``sbc`` gate the multi-year consistency window and
    must form a contiguous run (see ``providers._annual_history``). The fields below
    are OPTIONAL (default ``None``) and feed only the advisory ROIC-history /
    gross-margin annotations and the earnings-quality gate — a year missing one of
    these is simply skipped for that metric, never fabricated, and never truncates
    the consistency-gate history.
    """

    fiscal_label: str           # human-readable year label, e.g. "2023"
    revenue: float
    ocf: float                  # operating cash flow
    capex: float                # capital expenditure (positive magnitude)
    sbc: float                  # stock-based compensation
    operating_income: float | None = None      # EBIT that year (ROIC numerator)
    total_debt: float | None = None            # ROIC denominator (invested capital)
    cash_and_equivalents: float | None = None  # ROIC denominator (invested capital)
    total_equity: float | None = None          # ROIC denominator (invested capital)
    net_income: float | None = None            # earnings-quality gate
    cogs: float | None = None                  # gross-margin gate; None = GM N/A that yr


@dataclass
class Company:
    """Raw fundamentals for one company (all monetary fields in $millions).

    These are *raw statement lines*, never pre-packaged ratios — every metric in
    :func:`evaluate` is derived from these so the methodology stays auditable.
    """

    ticker: str
    revenue_ttm: float          # trailing-twelve-month revenue
    # The TTM revenue one year earlier. NaN when the source cannot expose a
    # summable prior-year TTM (Yahoo caps quarterly statements at ~5 columns,
    # so quarters 5-8 are unpublished) — growth then derives from the
    # quarterly YoY pair below, and is NOT_EVALUABLE if that is absent too.
    revenue_ttm_prior: float
    ocf_ttm: float              # operating cash flow, TTM
    capex_ttm: float            # capital expenditure, TTM (positive magnitude)
    sbc_ttm: float              # stock-based compensation, TTM
    diluted_shares_now: float   # current diluted share count
    diluted_shares_prior: float  # diluted share count one year earlier
    market_cap: float
    # PEG inputs on ONE basis (D1): forward_pe = price / FY1 consensus EPS and
    # forward_eps_growth = 100 x (FY1 / FY0 - 1). None = untestable (estimates
    # absent, non-positive or in another currency than the price); a number
    # <= 0 is real negative consensus growth, i.e. a FAILED PEG.
    forward_pe: float | None
    forward_eps_growth: float | None
    net_income_ttm: float       # trailing net income (same basis) — gates routing

    # Current-snapshot balance-sheet lines ($millions) for the balance-sheet gates.
    # Defaults are GATE-PASSING (net cash, no goodwill, healthy equity/EBIT) and exist
    # purely as test ergonomics — every provider always populates the real values.
    total_debt: float = 0.0
    cash_and_equivalents: float = 0.0
    goodwill: float = 0.0
    total_assets: float = 1000.0
    operating_income: float = 200.0   # EBIT (pre-tax) — drives ROIC
    total_equity: float = 1000.0
    goodwill_assumed_zero: bool = False  # goodwill line absent from source -> assumed 0
    debt_assumed_zero: bool = False      # total-debt line absent from source -> assumed 0

    # Optional provenance / confidence annotations from the data layer.
    low_confidence: list[str] = field(default_factory=list)
    name: str = ""              # human-readable company name (defaults to ticker)
    basis: str = ""             # accounting basis used: "TTM", "annual", or ""
    sbc_assumed_zero: bool = False  # SBC line absent from source -> assumed 0
    overridden_fields: list[str] = field(default_factory=list)  # from overrides.json
    sector: str = "Unknown"     # GICS-style sector bucket from the data layer
    industry: str = ""          # finer sub-sector (optional)
    # Same-fiscal-quarter revenue pair (RAW statement lines, both sourced —
    # never an estimated split of a yearly figure) for the growth derivation
    # when revenue_ttm_prior is not summable from the source. None = the pair
    # was not sourced; growth falls back to the TTM pair.
    revenue_latest_q: float | None = None    # newest quarter's revenue
    revenue_year_ago_q: float | None = None  # same fiscal quarter, 1 yr earlier
    # First trading date, ISO "YYYY-MM-DD". None = unknown (never guessed): the
    # name is screened normally and flagged ipo_date_unknown, NOT classified.
    ipo_date: str | None = None
    # Split/dividend-adjusted price snapshot for the ENTRY-TIMING annotations
    # (informational only — never read by any gate). None = price history was
    # unavailable (or, for price_3mo_ago, the listing is younger than 3 months);
    # the annotations render N/A rather than fabricating a level.
    current_price: float | None = None   # last adjusted close
    ath_price: float | None = None       # all-time-high adjusted close
    price_3mo_ago: float | None = None   # adjusted close ~3 months back
    price_history_years: float | None = None  # span the ATH window actually covers
    # Raw analyst-consensus inputs behind forward_pe / forward_eps_growth (D1):
    # EPS averages for the current (FY0) and next (FY1) fiscal year, the currency
    # the estimates are quoted in, and the FY1 analyst count. Informational — no
    # gate reads them; the NEEDS_DATA hint uses them to name why PEG is
    # untestable. None = unknown, never a fabricated value.
    eps_fy0: float | None = None
    eps_fy1: float | None = None
    estimate_currency: str | None = None
    fy1_analysts: int | None = None
    # True when market_cap was converted into the statement currency (D18, #58). The
    # unlock prices (market cap / shares) are then not shown, until #62 derives them
    # from the traded price instead.
    market_cap_converted: bool = False
    # Annual statement history (OLDEST -> NEWEST) for the multi-year consistency
    # gate. Empty when the data layer could not source a contiguous run.
    history: list[AnnualPeriod] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Coerce Nones (e.g. from older cache entries) and default name to ticker.
        if self.low_confidence is None:
            self.low_confidence = []
        if self.overridden_fields is None:
            self.overridden_fields = []
        if not self.name:
            self.name = self.ticker
        # Cache load passes history back as a list of plain dicts (asdict round-trip);
        # coerce them into AnnualPeriod so equality and the gate see real records.
        if self.history is None:
            self.history = []
        else:
            self.history = [
                p if isinstance(p, AnnualPeriod) else AnnualPeriod(**p)
                for p in self.history
            ]

    @property
    def manual_override(self) -> bool:
        return bool(self.overridden_fields)


@dataclass
class Result:
    """Outcome of screening one :class:`Company`.

    Carries every derived metric, the per-filter booleans, a human-readable
    track label, and the list of rejection reasons (empty when passed).
    """

    company: Company

    # derived metrics
    growth: float
    fcf: float
    adj_fcf: float
    adj_margin: float
    rule40: float
    p_s: float
    sbc_pct: float
    dilution: float
    peg: float | None

    # balance-sheet derived metrics (current snapshot)
    net_debt: float
    net_debt_to_fcf: float
    goodwill_to_assets: float
    invested_capital: float
    roic: float | None       # None when invested_capital <= 0 or EBIT absent

    # per-filter booleans
    pass_sbc: bool
    pass_rule40: bool
    pass_ps: bool
    pass_peg: bool
    quality_gate: bool

    # balance-sheet gate booleans
    pass_leverage: bool
    pass_goodwill: bool
    pass_roic: bool
    roic_applied: bool       # True only when ROIC was an active gate (Track A + strict)

    # earnings-quality gate (cumulative adjusted-FCF / net-income; profitable names only)
    pass_earnings_quality: bool         # True (never fails) when not applicable
    earnings_quality_applicable: bool   # False when cumulative NI <= 0 or no usable years
    earnings_quality_ratio: float | None
    earnings_quality_years: int         # actual window used (< EARNINGS_QUALITY_WINDOW if thin)
    # Cumulative capex as % of cumulative revenue over the SAME window (None:
    # no usable years). INFORMATIONAL disambiguator for a failed gate: above
    # EQ_CAPEX_INTENSITY_NOTE_MIN the shortfall may be reinvestment (growth
    # capex / working-capital build), not accrual rot — never flips the gate.
    eq_capex_intensity: float | None

    # entry-timing annotations (INFORMATIONAL ONLY — the screen answers "what is
    # worth owning", not "when to buy"; these never touch verdicts or ranking)
    pct_off_ath: float | None           # distance from all-time high, % (<= 0 typically)
    three_month_return: float | None    # trailing 3-month total return, % (None: young/no data)
    hot: bool                           # 3-mo return > +50% AND within 20% of ATH

    # watchlist unlock prices (set ONLY on a WATCHLIST result; else None) — the
    # pre-computed buy triggers: the price at which the failed valuation filter
    # would clear, on the same share count / revenue base as the P/S itself
    unlock_ps_price: float | None       # price where P/S == 0.5 x growth% (None: growth <= 0)
    unlock_peg_price: float | None      # price where PEG == 2.0 (None: PEG wasn't the blocker)

    # verdict
    status: ScreenStatus
    track_a: bool
    track_b: bool
    track: str               # "Track A", "Track B", or "—"
    reasons: list[str]       # rejection reasons (empty if passed)

    # Multi-year consistency gate (None when the gate did not run).
    consistency: "ConsistencyResult | None" = None

    # Informational sector annotation, attached post-hoc by
    # :func:`annotate_sector_context`. NEVER read by :func:`evaluate` or the
    # default ranking — it cannot change passed / track / rank.
    sector_context: "SectorContext | None" = None

    # Multi-year ROIC durability (advisory — None when the name never reached a
    # snapshot qualifier, mirroring ``consistency``). Never gates the verdict.
    roic_history: "RoicHistory | None" = None

    # Multi-year gross-margin level & stability (advisory — same lifecycle as
    # ``roic_history``). Never gates the verdict, never appears in ranking.
    margin_profile: "MarginProfile | None" = None

    @property
    def passed(self) -> bool:
        return self.status is ScreenStatus.PASS

    @property
    def evaluable(self) -> bool:
        """False only when metric math never ran (pre-revenue / divide-by-zero)."""
        return self.status not in (ScreenStatus.PRE_REVENUE, ScreenStatus.NOT_EVALUABLE)

    @property
    def insufficient_history(self) -> bool:
        """True ONLY for a snapshot qualifier demoted for lacking annual history —
        a snapshot-failing name is a normal rejection and never lands in that bucket."""
        return self.status is ScreenStatus.INSUFFICIENT_HISTORY

    @property
    def recent_ipo(self) -> bool:
        return self.status is ScreenStatus.RECENT_IPO

    @property
    def watchlist(self) -> bool:
        """Quality passed, only valuation failed — a patience case with a buy trigger."""
        return self.status is ScreenStatus.WATCHLIST

    @property
    def needs_data(self) -> bool:
        """Every testable gate passed but PEG could not be administered (no
        forward estimates) — a failed test rejects, an unadministrable one
        asks for data. overrides.json can supply forward_pe / forward_eps_growth
        (and a verified market_cap when the Yahoo cap looks wrong)."""
        return self.status is ScreenStatus.NEEDS_DATA

    @property
    def otc_adr_data_suspect(self) -> bool:
        """Informational: 5-letter OTC ADR (ends in Y/F) — Yahoo caps/statements
        often broken. Never a gate."""
        return is_otc_adr_ticker(self.company.ticker)

    @property
    def financial_sector_note(self) -> str | None:
        """Informational caveat for Financial Services names; never a gate."""
        if (self.company.sector or "") == FINANCIAL_SERVICES_SECTOR:
            return FINANCIAL_SECTOR_NOTE
        return None

    @property
    def ipo_date_unknown(self) -> bool:
        """The source exposed no first-trading date — screened normally, flagged."""
        return self.company.ipo_date is None

    @property
    def ticker(self) -> str:
        return self.company.ticker

    @property
    def sector(self) -> str:
        return self.company.sector or "Unknown"

    @property
    def name(self) -> str:
        return self.company.name

    @property
    def basis(self) -> str:
        return self.company.basis

    @property
    def sbc_assumed_zero(self) -> bool:
        return self.company.sbc_assumed_zero

    @property
    def manual_override(self) -> bool:
        return self.company.manual_override

    @property
    def ps_guardrail(self) -> float:
        """The P/S ceiling for this name (0.5 x revenue growth%)."""
        return PS_GUARDRAIL_FACTOR * self.growth

    @property
    def ps_headroom(self) -> float:
        """How far P/S sits below its guardrail ceiling (higher = more room)."""
        return PS_GUARDRAIL_FACTOR * self.growth - self.p_s


def _period_metrics(
    revenue: float, revenue_prior: float, ocf: float, capex: float, sbc: float,
    growth_pair: tuple[float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Return ``(growth, adj_fcf, adj_margin, rule40)`` for one period.

    The single source of truth for the per-period math, shared by the live
    snapshot in :func:`evaluate` and the historical years in the consistency
    gate so there is zero methodology drift between them.

    ``growth_pair`` (``(now, year_ago)``) substitutes a same-fiscal-quarter YoY
    revenue pair into the growth term only — the level ratios (margin, and the
    Rule-of-40 margin component) stay on the shared ``revenue`` base. It exists
    for sources that publish too few quarters to sum a true prior-year TTM
    (see ``Company.revenue_ttm_prior``); historical years never pass it.
    """
    growth_now, growth_base = growth_pair if growth_pair else (revenue, revenue_prior)
    growth = pct(growth_now - growth_base, growth_base)
    adj_fcf = ocf - capex - sbc
    adj_margin = pct(adj_fcf, revenue)
    rule40 = growth + adj_margin
    return growth, adj_fcf, adj_margin, rule40


def evaluate(c: Company, consistency_years: int = 0,
             consistency_mode: str = "strict",
             ipo_seasoning_months: int = IPO_SEASONING_MONTHS,
             today: date | None = None) -> Result:
    """Apply the screen to one company and return a fully populated Result.

    Order of operations: a **pre-revenue firewall** fires first (no metric math
    on a name with no revenue); then the snapshot four-filter / two-track routing
    (UNCHANGED); then — only for a snapshot qualifier — the current-snapshot
    balance-sheet gates and the multi-year consistency gate. A snapshot failer
    whose only blockers were valuation (WATCHLIST) or an untestable PEG
    (NEEDS_DATA) gets the same gates run on its would-be track. Finally, a name
    whose first trading date falls inside the last ``ipo_seasoning_months`` is
    reclassified ``RECENT_IPO`` regardless of the underlying verdict — recent
    IPOs structurally break the history-based inputs and carry hostile base
    rates, so they are set aside visibly rather than screened as normal names
    (an unknown IPO date is flagged, never guessed).

    ``consistency_years`` of 0 (the default) bypasses the historical gate while
    leaving the pre-revenue and balance-sheet gates active. ``consistency_mode``
    selects the Track-A durability rule (``"strict"`` = Rule of 40 in every year,
    the default for library callers; ``"trend"`` = trajectory-aware). Track B is
    unaffected by the mode. ``today`` exists so the pure module stays testable
    without patching the clock; it defaults to the real current date.
    """
    # 1. Pre-revenue firewall — the FIRST check, before any metric math.
    if c.revenue_ttm <= 0:
        return _pre_revenue_result(c)

    # 2. Snapshot metrics + balance-sheet derived metrics. Growth prefers the
    # sourced quarterly YoY pair when the provider could not sum a true prior
    # TTM (a non-positive year-ago quarter falls back to the TTM pair, which
    # is NaN in that case -> NOT_EVALUABLE, the honest divide-by-zero verdict).
    growth_pair = None
    if (c.revenue_latest_q is not None and c.revenue_year_ago_q is not None
            and c.revenue_year_ago_q > 0):
        growth_pair = (c.revenue_latest_q, c.revenue_year_ago_q)
    growth, adj_fcf, adj_margin, rule40 = _period_metrics(
        c.revenue_ttm, c.revenue_ttm_prior, c.ocf_ttm, c.capex_ttm, c.sbc_ttm,
        growth_pair=growth_pair,
    )
    fcf = c.ocf_ttm - c.capex_ttm
    p_s = c.market_cap / c.revenue_ttm if c.revenue_ttm else nan
    sbc_pct = pct(c.sbc_ttm, c.revenue_ttm)
    dilution = pct(c.diluted_shares_now - c.diluted_shares_prior, c.diluted_shares_prior)

    net_debt = c.total_debt - c.cash_and_equivalents
    goodwill_to_assets = c.goodwill / c.total_assets if c.total_assets > 0 else nan
    invested_capital = c.total_debt + c.total_equity - c.cash_and_equivalents
    roic = (
        c.operating_income / invested_capital
        if invested_capital > 0 and not isnan(c.operating_income)
        else None
    )
    # Leverage ratio: 0 when net cash; undefined (NaN) when there is net debt but
    # no FCF to service it; else net debt as a multiple of unadjusted FCF.
    if net_debt <= 0:
        net_debt_to_fcf = 0.0
    elif fcf <= 0:
        net_debt_to_fcf = nan
    else:
        net_debt_to_fcf = net_debt / fcf

    # PEG is TESTABLE when both inputs are known. Positive growth gives the ratio;
    # growth <= 0 is a FAILED PEG (the ratio is undefined, so peg stays None, but
    # the test was administered); unknown inputs leave it untestable.
    peg_testable = c.forward_pe is not None and c.forward_eps_growth is not None
    peg = (
        c.forward_pe / c.forward_eps_growth
        if peg_testable and c.forward_eps_growth > 0
        else None
    )

    # Routing keys solely on trailing GAAP profitability.
    gaap_profitable = c.net_income_ttm > 0

    # If any input that gates a filter is NaN, the name can't be evaluated.
    # net_income_ttm is included as belt-and-suspenders: a NaN here would slip
    # through gaap_profitable as False, so guard it like the derived metrics.
    evaluable = not any(
        isnan(x) for x in (growth, adj_margin, p_s, sbc_pct, dilution, c.net_income_ttm)
    )

    pass_sbc = sbc_pct <= SBC_MAX_PCT
    pass_rule40 = rule40 >= RULE40_MIN
    pass_ps = p_s <= PS_GUARDRAIL_FACTOR * growth
    pass_peg = peg is not None and peg <= PEG_MAX
    quality_gate = (adj_fcf > 0) and (dilution < growth)

    # CRITICAL: routing is gated on GAAP profitability, NOT on whether PEG is
    # computable. A profitable name must clear PEG via Track A (and is barred
    # from Track B even when PEG is N/A); only a GAAP-unprofitable name is
    # eligible for the PEG-exempt Track B. The quality_* flags are the same
    # routing with the valuation filters (P/S, PEG) removed — they exist ONLY
    # to detect the WATCHLIST case (quality passed, valuation the sole blocker)
    # and never loosen the tracks themselves.
    quality_a = gaap_profitable and pass_sbc and pass_rule40
    quality_b = (not gaap_profitable) and pass_sbc and pass_rule40 and quality_gate
    track_a = quality_a and pass_ps and pass_peg
    track_b = quality_b and pass_ps

    # Balance-sheet gates. Leverage + goodwill apply to ALL snapshot qualifiers;
    # ROIC is gated only for a Track-A name under strict mode (advisory otherwise).
    pass_leverage = net_debt <= 0 or (fcf > 0 and net_debt_to_fcf <= NET_DEBT_TO_FCF_MAX)
    pass_goodwill = c.total_assets > 0 and goodwill_to_assets <= GOODWILL_TO_ASSETS_MAX
    pass_roic = roic is None or roic >= ROIC_MIN
    roic_applied = track_a and consistency_mode == "strict"

    # Earnings quality: cumulative adjusted-FCF / net-income over the trailing
    # window. N/A (never fails) when not applicable (unprofitable cumulatively,
    # or no net-income history); applies to any snapshot qualifier, either track.
    (eq_applicable, eq_ratio, eq_years, pass_earnings_quality,
     eq_capex_intensity) = _earnings_quality(c.history)

    # Entry timing (informational): % off ATH, 3-month return, and the HOT flag
    # for the downstream anti-FOMO entry rule. Never feeds a gate or the rank.
    pct_off_ath, three_month_return, hot = _entry_timing(c)

    consistency: ConsistencyResult | None = None
    roic_hist: RoicHistory | None = None
    margin_prof: MarginProfile | None = None
    unlock_ps_price: float | None = None
    unlock_peg_price: float | None = None

    def _run_quality_gates(track_a_like: bool):
        """``(bs_reasons, consistency)`` for a real or hypothetical track.

        The ONE place the snapshot-qualifier gate set (leverage, goodwill,
        ROIC-when-applicable, earnings quality, and the multi-year consistency
        gate) is assembled. The real qualifier path and the hypothetical
        WATCHLIST candidacy both call it, so a future gate cannot be added to
        one path and silently forgotten in the other. ``track_a_like`` picks
        the Track-A rules (strict-mode ROIC gating, Track-A consistency) vs
        Track B's; a closure so the gate inputs can't drift between callers.
        """
        gate_roic_applied = track_a_like and consistency_mode == "strict"
        bs = _balance_sheet_reasons(
            pass_leverage, pass_goodwill, gate_roic_applied, pass_roic,
            net_debt, fcf, net_debt_to_fcf, goodwill_to_assets, roic,
            eq_applicable, pass_earnings_quality, eq_ratio, eq_years,
            eq_capex_intensity,
        )
        hist = None
        if consistency_years > 0:
            hist = (
                track_a_consistency(c.history, consistency_years, consistency_mode)
                if track_a_like
                else track_b_consistency(c.history, consistency_years)
            )
        return bs, hist

    if not evaluable:
        status = ScreenStatus.NOT_EVALUABLE
        track = "—"
        reasons = ["could not evaluate (missing/zero denominator)"]
    elif not (track_a or track_b):
        status = ScreenStatus.REJECTED
        track = "—"
        reasons = _rejection_reasons(
            gaap_profitable, pass_sbc, pass_rule40, pass_ps, quality_gate,
            sbc_pct, rule40, p_s, growth, peg,
            c.forward_eps_growth if peg_testable else None,
        )
        if EPS_DECLINE_NOTE in c.low_confidence:  # advisory text only, never a gate
            prefix = "PEG failed: consensus EPS growth"
            reasons = [f"{x} [{EPS_DECLINE_NOTE}]" if x.startswith(prefix) else x
                       for x in reasons]
        # WATCHLIST candidacy: every quality gate passed and ONLY valuation
        # (P/S and/or a computable-but-failed PEG) blocked a track. The same
        # balance-sheet / earnings-quality / consistency gates are then run on
        # the would-be track — a name failing any of those stays plain REJECTED
        # (per the fixed architecture, a snapshot failer's hypothetical gates
        # are moot, so their outcomes are not attached or reported). A
        # profitable name whose PEG is N/A is NOT a candidate: no price fixes
        # missing forward estimates or negative consensus growth, and Track B
        # stays barred.
        #
        # NEEDS_DATA candidacy: the disjoint sibling case — a profitable name
        # whose ONLY blocker is an UNTESTABLE PEG (estimates absent,
        # non-positive or in another currency — e.g. an unsponsored ADR) while
        # every testable gate passes, P/S included. A failed test rejects (so
        # consensus growth <= 0 is REJECTED, not NEEDS_DATA); an
        # unadministrable one asks for data — the user can supply forward_pe /
        # forward_eps_growth via overrides.json. Track B stays barred either
        # way (never in the shortlist), and a name that ALSO fails P/S or any
        # quality gate stays plain REJECTED: more estimate data could not have
        # saved it.
        eligible = (quality_a and peg is not None) or quality_b
        needs_data_eligible = quality_a and not peg_testable and pass_ps
        if eligible or needs_data_eligible:
            wl_bs_reasons, wl_consistency = _run_quality_gates(quality_a)
            hist_status = None if wl_consistency is None else wl_consistency.status
            if needs_data_eligible:
                # NEEDS_DATA is the no-failure state: only a real gate FAIL
                # rejects the candidacy. INSUFFICIENT history is one more
                # missing-data condition (the unsponsored-ADR profile pairs
                # thin estimates WITH thin history), not a verdict — the
                # attached consistency result reports it honestly.
                candidacy_clears = hist_status is not ConsistencyStatus.FAIL
            else:
                # WATCHLIST claims "every quality gate passed" — INSUFFICIENT
                # can't back that claim, and the name already carries a real
                # valuation failure, so plain REJECTED stays truthful.
                candidacy_clears = hist_status in (None, ConsistencyStatus.PASS)
            if not wl_bs_reasons and candidacy_clears:
                consistency = wl_consistency
                roic_hist = roic_history(c.history)
                margin_prof = margin_profile(c.history)
                if needs_data_eligible:
                    status = ScreenStatus.NEEDS_DATA
                    reasons = [needs_data_hint(c)]
                else:
                    status = ScreenStatus.WATCHLIST
                    unlock_ps_price, unlock_peg_price = _unlock_prices(
                        c, growth, p_s, peg, quality_a, pass_ps, pass_peg,
                    )
    else:
        # A snapshot track cleared — layer the balance-sheet and consistency gates.
        track = "Track A" if track_a else "Track B"
        roic_hist = roic_history(c.history)
        margin_prof = margin_profile(c.history)
        bs_reasons, consistency = _run_quality_gates(track_a)

        # Status precedence: a balance-sheet failure is definitive and wins over
        # the historical gate; otherwise insufficiency, then a consistency fail.
        if bs_reasons:
            status = ScreenStatus.REJECTED
            track = "—"
            reasons = bs_reasons
            if consistency is not None and consistency.status is ConsistencyStatus.FAIL:
                reasons = reasons + [
                    _consistency_fail_reason(consistency, track_a, consistency_years)
                ]
        elif consistency is not None and consistency.status is ConsistencyStatus.INSUFFICIENT:
            status = ScreenStatus.INSUFFICIENT_HISTORY
            track = "—"
            reasons = [
                f"insufficient annual history "
                f"({consistency.years_available} of {consistency.years_required} yrs)"
            ]
        elif consistency is not None and consistency.status is ConsistencyStatus.FAIL:
            status = ScreenStatus.REJECTED
            track = "—"
            reasons = [_consistency_fail_reason(consistency, track_a, consistency_years)]
        else:
            status = ScreenStatus.PASS
            reasons = []

    # 3. IPO-seasoning override — LAST, unconditional (mirrors how
    # INSUFFICIENT_HISTORY keeps a name out of the shortlist): whatever the
    # gates decided, an unseasoned listing is reported in its own section.
    if _ipo_is_recent(c.ipo_date, ipo_seasoning_months, today or date.today()):
        status = ScreenStatus.RECENT_IPO
        track = "—"
        reasons = [
            f"recent IPO (listed {c.ipo_date}, "
            f"< {ipo_seasoning_months} mo seasoning)"
        ]
        # Unlock prices are WATCHLIST buy triggers — an unseasoned listing has
        # no actionable trigger, so a would-be-WATCHLIST name sheds them here.
        unlock_ps_price = unlock_peg_price = None

    return Result(
        company=c,
        growth=growth, fcf=fcf, adj_fcf=adj_fcf, adj_margin=adj_margin,
        rule40=rule40, p_s=p_s, sbc_pct=sbc_pct, dilution=dilution, peg=peg,
        net_debt=net_debt, net_debt_to_fcf=net_debt_to_fcf,
        goodwill_to_assets=goodwill_to_assets, invested_capital=invested_capital, roic=roic,
        pass_sbc=pass_sbc, pass_rule40=pass_rule40, pass_ps=pass_ps,
        pass_peg=pass_peg, quality_gate=quality_gate,
        pass_leverage=pass_leverage, pass_goodwill=pass_goodwill,
        pass_roic=pass_roic, roic_applied=roic_applied,
        pass_earnings_quality=pass_earnings_quality,
        earnings_quality_applicable=eq_applicable,
        earnings_quality_ratio=eq_ratio, earnings_quality_years=eq_years,
        eq_capex_intensity=eq_capex_intensity,
        pct_off_ath=pct_off_ath, three_month_return=three_month_return, hot=hot,
        unlock_ps_price=unlock_ps_price, unlock_peg_price=unlock_peg_price,
        status=status, track_a=track_a, track_b=track_b,
        track=track, reasons=reasons, consistency=consistency,
        roic_history=roic_hist, margin_profile=margin_prof,
    )


def _unlock_prices(
    c: Company, growth: float, p_s: float, peg: float | None,
    track_a_like: bool, pass_ps: bool, pass_peg: bool,
) -> tuple[float | None, float | None]:
    """``(unlock_ps_price, unlock_peg_price)`` — the WATCHLIST buy triggers.

    Both filters scale linearly with price on the SAME share count / revenue
    base as the P/S computation itself (implied price = market_cap / diluted
    shares), so each unlock is the current implied price scaled by the ratio of
    the filter's ceiling to its current value:

    * P/S:  price where ``P/S == 0.5 x growth%`` — N/A when growth <= 0 (the
      guardrail is then non-positive: no price fixes a shrinking company, it
      must re-qualify on quality first).
    * PEG:  price where ``PEG == 2.0`` — only when PEG was actually the blocker
      (a Track-B-like name has no PEG concept).

    A filter that already passes needs no unlock and stays None.
    """
    if not c.diluted_shares_now or p_s <= 0 or c.market_cap_converted:
        return None, None
    implied_price = c.market_cap / c.diluted_shares_now
    unlock_ps = None
    if not pass_ps and growth > 0:
        unlock_ps = implied_price * (PS_GUARDRAIL_FACTOR * growth / p_s)
    unlock_peg = None
    if track_a_like and not pass_peg and peg is not None and peg > 0:
        unlock_peg = implied_price * (PEG_MAX / peg)
    return unlock_ps, unlock_peg


def _entry_timing(c: Company) -> tuple[float | None, float | None, bool]:
    """``(pct_off_ath, three_month_return, hot)`` from the adjusted price snapshot.

    The screen answers "what is worth owning", deliberately not "when to buy" —
    but the downstream human workflow applies an anti-FOMO entry rule (no initial
    buy into a parabolic move near highs), and checking that mechanically for
    every survivor is exactly what a tool should absorb. HOT = trailing 3-month
    return > +50% AND price within 20% of the all-time high; it marks a
    *patience* case, never a rejection. Missing prices -> None (rendered N/A),
    never a fabricated level; a listing younger than 3 months has no 3-month
    return by construction.
    """
    pct_off_ath = (
        pct(c.current_price - c.ath_price, c.ath_price)
        if c.current_price is not None and c.ath_price else None
    )
    three_month_return = (
        pct(c.current_price - c.price_3mo_ago, c.price_3mo_ago)
        if c.current_price is not None and c.price_3mo_ago else None
    )
    hot = (
        three_month_return is not None and three_month_return > HOT_3MO_RETURN_MIN
        and pct_off_ath is not None and pct_off_ath >= -HOT_ATH_PROXIMITY_PCT
    )
    return pct_off_ath, three_month_return, hot


def _months_before(today: date, months: int) -> date:
    """The calendar date ``months`` months before ``today`` (day clamped to month end)."""
    total = today.year * 12 + (today.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(today.day, monthrange(year, month)[1])
    return date(year, month, day)


def _ipo_is_recent(ipo_date: str | None, months: int, today: date) -> bool:
    """True when a parseable first-trading date falls inside the seasoning window.

    ``None`` (unknown) and unparseable dates are NOT recent — never guess; such
    names are screened normally and surfaced via ``Result.ipo_date_unknown``.
    A non-positive ``months`` disables the check entirely.
    """
    if ipo_date is None or months <= 0:
        return False
    try:
        listed = date.fromisoformat(str(ipo_date)[:10])
    except ValueError:
        return False
    return listed > _months_before(today, months)


def _pre_revenue_result(c: Company) -> Result:
    """A Result for a pre-revenue name — metric math never ran, every gate moot."""
    return Result(
        company=c,
        growth=nan, fcf=nan, adj_fcf=nan, adj_margin=nan, rule40=nan,
        p_s=nan, sbc_pct=nan, dilution=nan, peg=None,
        net_debt=nan, net_debt_to_fcf=nan, goodwill_to_assets=nan,
        invested_capital=nan, roic=None,
        pass_sbc=False, pass_rule40=False, pass_ps=False, pass_peg=False,
        quality_gate=False, pass_leverage=False, pass_goodwill=False,
        pass_roic=False, roic_applied=False,
        pass_earnings_quality=False, earnings_quality_applicable=False,
        earnings_quality_ratio=None, earnings_quality_years=0,
        eq_capex_intensity=None,
        pct_off_ath=None, three_month_return=None, hot=False,
        unlock_ps_price=None, unlock_peg_price=None,
        status=ScreenStatus.PRE_REVENUE, track_a=False, track_b=False,
        track="—", reasons=["pre-revenue (revenue_ttm <= 0)"], consistency=None,
    )


def _balance_sheet_reasons(
    pass_leverage: bool, pass_goodwill: bool, roic_applied: bool, pass_roic: bool,
    net_debt: float, fcf: float, net_debt_to_fcf: float,
    goodwill_to_assets: float, roic: float | None,
    eq_applicable: bool = False, pass_earnings_quality: bool = True,
    eq_ratio: float | None = None, eq_years: int = 0,
    eq_capex_intensity: float | None = None,
) -> list[str]:
    """All failing balance-sheet-gate reasons for a snapshot qualifier (no short-circuit).

    ROIC contributes a reason only when it was an active gate (Track A + strict)
    AND a value was computable; a not-computable ROIC is advisory, never a fail.
    Earnings quality contributes a reason only when applicable (cumulative net
    income > 0 over the trailing window) AND it failed.
    """
    reasons: list[str] = []
    if not pass_leverage:
        if net_debt > 0 and fcf <= 0:
            reasons.append("net debt with non-positive FCF (no cash to service debt)")
        else:
            reasons.append(
                f"Net Debt/FCF = {net_debt_to_fcf:.1f} (> {NET_DEBT_TO_FCF_MAX:.1f})"
            )
    if not pass_goodwill:
        if isnan(goodwill_to_assets):
            reasons.append("total assets <= 0 (broken balance sheet)")
        else:
            reasons.append(
                f"Goodwill/Assets = {goodwill_to_assets * 100:.0f}% "
                f"(> {GOODWILL_TO_ASSETS_MAX * 100:.0f}%)"
            )
    if roic_applied and roic is not None and not pass_roic:
        reasons.append(f"ROIC = {roic * 100:.1f}% (< {ROIC_MIN * 100:.0f}%)")
    if eq_applicable and not pass_earnings_quality:
        reason = (
            f"earnings quality: cumulative {eq_years}-yr AdjFCF/NI = {eq_ratio:.2f} "
            f"(< {EARNINGS_QUALITY_MIN:.1f})"
        )
        # The ratio can't tell accrual rot from heavy reinvestment; when capex
        # over the same window is intense, say so next to the rejection (the
        # gate still fails — the annotation flags the ambiguity, never waives it).
        if (eq_capex_intensity is not None
                and eq_capex_intensity > EQ_CAPEX_INTENSITY_NOTE_MIN):
            reason += (
                f" — EQ shortfall may reflect capex intensity "
                f"(capex {eq_capex_intensity:.0f}% of revenue), not accruals"
            )
        reasons.append(reason)
    return reasons


def _rejection_reasons(
    gaap_profitable: bool, pass_sbc: bool, pass_rule40: bool, pass_ps: bool,
    quality_gate: bool, sbc_pct: float, rule40: float, p_s: float,
    growth: float, peg: float | None, eps_growth: float | None = None,
) -> list[str]:
    """Build a human-readable list of why a name was rejected.

    Branches on GAAP profitability — the routing key — not on whether PEG is
    computable: a profitable name lives or dies on Track A (PEG required), an
    unprofitable one on the Track B quality gate. ``eps_growth`` is the consensus
    growth when PEG was testable (None when untestable).
    """
    reasons: list[str] = []
    if not pass_rule40:
        reasons.append(f"Rule of 40 = {rule40:.1f} (< {RULE40_MIN:.0f})")
    if not pass_ps:
        reasons.append(
            f"P/S = {p_s:.1f} (> guardrail {PS_GUARDRAIL_FACTOR * growth:.1f})"
        )
    if not pass_sbc:
        reasons.append(f"SBC = {sbc_pct:.1f}% of revenue (> {SBC_MAX_PCT:.0f}%)")
    if gaap_profitable:
        # Profitable -> Track A only, so PEG must clear; Track B is barred.
        if eps_growth is not None and eps_growth <= 0:
            reasons.append(
                f"PEG failed: consensus EPS growth <= 0 ({eps_growth:.1f}%); "
                "profitable, Track B barred"
            )
        elif peg is None:
            reasons.append(
                "PEG not computable (no usable consensus estimates); "
                "profitable name must clear PEG, Track B barred"
            )
        elif peg > PEG_MAX:
            reasons.append(
                f"PEG = {peg:.1f} (> {PEG_MAX:.1f}); profitable, Track B barred"
            )
    elif not quality_gate:
        # Unprofitable -> Track B only; rejection comes from the quality gate.
        reasons.append("fails Track B quality gate (adj FCF<=0 or dilution>=growth)")
    return reasons or ["did not clear either track"]


# ---------------------------------------------------------------------------
# ROIC history (advisory, multi-year durable-moat proxy) — pure, no IO
# ---------------------------------------------------------------------------
@dataclass
class RoicHistory:
    """Multi-year ROIC trajectory for one company — advisory only, never a gate.

    A single-year ROIC is noisy (one restructuring charge or a cyclical margin
    peak can swing it violently); the median over up to 10 years of history is
    a much stronger proxy for a durable moat — a company that clears its cost
    of capital by a wide, repeated spread rather than one great year.
    """

    median: float | None    # None when no year had a usable ROIC
    years_used: int         # count of years actually used in the median
    durable: bool           # median >= ROIC_DURABLE_MIN over >= ROIC_DURABLE_YEARS
    note: str = ""          # e.g. "insufficient ROIC history" (advisory, informational)


def roic_history(history: list[AnnualPeriod]) -> RoicHistory:
    """Median ROIC (pre-tax operating income / invested capital) over available years.

    Up to the last 10 annual periods are considered. A year is skipped (never
    fabricated) when ``operating_income`` is absent, or when invested capital
    (``total_debt + total_equity - cash_and_equivalents``) is <= 0 for that year.
    Fewer than :data:`ROIC_HISTORY_MIN_YEARS` usable years is flagged as thin
    evidence but is still reported honestly, never withheld or padded.
    """
    values: list[float] = []
    for p in history[-10:]:
        if p.operating_income is None:
            continue
        if p.total_debt is None or p.total_equity is None or p.cash_and_equivalents is None:
            continue
        invested_capital = p.total_debt + p.total_equity - p.cash_and_equivalents
        if invested_capital <= 0:
            continue
        values.append(p.operating_income / invested_capital)

    if not values:
        return RoicHistory(None, 0, False, note="no usable ROIC years")

    med = median(values)
    years_used = len(values)
    durable = med >= ROIC_DURABLE_MIN and years_used >= ROIC_DURABLE_YEARS
    note = "insufficient ROIC history" if years_used < ROIC_HISTORY_MIN_YEARS else ""
    return RoicHistory(med, years_used, durable, note=note)


# ---------------------------------------------------------------------------
# Gross-margin level & stability (advisory, multi-year) — pure, no IO
# ---------------------------------------------------------------------------
@dataclass
class MarginProfile:
    """Multi-year gross-margin level + stability — advisory only, never a gate.

    Gross margin is where pricing power shows up first: a high AND stable
    margin through cycles means customers keep paying up and competitors can't
    force discounting. Hard-gating on it would double-punish capital-intensive
    sectors the screen is already structurally biased against, so this stays
    purely informational.
    """

    gm_latest: float | None    # most recent usable year's gross margin, %
    gm_stddev: float | None    # population stddev of gross margin, percentage points
    years_used: int
    pricing_power: bool        # gm_latest >= 40, stddev < 5pp, over >= 5 usable years
    not_applicable: bool       # zero usable years (e.g. banks/insurers -> no COGS line)


def margin_profile(history: list[AnnualPeriod]) -> MarginProfile:
    """Gross-margin level (latest) and stability (stddev) over available years.

    Up to the last 10 annual periods are considered; a year is skipped (never
    fabricated) when ``cogs`` is absent — the expected case for banks/insurers,
    which don't report a cost-of-revenue line. Zero usable years is flagged
    ``not_applicable`` rather than reporting a misleading N/A-as-zero margin.
    """
    usable = [(p.fiscal_label, pct(p.revenue - p.cogs, p.revenue))
              for p in history[-10:] if p.cogs is not None]

    if not usable:
        return MarginProfile(None, None, 0, False, not_applicable=True)

    values = [v for _, v in usable]
    gm_latest = values[-1]
    years_used = len(values)
    gm_stddev = pstdev(values)
    pricing_power = (
        gm_latest >= PRICING_POWER_GM_MIN
        and gm_stddev < PRICING_POWER_STDDEV_MAX
        and years_used >= PRICING_POWER_MIN_YEARS
    )
    return MarginProfile(gm_latest, gm_stddev, years_used, pricing_power, not_applicable=False)


# ---------------------------------------------------------------------------
# Earnings-quality gate (rejecting; balance-sheet-gate family) — pure, no IO
# ---------------------------------------------------------------------------
def _earnings_quality(
    history: list[AnnualPeriod],
) -> tuple[bool, float | None, int, bool, float | None]:
    """Cumulative adjusted-FCF / net-income ratio over the last
    :data:`EARNINGS_QUALITY_WINDOW` years — the classic accounting red flag for
    profits that never become cash (aggressive revenue recognition, ballooning
    receivables, capitalized costs). A single year is noisy from working-capital
    swings, so this cumulates; :data:`EARNINGS_QUALITY_MIN` tolerates normal
    timing noise while catching persistent accrual inflation.

    Returns ``(applicable, ratio, years_used, passed, capex_intensity)``.
    **N/A, never a fail**, when no year in the window has a net-income figure,
    or cumulative net income is <= 0 (Track B's own FCF gate already covers
    unprofitable names). Uses only the years that actually carry a net-income
    figure — never fabricated — so ``years_used`` can be less than the
    configured window.

    ``capex_intensity`` is cumulative capex as a % of cumulative revenue over
    the SAME years — the disambiguator for a failed gate: the ratio arithmetic
    cannot tell accrual rot (receivables ballooning) from heavy reinvestment
    (growth capex, working-capital build), and above
    :data:`EQ_CAPEX_INTENSITY_NOTE_MIN` the shortfall plausibly reads as the
    latter. Informational only — it never flips ``passed``.
    """
    window = [p for p in history[-EARNINGS_QUALITY_WINDOW:] if p.net_income is not None]
    years_used = len(window)
    if years_used == 0:
        return False, None, 0, True, None
    capex_intensity = pct(sum(p.capex for p in window), sum(p.revenue for p in window))
    if isnan(capex_intensity):
        capex_intensity = None
    cum_ni = sum(p.net_income for p in window)
    if cum_ni <= 0:
        return False, None, years_used, True, capex_intensity
    cum_adj_fcf = sum(p.ocf - p.capex - p.sbc for p in window)
    ratio = cum_adj_fcf / cum_ni
    return True, ratio, years_used, ratio >= EARNINGS_QUALITY_MIN, capex_intensity


# ---------------------------------------------------------------------------
# Multi-year consistency gate (annual durability filter) — pure, no IO
# ---------------------------------------------------------------------------
class ConsistencyStatus(Enum):
    """Outcome of the multi-year consistency gate for a snapshot qualifier."""

    PASS = auto()
    FAIL = auto()
    INSUFFICIENT = auto()


@dataclass
class ConsistencyResult:
    """Verdict + trajectory of the consistency gate, for reporting and audit.

    The trajectory lists are informational (oldest -> newest); the gate itself
    stays binary. ``rule40_by_year`` / ``adj_fcf_by_year`` drive the report's
    deceleration-audit column and the rejection-reason string.
    """

    status: ConsistencyStatus
    years_required: int
    years_available: int
    rule40_by_year: list[tuple[str, float]] = field(default_factory=list)
    adj_fcf_by_year: list[tuple[str, float]] = field(default_factory=list)
    note: str = ""


def track_a_consistency(
    history: list[AnnualPeriod], years: int, mode: str = "strict",
) -> ConsistencyResult:
    """Track A durability over the most-recent windowed Rule-of-40 values.

    A windowed year's Rule of 40 needs its prior year's revenue, so ``years``
    rule40 values require ``years + 1`` contiguous complete annual periods.
    Too few -> INSUFFICIENT. A NaN in the window is a data gap (not a genuine
    fail) -> INSUFFICIENT. The windowing here is mode-independent; only the
    PASS/FAIL decision differs (see :func:`_rule40_trend_pass`):

    * ``strict`` — Rule of 40 >= :data:`RULE40_MIN` in EVERY windowed year.
    * ``trend``  — trajectory-aware (recency anchor + average backstop + no
      lumpy sub-floor collapse).
    """
    required = years + 1
    if len(history) < required:
        return ConsistencyResult(
            ConsistencyStatus.INSUFFICIENT, required, len(history),
            note="need one extra year for the oldest growth rate",
        )
    # The window is the most recent ``years`` periods, each paired with its prior.
    rule40_by_year: list[tuple[str, float]] = []
    for i in range(len(history) - years, len(history)):
        cur, prior = history[i], history[i - 1]
        _, _, _, rule40 = _period_metrics(
            cur.revenue, prior.revenue, cur.ocf, cur.capex, cur.sbc
        )
        rule40_by_year.append((cur.fiscal_label, rule40))
    if any(isnan(v) for _, v in rule40_by_year):
        return ConsistencyResult(
            ConsistencyStatus.INSUFFICIENT, required, len(history),
            rule40_by_year=rule40_by_year, note="data gap (NaN) in window",
        )
    vals = [v for _, v in rule40_by_year]
    status = (
        ConsistencyStatus.PASS
        if _rule40_consistent(vals, mode)
        else ConsistencyStatus.FAIL
    )
    return ConsistencyResult(
        status, required, len(history), rule40_by_year=rule40_by_year,
        note=f"mode={mode}",
    )


def _rule40_consistent(vals: list[float], mode: str) -> bool:
    """PASS/FAIL decision for windowed Rule-of-40 values (oldest -> newest).

    ``strict``: every value clears :data:`RULE40_MIN`.
    ``trend``: the latest year compounds now (``>= RULE40_MIN``), the window
    averages at least :data:`TREND_AVG_MIN`, and there is no *lumpy collapse* —
    a year that drops below :data:`TREND_FLOOR` from a higher prior year. A
    sub-floor first year (a launch year, no prior in the window) is forgiven.
    """
    if not vals:
        return False
    if mode == "strict":
        return all(v >= RULE40_MIN for v in vals)
    if vals[-1] < RULE40_MIN:
        return False
    if mean(vals) < TREND_AVG_MIN:
        return False
    collapse = any(
        v < TREND_FLOOR and i > 0 and v < vals[i - 1]
        for i, v in enumerate(vals)
    )
    return not collapse


def track_b_consistency(history: list[AnnualPeriod], years: int) -> ConsistencyResult:
    """Track B durability: adjusted FCF > 0 in EVERY available year of the window.

    Effective floor is ``min(2, years)`` available annual periods. Fewer ->
    INSUFFICIENT. Otherwise adj FCF (ocf - capex - sbc) must be > 0 every year ->
    PASS else FAIL. The Rule-of-40 trajectory is filled where a prior year exists
    (display only); the gate keys solely on adjusted FCF.
    """
    floor = min(2, years)
    window = history[-years:] if years else []
    if len(window) < floor:
        return ConsistencyResult(
            ConsistencyStatus.INSUFFICIENT, floor, len(window),
            note=f"need at least {floor} annual years of adjusted FCF",
        )
    adj_fcf_by_year = [(p.fiscal_label, p.ocf - p.capex - p.sbc) for p in window]
    # Rule-of-40 trajectory for the window, where a prior year is available.
    start = len(history) - len(window)
    rule40_by_year: list[tuple[str, float]] = []
    for i in range(start, len(history)):
        if i == 0:
            continue  # no prior year for a growth rate
        cur, prior = history[i], history[i - 1]
        _, _, _, rule40 = _period_metrics(
            cur.revenue, prior.revenue, cur.ocf, cur.capex, cur.sbc
        )
        rule40_by_year.append((cur.fiscal_label, rule40))
    status = (
        ConsistencyStatus.PASS
        if all(v > 0 for _, v in adj_fcf_by_year)
        else ConsistencyStatus.FAIL
    )
    return ConsistencyResult(
        status, floor, len(window),
        rule40_by_year=rule40_by_year, adj_fcf_by_year=adj_fcf_by_year,
    )


def _fmt_trajectory(pairs: list[tuple[str, float]]) -> str:
    """Render a ``(label, value)`` series as ``[55.1 -> 42.0 -> 40.5]`` (oldest first)."""
    return "[" + " -> ".join(f"{v:.1f}" for _, v in pairs) + "]"


def _consistency_fail_reason(
    result: ConsistencyResult, track_a: bool, years: int,
) -> str:
    """The rejection-reason string for a name demoted by a FAILED consistency gate."""
    if track_a:
        return (
            f"failed {years}-yr consistency gate "
            f"(Rule40 Hist: {_fmt_trajectory(result.rule40_by_year)})"
        )
    return (
        f"failed {years}-yr consistency gate "
        f"(AdjFCF Hist: {_fmt_trajectory(result.adj_fcf_by_year)})"
    )


def rank(results: list[Result], mode: str = "rule40") -> list[Result]:
    """Rank survivors.

    ``mode="rule40"`` (the default, absolute) preserves the validated ordering:
    Rule of 40 desc, then PEG asc (None last), then P/S headroom desc.

    ``mode="sector-relative"`` reorders the *same* survivor set by how attractive
    each name is within its sector (Rule of 40 above its sector median and P/S
    below it). This is a presentation choice only — it never changes which names
    pass; survivors lacking sector context fall back to the absolute key and sort
    last. Requires :func:`annotate_sector_context` to have run first.
    """
    survivors = [r for r in results if r.passed]
    if mode == "sector-relative":
        return sorted(survivors, key=_sector_relative_key)
    return sorted(survivors, key=_rule40_key)


def _rule40_key(r: Result):
    peg_sort = r.peg if r.peg is not None else float("inf")
    return (-r.rule40, peg_sort, -r.ps_headroom)


def _sector_relative_key(r: Result):
    """Sort key for sector-relative mode: better-than-sector names first.

    Score rewards Rule of 40 above the sector median and P/S below it. Names
    without usable sector medians sort after those that have them, and within
    each group the absolute Rule-of-40 key breaks ties deterministically.
    """
    ctx = r.sector_context
    meds = ctx.medians if (ctx and ctx.available) else {}
    if meds.get("rule40") and meds.get("p_s") and r.p_s > 0:
        r40_rel = r.rule40 / meds["rule40"]          # >1 = above sector median
        ps_rel = meds["p_s"] / r.p_s                 # >1 = cheaper than sector
        return (0, -(r40_rel + ps_rel)) + _rule40_key(r)
    return (1, 0.0) + _rule40_key(r)


@dataclass
class SectorStats:
    """Per-sector medians for one run (only built when peers >= SECTOR_MIN_PEERS)."""

    sector: str
    n: int                       # number of evaluable peers in this sector
    medians: dict[str, float]    # metric name -> median (peg omitted if all N/A)


@dataclass
class SectorContext:
    """Informational sector annotation attached to a single :class:`Result`."""

    sector: str
    peers: int                   # evaluable same-sector names in the run
    available: bool              # True when sector medians were computed
    note: str = ""               # reason when unavailable (e.g. too few peers)
    medians: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)  # value - sector median


def _metric_value(r: Result, metric: str) -> float | None:
    """A result's value for a sector metric, or None when N/A (PEG) or NaN."""
    if metric == "peg":
        return r.peg
    v = getattr(r, metric)
    return None if v is None or isnan(v) else v


def _sector_groups(results: list[Result]) -> dict[str, list[Result]]:
    """Group evaluable results by sector, excluding Unknown-sector names."""
    groups: dict[str, list[Result]] = {}
    for r in results:
        if not r.evaluable:
            continue
        sec = r.company.sector or "Unknown"
        if sec == "Unknown":
            continue
        groups.setdefault(sec, []).append(r)
    return groups


def sector_medians(results: list[Result]) -> dict[str, SectorStats]:
    """Median of each key metric per sector, for sectors with enough peers.

    Pure and network-free. A sector median is computed only when the sector has
    at least :data:`SECTOR_MIN_PEERS` evaluable peers in the run; PEG medians
    ignore N/A names; Unknown-sector names are excluded entirely. This is
    descriptive context — it does not feed back into any verdict or gate.
    """
    out: dict[str, SectorStats] = {}
    for sec, rs in _sector_groups(results).items():
        if len(rs) < SECTOR_MIN_PEERS:
            continue
        meds: dict[str, float] = {}
        for m in _SECTOR_METRICS:
            vals = [v for r in rs if (v := _metric_value(r, m)) is not None]
            if vals:
                meds[m] = median(vals)
        out[sec] = SectorStats(sec, len(rs), meds)
    return out


def annotate_sector_context(results: list[Result]) -> dict[str, SectorStats]:
    """Attach a :class:`SectorContext` to every result; return the sector stats.

    Mutates each result's ``sector_context`` only — it deliberately touches no
    field that :func:`evaluate` or the default :func:`rank` read, so verdicts,
    track routing, and absolute ranking are provably unchanged.
    """
    groups = _sector_groups(results)
    stats = sector_medians(results)
    for r in results:
        sec = r.company.sector or "Unknown"
        st = stats.get(sec)
        if st is not None:
            deltas = {
                m: v - med
                for m, med in st.medians.items()
                if (v := _metric_value(r, m)) is not None
            }
            r.sector_context = SectorContext(
                sec, st.n, True, "", st.medians, deltas)
        elif sec == "Unknown":
            r.sector_context = SectorContext(sec, 0, False, "n/a — sector unknown")
        else:
            r.sector_context = SectorContext(
                sec, len(groups.get(sec, [])), False, "n/a — too few peers (<5)")
    return stats
