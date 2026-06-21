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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from math import isnan, nan
from statistics import mean, median

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

# --- sector context (INFORMATIONAL ONLY — never gates a verdict) -----------
SECTOR_MIN_PEERS = 5    # don't compute a sector median below this many peers
# Result attributes whose per-sector median we report (adj_fcf_margin == adj_margin).
_SECTOR_METRICS = ("rule40", "p_s", "peg", "growth", "adj_margin", "sbc_pct")


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


@dataclass
class AnnualPeriod:
    """One fiscal year of RAW statement lines ($millions), for the consistency gate.

    Raw lines only — never pre-computed ratios — so the per-year math runs through
    the same :func:`_period_metrics` helper as the live snapshot, with zero drift.
    """

    fiscal_label: str           # human-readable year label, e.g. "2023"
    revenue: float
    ocf: float                  # operating cash flow
    capex: float                # capital expenditure (positive magnitude)
    sbc: float                  # stock-based compensation


@dataclass
class Company:
    """Raw fundamentals for one company (all monetary fields in $millions).

    These are *raw statement lines*, never pre-packaged ratios — every metric in
    :func:`evaluate` is derived from these so the methodology stays auditable.
    """

    ticker: str
    revenue_ttm: float          # trailing-twelve-month revenue
    revenue_ttm_prior: float    # the TTM revenue one year earlier
    ocf_ttm: float              # operating cash flow, TTM
    capex_ttm: float            # capital expenditure, TTM (positive magnitude)
    sbc_ttm: float              # stock-based compensation, TTM
    diluted_shares_now: float   # current diluted share count
    diluted_shares_prior: float  # diluted share count one year earlier
    market_cap: float
    forward_pe: float | None    # None when GAAP-unprofitable / no estimate
    forward_eps_growth: float   # forward EPS growth, in %
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
) -> tuple[float, float, float, float]:
    """Return ``(growth, adj_fcf, adj_margin, rule40)`` for one period.

    The single source of truth for the per-period math, shared by the live
    snapshot in :func:`evaluate` and the historical years in the consistency
    gate so there is zero methodology drift between them.
    """
    growth = pct(revenue - revenue_prior, revenue_prior)
    adj_fcf = ocf - capex - sbc
    adj_margin = pct(adj_fcf, revenue)
    rule40 = growth + adj_margin
    return growth, adj_fcf, adj_margin, rule40


def evaluate(c: Company, consistency_years: int = 0,
             consistency_mode: str = "strict") -> Result:
    """Apply the screen to one company and return a fully populated Result.

    Order of operations: a **pre-revenue firewall** fires first (no metric math
    on a name with no revenue); then the snapshot four-filter / two-track routing
    (UNCHANGED); then — only for a snapshot qualifier — the current-snapshot
    balance-sheet gates and the multi-year consistency gate.

    ``consistency_years`` of 0 (the default) bypasses the historical gate while
    leaving the pre-revenue and balance-sheet gates active. ``consistency_mode``
    selects the Track-A durability rule (``"strict"`` = Rule of 40 in every year,
    the default for library callers; ``"trend"`` = trajectory-aware). Track B is
    unaffected by the mode.
    """
    # 1. Pre-revenue firewall — the FIRST check, before any metric math.
    if c.revenue_ttm <= 0:
        return _pre_revenue_result(c)

    # 2. Snapshot metrics + balance-sheet derived metrics.
    growth, adj_fcf, adj_margin, rule40 = _period_metrics(
        c.revenue_ttm, c.revenue_ttm_prior, c.ocf_ttm, c.capex_ttm, c.sbc_ttm
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

    # PEG is genuinely N/A unless there is a forward P/E AND positive growth.
    peg = (
        c.forward_pe / c.forward_eps_growth
        if (c.forward_pe is not None and c.forward_eps_growth > 0)
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
    # eligible for the PEG-exempt Track B.
    track_a = gaap_profitable and pass_sbc and pass_rule40 and pass_ps and pass_peg
    track_b = (not gaap_profitable) and pass_sbc and pass_rule40 and pass_ps and quality_gate

    # Balance-sheet gates. Leverage + goodwill apply to ALL snapshot qualifiers;
    # ROIC is gated only for a Track-A name under strict mode (advisory otherwise).
    pass_leverage = net_debt <= 0 or (fcf > 0 and net_debt_to_fcf <= NET_DEBT_TO_FCF_MAX)
    pass_goodwill = c.total_assets > 0 and goodwill_to_assets <= GOODWILL_TO_ASSETS_MAX
    pass_roic = roic is None or roic >= ROIC_MIN
    roic_applied = track_a and consistency_mode == "strict"

    consistency: ConsistencyResult | None = None

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
        )
    else:
        # A snapshot track cleared — layer the balance-sheet and consistency gates.
        track = "Track A" if track_a else "Track B"
        bs_reasons = _balance_sheet_reasons(
            pass_leverage, pass_goodwill, roic_applied, pass_roic,
            net_debt, fcf, net_debt_to_fcf, goodwill_to_assets, roic,
        )
        if consistency_years > 0:
            consistency = (
                track_a_consistency(c.history, consistency_years, consistency_mode)
                if track_a else track_b_consistency(c.history, consistency_years)
            )

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
        status=status, track_a=track_a, track_b=track_b,
        track=track, reasons=reasons, consistency=consistency,
    )


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
        status=ScreenStatus.PRE_REVENUE, track_a=False, track_b=False,
        track="—", reasons=["pre-revenue (revenue_ttm <= 0)"], consistency=None,
    )


def _balance_sheet_reasons(
    pass_leverage: bool, pass_goodwill: bool, roic_applied: bool, pass_roic: bool,
    net_debt: float, fcf: float, net_debt_to_fcf: float,
    goodwill_to_assets: float, roic: float | None,
) -> list[str]:
    """All failing balance-sheet-gate reasons for a snapshot qualifier (no short-circuit).

    ROIC contributes a reason only when it was an active gate (Track A + strict)
    AND a value was computable; a not-computable ROIC is advisory, never a fail.
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
    return reasons


def _rejection_reasons(
    gaap_profitable: bool, pass_sbc: bool, pass_rule40: bool, pass_ps: bool,
    quality_gate: bool, sbc_pct: float, rule40: float, p_s: float,
    growth: float, peg: float | None,
) -> list[str]:
    """Build a human-readable list of why a name was rejected.

    Branches on GAAP profitability — the routing key — not on whether PEG is
    computable: a profitable name lives or dies on Track A (PEG required), an
    unprofitable one on the Track B quality gate.
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
        if peg is None:
            reasons.append(
                "PEG not computable (no forward P/E or forward EPS growth <= 0); "
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
