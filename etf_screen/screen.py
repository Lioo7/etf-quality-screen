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
from statistics import median

# --- thresholds (the validated spec) ---------------------------------------
RULE40_MIN = 40.0       # revenue growth% + adjusted FCF margin%
PEG_MAX = 2.0           # Track A only
SBC_MAX_PCT = 15.0      # SBC as % of revenue
PS_GUARDRAIL_FACTOR = 0.5  # P/S must be <= factor * revenue growth%

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

    # per-filter booleans
    pass_sbc: bool
    pass_rule40: bool
    pass_ps: bool
    pass_peg: bool
    quality_gate: bool

    # verdict
    track_a: bool
    track_b: bool
    passed: bool
    track: str               # "Track A", "Track B", or "—"
    reasons: list[str]       # rejection reasons (empty if passed)
    evaluable: bool          # False when a divide-by-zero guard tripped

    # Multi-year consistency gate (None when the gate did not run). ``insufficient
    # history`` is True ONLY for a snapshot qualifier demoted for lacking history —
    # a snapshot-failing name is a normal rejection and never lands in that bucket.
    consistency: "ConsistencyResult | None" = None
    insufficient_history: bool = False

    # Informational sector annotation, attached post-hoc by
    # :func:`annotate_sector_context`. NEVER read by :func:`evaluate` or the
    # default ranking — it cannot change passed / track / rank.
    sector_context: "SectorContext | None" = None

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


def evaluate(c: Company, consistency_years: int = 0) -> Result:
    """Apply the screen to one company and return a fully populated Result.

    Mirrors the authoritative reference implementation exactly. When
    ``consistency_years`` is 0 (the default) the multi-year gate is bypassed and
    behaviour is identical to the snapshot-only screen; a positive value layers
    the track-aware annual durability gate on top of a snapshot qualifier.
    """
    growth, adj_fcf, adj_margin, rule40 = _period_metrics(
        c.revenue_ttm, c.revenue_ttm_prior, c.ocf_ttm, c.capex_ttm, c.sbc_ttm
    )
    fcf = c.ocf_ttm - c.capex_ttm
    p_s = c.market_cap / c.revenue_ttm if c.revenue_ttm else nan
    sbc_pct = pct(c.sbc_ttm, c.revenue_ttm)
    dilution = pct(c.diluted_shares_now - c.diluted_shares_prior, c.diluted_shares_prior)

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

    passed = evaluable and (track_a or track_b)

    if not evaluable:
        track = "—"
        reasons = ["could not evaluate (missing/zero denominator)"]
    elif track_a:
        track = "Track A"
        reasons = []
    elif track_b:
        track = "Track B"
        reasons = []
    else:
        track = "—"
        reasons = _rejection_reasons(
            gaap_profitable, pass_sbc, pass_rule40, pass_ps, quality_gate,
            sbc_pct, rule40, p_s, growth, peg,
        )

    # Multi-year consistency gate — layered ONLY on a snapshot qualifier. A name
    # that already failed the snapshot is a normal rejection; its history is moot.
    consistency: ConsistencyResult | None = None
    insufficient_history = False
    if consistency_years > 0 and passed:
        consistency = (
            track_a_consistency(c.history, consistency_years) if track_a
            else track_b_consistency(c.history, consistency_years)
        )
        if consistency.status is ConsistencyStatus.FAIL:
            passed = False
            track = "—"
            reasons = [_consistency_fail_reason(consistency, track_a, consistency_years)]
        elif consistency.status is ConsistencyStatus.INSUFFICIENT:
            passed = False
            track = "—"
            insufficient_history = True
            reasons = [
                f"insufficient annual history "
                f"({consistency.years_available} of {consistency.years_required} yrs)"
            ]

    return Result(
        company=c,
        growth=growth, fcf=fcf, adj_fcf=adj_fcf, adj_margin=adj_margin,
        rule40=rule40, p_s=p_s, sbc_pct=sbc_pct, dilution=dilution, peg=peg,
        pass_sbc=pass_sbc, pass_rule40=pass_rule40, pass_ps=pass_ps,
        pass_peg=pass_peg, quality_gate=quality_gate,
        track_a=track_a, track_b=track_b, passed=passed,
        track=track, reasons=reasons, evaluable=evaluable,
        consistency=consistency, insufficient_history=insufficient_history,
    )


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


def track_a_consistency(history: list[AnnualPeriod], years: int) -> ConsistencyResult:
    """Track A durability: Rule of 40 >= threshold in EVERY year of the window.

    A windowed year's Rule of 40 needs its prior year's revenue, so ``years``
    rule40 values require ``years + 1`` contiguous complete annual periods.
    Too few -> INSUFFICIENT. A NaN in the window is a data gap (not a genuine
    fail) -> INSUFFICIENT. Otherwise all >= :data:`RULE40_MIN` -> PASS else FAIL.
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
    status = (
        ConsistencyStatus.PASS
        if all(v >= RULE40_MIN for _, v in rule40_by_year)
        else ConsistencyStatus.FAIL
    )
    return ConsistencyResult(
        status, required, len(history), rule40_by_year=rule40_by_year
    )


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
