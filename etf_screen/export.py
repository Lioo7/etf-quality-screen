"""Export the full screen result set to CSV or Markdown.

Writes *every* name — survivors, rejected, and skipped (could-not-source) — with
all metrics, the track label, the rejection/skip reason, and the
confidence/basis flags. A provenance header is included as CSV comment lines or a
Markdown header block so an exported file is self-describing.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from math import isnan
from pathlib import Path

from tabulate import tabulate

from .screen import Result

# Column order shared by both formats.
COLUMNS = [
    "ticker", "name", "sector", "track", "status", "passed", "growth_pct",
    "adj_fcf_margin_pct", "rule40", "p_s", "ps_guardrail", "dilution_pct",
    "sbc_pct", "peg", "net_debt_to_fcf", "goodwill_to_assets", "roic",
    "roic_median", "roic_years", "roic_durable",
    "earnings_quality_ratio", "earnings_quality_years", "earnings_quality_applicable",
    "eq_capex_intensity_pct",
    "gm_latest", "gm_stddev", "gm_years", "pricing_power", "gm_not_applicable",
    "pct_off_ath", "three_month_return", "hot", "price_history_years",
    "unlock_ps_price", "unlock_peg_price",
    "net_income_ttm", "rule40_hist", "consistency", "ipo_date",
    "sector_med_rule40", "sector_med_ps", "sector_med_peg",
    "basis", "sbc_assumed_zero", "manual_override", "reason", "low_confidence",
]


@dataclass
class Provenance:
    provider: str
    universe: str
    source: str
    as_of: str
    run_date: str


# Default directory for exports (git-ignored, regenerable point-in-time
# snapshots); overridden by --out. Committed curated snapshots live in examples/.
DEFAULT_EXPORT_DIR = Path("results")


def default_filename(universe: str, run_date: str, fmt: str) -> str:
    return f"etf_screen_{universe}_{run_date}.{fmt}"


def _num(x, nd: int = 2) -> str:
    if x is None:
        return "N/A"
    try:
        return "N/A" if isnan(x) else f"{round(x, nd)}"
    except TypeError:
        return str(x)


def _sector_med(r: Result, metric: str) -> str:
    """The result's sector median for ``metric`` (blank when no context)."""
    ctx = r.sector_context
    if ctx is None or not ctx.available or metric not in ctx.medians:
        return "N/A"
    return _num(ctx.medians[metric])


def rule40_hist(r: Result) -> str:
    """The Rule-of-40 trajectory ``[55.1 -> 42.0 -> 40.5]`` (oldest -> newest).

    Blank when the consistency gate did not run or yielded no windowed years.
    """
    if r.consistency is None or not r.consistency.rule40_by_year:
        return ""
    return "[" + " -> ".join(f"{v:.1f}" for _, v in r.consistency.rule40_by_year) + "]"


def consistency_status(r: Result) -> str:
    """PASS / FAIL / INSUFFICIENT, or ``—`` when the gate did not run."""
    return r.consistency.status.name if r.consistency is not None else "—"


def _roic_median(r: Result) -> str:
    if r.roic_history is None or r.roic_history.median is None:
        return "N/A"
    return _num(r.roic_history.median * 100)


def _roic_years(r: Result):
    return r.roic_history.years_used if r.roic_history else ""


def _roic_durable(r: Result):
    return r.roic_history.durable if r.roic_history else False


def _gm_field(r: Result, attr: str, nd: int = 1):
    mp = r.margin_profile
    if mp is None or mp.not_applicable:
        return "N/A"
    val = getattr(mp, attr)
    return "N/A" if val is None else _num(val, nd)


