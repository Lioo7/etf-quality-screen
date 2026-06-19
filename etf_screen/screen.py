"""Core screening logic — no network access lives here.

This module defines the :class:`Company` input record, the :func:`evaluate`
function that applies the validated four-filter / two-track methodology, and
helpers for ranking and rendering results.

The thresholds and routing here are a *fixed, already-validated spec* — see the
acceptance tests in ``tests/test_screen.py``. In particular, Track B is reachable
**only** when PEG is genuinely N/A (a GAAP-unprofitable name with no forward
P/E); a profitable name that fails the PEG filter must be rejected outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

    # Optional provenance / confidence annotations from the data layer.
    low_confidence: list[str] = field(default_factory=list)
    name: str = ""              # human-readable company name (defaults to ticker)
    basis: str = ""             # accounting basis used: "TTM", "annual", or ""
    sbc_assumed_zero: bool = False  # SBC line absent from source -> assumed 0
    overridden_fields: list[str] = field(default_factory=list)  # from overrides.json
    sector: str = "Unknown"     # GICS-style sector bucket from the data layer
    industry: str = ""          # finer sub-sector (optional)

    def __post_init__(self) -> None:
        # Coerce Nones (e.g. from older cache entries) and default name to ticker.
        if self.low_confidence is None:
            self.low_confidence = []
        if self.overridden_fields is None:
            self.overridden_fields = []
        if not self.name:
            self.name = self.ticker

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


def evaluate(c: Company) -> Result:
    """Apply the screen to one company and return a fully populated Result.

    Mirrors the authoritative reference implementation exactly.
    """
    growth = pct(c.revenue_ttm - c.revenue_ttm_prior, c.revenue_ttm_prior)
    fcf = c.ocf_ttm - c.capex_ttm
    adj_fcf = fcf - c.sbc_ttm
    adj_margin = pct(adj_fcf, c.revenue_ttm)
    rule40 = growth + adj_margin
    p_s = c.market_cap / c.revenue_ttm if c.revenue_ttm else nan
    sbc_pct = pct(c.sbc_ttm, c.revenue_ttm)
    dilution = pct(c.diluted_shares_now - c.diluted_shares_prior, c.diluted_shares_prior)

    # PEG is genuinely N/A unless there is a forward P/E AND positive growth.
    peg = (
        c.forward_pe / c.forward_eps_growth
        if (c.forward_pe is not None and c.forward_eps_growth > 0)
        else None
    )

    # If any input that gates a filter is NaN, the name can't be evaluated.
    evaluable = not any(isnan(x) for x in (growth, adj_margin, p_s, sbc_pct, dilution))

    pass_sbc = sbc_pct <= SBC_MAX_PCT
    pass_rule40 = rule40 >= RULE40_MIN
    pass_ps = p_s <= PS_GUARDRAIL_FACTOR * growth
    pass_peg = peg is not None and peg <= PEG_MAX
    quality_gate = (adj_fcf > 0) and (dilution < growth)

    track_a = pass_sbc and pass_rule40 and pass_ps and pass_peg
    # CRITICAL: Track B only when PEG is genuinely N/A. A profitable name that
    # fails PEG must NOT slip into Track B.
    track_b = (peg is None) and pass_sbc and pass_rule40 and pass_ps and quality_gate

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
            pass_sbc, pass_rule40, pass_ps, pass_peg, quality_gate,
            sbc_pct, rule40, p_s, growth, peg,
        )

    return Result(
        company=c,
        growth=growth, fcf=fcf, adj_fcf=adj_fcf, adj_margin=adj_margin,
        rule40=rule40, p_s=p_s, sbc_pct=sbc_pct, dilution=dilution, peg=peg,
        pass_sbc=pass_sbc, pass_rule40=pass_rule40, pass_ps=pass_ps,
        pass_peg=pass_peg, quality_gate=quality_gate,
        track_a=track_a, track_b=track_b, passed=passed,
        track=track, reasons=reasons, evaluable=evaluable,
    )


def _rejection_reasons(
    pass_sbc: bool, pass_rule40: bool, pass_ps: bool, pass_peg: bool,
    quality_gate: bool, sbc_pct: float, rule40: float, p_s: float,
    growth: float, peg: float | None,
) -> list[str]:
    """Build a human-readable list of why a name was rejected."""
    reasons: list[str] = []
    if not pass_rule40:
        reasons.append(f"Rule of 40 = {rule40:.1f} (< {RULE40_MIN:.0f})")
    if not pass_ps:
        reasons.append(
            f"P/S = {p_s:.1f} (> guardrail {PS_GUARDRAIL_FACTOR * growth:.1f})"
        )
    if not pass_sbc:
        reasons.append(f"SBC = {sbc_pct:.1f}% of revenue (> {SBC_MAX_PCT:.0f}%)")
    if peg is None:
        # PEG N/A -> Track A impossible; rejection then comes from quality gate.
        if not quality_gate:
            reasons.append("fails Track B quality gate (adj FCF<=0 or dilution>=growth)")
    elif not pass_peg:
        reasons.append(f"PEG = {peg:.1f} (> {PEG_MAX:.1f}); profitable, so Track B is barred")
    return reasons or ["did not clear either track"]


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
