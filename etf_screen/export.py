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
    "ticker", "name", "track", "passed", "growth_pct", "adj_fcf_margin_pct",
    "rule40", "p_s", "ps_guardrail", "dilution_pct", "sbc_pct", "peg",
    "basis", "sbc_assumed_zero", "manual_override", "reason", "low_confidence",
]


@dataclass
class Provenance:
    provider: str
    universe: str
    source: str
    as_of: str
    run_date: str


def default_filename(universe: str, run_date: str, fmt: str) -> str:
    return f"etf_screen_{universe}_{run_date}.{fmt}"


def _num(x, nd: int = 2) -> str:
    if x is None:
        return "N/A"
    try:
        return "N/A" if isnan(x) else f"{round(x, nd)}"
    except TypeError:
        return str(x)


def _result_row(r: Result) -> dict:
    return {
        "ticker": r.ticker, "name": r.name, "track": r.track,
        "passed": r.passed, "growth_pct": _num(r.growth),
        "adj_fcf_margin_pct": _num(r.adj_margin), "rule40": _num(r.rule40),
        "p_s": _num(r.p_s), "ps_guardrail": _num(r.ps_guardrail),
        "dilution_pct": _num(r.dilution), "sbc_pct": _num(r.sbc_pct),
        "peg": _num(r.peg), "basis": r.basis,
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
    """Survivors+rejected first (passed first, then by Rule of 40), then skipped."""
    ordered = sorted(results, key=lambda r: (not r.passed, -_safe(r.rule40)))
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