def _result_row(r: Result) -> dict:
    return {
        "ticker": r.ticker, "name": r.name, "sector": r.sector, "track": r.track,
        "status": r.status.name, "passed": r.passed, "growth_pct": _num(r.growth),
        "adj_fcf_margin_pct": _num(r.adj_margin), "rule40": _num(r.rule40),
        "p_s": _num(r.p_s), "ps_guardrail": _num(r.ps_guardrail),
        "dilution_pct": _num(r.dilution), "sbc_pct": _num(r.sbc_pct),
        "peg": _num(r.peg), "net_debt_to_fcf": _num(r.net_debt_to_fcf),
        "goodwill_to_assets": _num(r.goodwill_to_assets), "roic": _num(r.roic),
        "roic_median": _roic_median(r), "roic_years": _roic_years(r),
        "roic_durable": _roic_durable(r),
        "earnings_quality_ratio": _num(r.earnings_quality_ratio, 2),
        "earnings_quality_years": r.earnings_quality_years,
        "earnings_quality_applicable": r.earnings_quality_applicable,
        "eq_capex_intensity_pct": _num(r.eq_capex_intensity),
        "gm_latest": _gm_field(r, "gm_latest"), "gm_stddev": _gm_field(r, "gm_stddev"),
        "gm_years": _gm_field(r, "years_used", 0),
        "pricing_power": bool(r.margin_profile and r.margin_profile.pricing_power),
        "gm_not_applicable": bool(r.margin_profile and r.margin_profile.not_applicable),
        "pct_off_ath": _num(r.pct_off_ath), "three_month_return": _num(r.three_month_return),
        "hot": r.hot, "price_history_years": _num(r.company.price_history_years, 1),
        "unlock_ps_price": _num(r.unlock_ps_price),
        "unlock_peg_price": _num(r.unlock_peg_price),
        "net_income_ttm": _num(r.company.net_income_ttm),
        "rule40_hist": rule40_hist(r), "consistency": consistency_status(r),
        "ipo_date": r.company.ipo_date or "unknown",
        "sector_med_rule40": _sector_med(r, "rule40"),
        "sector_med_ps": _sector_med(r, "p_s"), "sector_med_peg": _sector_med(r, "peg"),
        "basis": r.basis,
        "sbc_assumed_zero": r.sbc_assumed_zero, "manual_override": r.manual_override,
        "reason": "; ".join(r.reasons), "low_confidence": "; ".join(r.company.low_confidence),
    }


def _skipped_row(ticker: str, name: str, reason: str) -> dict:
    row = {c: "" for c in COLUMNS}
    row.update({
        "ticker": ticker, "name": name, "track": "—", "passed": False,
        "sbc_assumed_zero": False, "manual_override": False, "reason": reason,
    })
    return row


def build_rows(results: list[Result], skipped, names: dict[str, str]) -> list[dict]:
    """Survivors, watchlist, needs-data, then the rest (by Rule of 40), then skipped."""
    ordered = sorted(results,
                     key=lambda r: (not r.passed, not r.watchlist, not r.needs_data,
                                    -_safe(r.rule40)))
    rows = [_result_row(r) for r in ordered]
    rows += [_skipped_row(s.ticker, names.get(s.ticker, s.ticker), s.reason)
             for s in skipped]
    return rows


def _safe(x: float) -> float:
    try:
        return -1e18 if isnan(x) else x
    except TypeError:
        return -1e18


def export(path: Path, fmt: str, prov: Provenance, rows: list[dict]) -> None:
    if fmt == "csv":
        _write_csv(path, prov, rows)
    elif fmt == "md":
        _write_md(path, prov, rows)
    else:  # pragma: no cover - guarded by argparse choices
        raise ValueError(f"unknown export format: {fmt}")


def _prov_lines(prov: Provenance) -> list[str]:
    return [
        "etf-quality-screen export",
        f"provider: {prov.provider}",
        f"universe: {prov.universe}",
        f"constituents source: {prov.source}",
        f"data as-of: {prov.as_of}",
        f"run date: {prov.run_date}",
    ]


def _write_csv(path: Path, prov: Provenance, rows: list[dict]) -> None:
    with path.open("w", newline="") as fh:
        for line in _prov_lines(prov):
            fh.write(f"# {line}\n")
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_md(path: Path, prov: Provenance, rows: list[dict]) -> None:
    table = tabulate(
        [[row[c] for c in COLUMNS] for row in rows],
        headers=COLUMNS, tablefmt="github",
    )
    lines = ["# ETF Quality-Screen Export", ""]
    lines += [f"- **{ln.split(': ', 1)[0]}**: {ln.split(': ', 1)[1]}"
              if ": " in ln else f"_{ln}_" for ln in _prov_lines(prov)]
    lines += ["", table, ""]
    path.write_text("\n".join(lines))
