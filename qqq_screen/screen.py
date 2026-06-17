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

# --- thresholds (the validated spec) ---------------------------------------
RULE40_MIN = 40.0       # revenue growth% + adjusted FCF margin%
PEG_MAX = 2.0           # Track A only
SBC_MAX_PCT = 15.0      # SBC as % of revenue
PS_GUARDRAIL_FACTOR = 0.5  # P/S must be <= factor * revenue growth%


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

    @property
    def ticker(self) -> str:
        return self.company.ticker

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


def rank(results: list[Result]) -> list[Result]:
    """Rank survivors: Rule of 40 desc, then PEG asc (None last), then P/S headroom desc."""
    def key(r: Result):
        peg_sort = r.peg if r.peg is not None else float("inf")
        return (-r.rule40, peg_sort, -r.ps_headroom)

    return sorted([r for r in results if r.passed], key=key)
