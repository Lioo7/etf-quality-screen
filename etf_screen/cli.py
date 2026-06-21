"""Command-line entrypoint: ``python -m etf_screen.cli``.

Resolves a universe (or an explicit ticker list), fetches fundamentals through
the chosen provider (with optional manual overrides), runs the screen, and prints
a provenance header, Phase-1 elimination counts, a ranked survivor table,
per-name reasoning, an "SBC assumed 0" review section, and a "could not source"
section. Optionally exports the full result set to CSV/Markdown. Data is never
fabricated: unsourced names are reported, not guessed.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date
from math import isnan
from pathlib import Path

from tabulate import tabulate

from . import __version__, export as export_mod
from .cache import DiskCache
from .constituents import holdings_from_csv, resolve
from .overrides import apply_override, company_from_override, load_overrides
from .providers import PROVIDERS, DataProvider, DataUnavailable
from .screen import Result, annotate_sector_context, evaluate, rank


@dataclass
class Skipped:
    ticker: str
    reason: str


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m etf_screen.cli",
        description="Quality-growth screener for ETF constituents.",
    )
    p.add_argument("--provider", choices=sorted(PROVIDERS), default="yfinance",
                   help="data source (default: yfinance, no API key needed)")
    p.add_argument("--universe", default="qqq",
                   help="ETF whose holdings to screen, e.g. qqq or spy (default: qqq)")
    p.add_argument("--holdings", default=None,
                   help="path to an issuer holdings CSV (any ETF: ARK, iShares, "
                        "SSGA, ...); overrides --universe")
    p.add_argument("--tickers",
                   help="comma-separated tickers to screen instead of a universe")
    p.add_argument("--limit", type=int, default=None,
                   help="screen at most N tickers (useful for cheap validation)")
    p.add_argument("--refresh", action="store_true",
                   help="ignore cached data and refetch")
    p.add_argument("--no-cache", action="store_true",
                   help="disable the per-day disk cache entirely")
    p.add_argument("--throttle", type=float, default=0.0,
                   help="seconds to sleep between fetches (rate-limit courtesy)")
    p.add_argument("--overrides", default="overrides.json",
                   help="path to a git-ignored overrides.json (default: overrides.json)")
    p.add_argument("--export", choices=("csv", "md"), default=None,
                   help="also write the full result set to a file")
    p.add_argument("--out", default=None,
                   help="export path (default: etf_screen_<universe>_<date>.<ext>)")
    p.add_argument("--sector-context", action="store_true",
                   help="show the verbose sector-context view (informational; the "
                        "Sector column and export fields are always present)")
    p.add_argument("--rank", choices=("rule40", "sector-relative"), default="rule40",
                   help="survivor ordering: absolute Rule of 40 (default) or "
                        "within-sector attractiveness (informational re-sort only)")
    p.add_argument("--consistency-years", type=int, default=3,
                   help="multi-year consistency gate window (default: 3); 0 bypasses "
                        "the gate entirely for a snapshot-only baseline run")
    return p


def _resolve_universe(args) -> tuple[list[str], str, str, bool, dict[str, str], str]:
    """Return (tickers, source, as_of, is_stale, names, label) for the universe.

    Precedence: explicit ``--tickers`` > a ``--holdings`` CSV > a named ``--universe``.
    """
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        return tickers, "explicit --tickers", date.today().isoformat(), False, {}, "tickers"
    if args.holdings:
        h = holdings_from_csv(args.holdings)
        return h.tickers, h.source, h.as_of, h.is_stale, h.names, h.etf.lower()
    h = resolve(args.universe)
    return h.tickers, h.source, h.as_of, h.is_stale, h.names, args.universe


def _make_provider(args) -> DataProvider:
    """Build the chosen provider, exiting cleanly if it can't be constructed."""
    cache = None if args.no_cache else DiskCache(
        args.provider, enabled=True, refresh=args.refresh)
    try:
        return PROVIDERS[args.provider](cache=cache)
    except DataUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _screen_one(provider, ticker, override, consistency_years=0) -> Result | Skipped:
    """Fetch + (optionally override) + evaluate one ticker, or report it skipped."""
    try:
        company = provider.fetch(ticker)
        if override:
            company = apply_override(company, override)
        return evaluate(company, consistency_years=consistency_years)
    except DataUnavailable as exc:
        if override:  # provider had nothing, but the user supplied verified data
            rescued = company_from_override(ticker, override)
            if rescued is not None:
                # An override-rescued name carries no annual history, so the gate
                # will route it to INSUFFICIENT_HISTORY when enabled.
                return evaluate(rescued, consistency_years=consistency_years)
        return Skipped(ticker, str(exc))
    except Exception as exc:  # unexpected provider/parse error -> report, skip
        return Skipped(ticker, f"unexpected error: {exc}")


def run(args) -> int:
    try:
        tickers, source, as_of, stale, names, label = _resolve_universe(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.limit is not None:
        tickers = tickers[: args.limit]
    provider = _make_provider(args)
    overrides = load_overrides(args.overrides)

    results: list[Result] = []
    skipped: list[Skipped] = []
    for i, ticker in enumerate(tickers):
        outcome = _screen_one(provider, ticker, overrides.get(ticker),
                              consistency_years=args.consistency_years)
        (skipped if isinstance(outcome, Skipped) else results).append(outcome)
        if args.throttle and i < len(tickers) - 1:
            time.sleep(args.throttle)

    # Informational only — attaches sector context; never alters any verdict.
    sector_stats = annotate_sector_context(results)

    _report(args, source, as_of, stale, tickers, results, skipped, names,
            sector_stats, label)

    if args.export:
        _do_export(args, source, as_of, results, skipped, names, label)
    return 0


def _do_export(args, source, as_of, results, skipped, names, label) -> None:
    universe = label
    run_date = date.today().isoformat()
    path = Path(args.out) if args.out else Path(
        export_mod.default_filename(universe, run_date, args.export))
    prov = export_mod.Provenance(args.provider, universe, source, as_of, run_date)
    rows = export_mod.build_rows(results, skipped, names)
    export_mod.export(path, args.export, prov, rows)
    print(f"\nExported {len(rows)} rows to {path}")


def _report(args, source, as_of, stale, tickers, results, skipped, names,
            sector_stats, label) -> None:
    survivors = rank(results, mode=args.rank)
    # INSUFFICIENT_HISTORY names get their own section; keep them out of the
    # ordinary rejection list (a failed-consistency name stays a normal reject).
    insufficient = [r for r in results if r.insufficient_history]
    rejected = [r for r in results if not r.passed and not r.insufficient_history]
    sbc_assumed = [r for r in results if r.sbc_assumed_zero]
    gated_out = sum(1 for r in rejected
                    if any("consistency gate" in reason for reason in r.reasons))

    # --- provenance header ---
    print("=" * 78)
    print(f"etf-quality-screen v{__version__}  |  run {date.today().isoformat()}")
    print(f"Provider : {args.provider}")
    print(f"Universe : {'explicit' if args.tickers else label} "
          f"({len(tickers)} tickers)")
    print(f"Source   : {source}{'  [STALE]' if stale else ''}")
    print(f"As-of    : {as_of}")
    print("=" * 78)

    # --- Phase-1 elimination counts ---
    print("\nPhase-1 elimination counts")
    print(f"  screened        : {len(tickers)}")
    print(f"  evaluated       : {len(results)}")
    print(f"  could not source: {len(skipped)}")
    print(f"  passed          : {len(survivors)}")
    print(f"  rejected        : {len(rejected)}")
    if args.consistency_years:
        print(f"  consistency gate: {gated_out} demoted, "
              f"{len(insufficient)} insufficient history "
              f"({args.consistency_years}-yr window)")
    print(f"  SBC assumed 0   : {len(sbc_assumed)} (review below)")
    print("  rejected by filter (names may fail more than one):")
    print(f"    Rule of 40    : {sum(1 for r in rejected if not r.pass_rule40)}")
    print(f"    P/S guardrail : {sum(1 for r in rejected if not r.pass_ps)}")
    print(f"    SBC > 15%     : {sum(1 for r in rejected if not r.pass_sbc)}")
    print(f"    PEG (Track A) : {sum(1 for r in rejected if r.peg is not None and not r.pass_peg)}")

    # --- ranked survivor table ---
    rank_label = ("Rule of 40" if args.rank == "rule40"
                  else "within-sector attractiveness")
    print(f"\nShortlist ({len(survivors)} names) — ranked by {rank_label}\n")
    if survivors:
        rows = [[
            i + 1, r.ticker, _short(r.name), _short(r.sector, 20), r.track,
            _f(r.growth), _f(r.adj_margin), _f(r.rule40), _f(r.p_s, 1),
            _f(r.dilution), _f(r.sbc_pct), _peg(r.peg), export_mod.rule40_hist(r),
        ] for i, r in enumerate(survivors)]
        headers = ["#", "Ticker", "Company", "Sector", "Track", "Growth%",
                   "AdjFCF%", "Rule40", "P/S", "Dil%", "SBC%", "PEG", "Rule40 Hist"]
        print(tabulate(rows, headers=headers, floatfmt=".1f"))
    else:
        print("  (empty — a strict screen producing no survivors is a valid result)")

    # --- sector context (informational; gated behind --sector-context) ---
    if args.sector_context:
        _report_sector_context(survivors, results, sector_stats)

    # --- per-name reasoning ---
    if survivors:
        print("\nWhy survivors passed:")
        for r in survivors:
            print(f"  {r.ticker} ({_short(r.name)}): {r.track} "
                  f"(Rule40 {_f(r.rule40)}, P/S {_f(r.p_s, 1)} vs guardrail "
                  f"{_f(r.ps_guardrail, 1)}, SBC {_f(r.sbc_pct)}%"
                  f"{', PEG ' + _peg(r.peg) if r.peg else ''}){_flags(r)}")

    if rejected:
        print("\nWhy names were rejected:")
        for r in rejected:
            print(f"  {r.ticker} ({_short(r.name)}): {'; '.join(r.reasons)}{_flags(r)}")

    # --- SBC assumed 0 review section ---
    if sbc_assumed:
        print(f"\nSBC assumed 0 ({len(sbc_assumed)}) — source reported no SBC line; "
              f"VERIFY these before trusting their verdict:")
        for r in sbc_assumed:
            verdict = r.track if r.passed else "rejected"
            print(f"  {r.ticker} ({_short(r.name)}): {verdict} "
                  f"(Rule40 {_f(r.rule40)}, SBC% shown as {_f(r.sbc_pct)})")

    # --- insufficient history (cleared the snapshot, lacked annual history) ---
    if insufficient:
        print(f"\nINSUFFICIENT_HISTORY ({len(insufficient)}) — cleared the snapshot "
              f"but lacked a long enough annual record for the consistency gate:")
        for r in insufficient:
            c = r.consistency
            print(f"  {r.ticker} ({_short(r.name)}): "
                  f"{c.years_available} of {c.years_required} yrs available")

    # --- could not source ---
    if skipped:
        print(f"\nCould not source ({len(skipped)}) — skipped, never fabricated:")
        for s in skipped:
            print(f"  {s.ticker} ({_short(names.get(s.ticker, s.ticker))}): {s.reason}")


def _report_sector_context(survivors, results, sector_stats) -> None:
    """Print the like-with-like sector view: survivors vs. their sector medians,
    plus a per-sector summary. Purely informational — never a gate."""
    print("\nSector context (informational — compares like-with-like; never gates "
          "a verdict)")

    if survivors:
        print("\n  Survivors vs. their sector median:")
        for r in survivors:
            ctx = r.sector_context
            if ctx and ctx.available:
                print(f"    {r.ticker} ({_short(r.sector, 22)}): "
                      f"{_vs(r.p_s, ctx, 'p_s', 'P/S', 1)}, "
                      f"{_vs(r.peg, ctx, 'peg', 'PEG', 2)}, "
                      f"{_vs(r.rule40, ctx, 'rule40', 'Rule40', 1)}")
            else:
                note = ctx.note if ctx else "n/a"
                print(f"    {r.ticker} ({_short(r.sector, 22)}): {note}")

    # Per-sector summary block (#names, median Rule40 / P/S / PEG).
    if sector_stats:
        print("\n  Per-sector medians (sectors with >= 5 evaluable peers):")
        rows = []
        for sec in sorted(sector_stats):
            st = sector_stats[sec]
            rows.append([_short(sec, 26), st.n, _med(st, "rule40"),
                         _med(st, "p_s", 1), _med(st, "peg", 2)])
        print(tabulate(rows, headers=["Sector", "#", "Rule40", "P/S", "PEG"],
                       floatfmt=".1f", tablefmt="simple"))
    else:
        print("\n  Per-sector medians: none — no sector reached 5 evaluable peers.")

    # Note sparse coverage (expected for small universes like ARKK).
    sparse = sorted({r.sector for r in results
                     if r.sector_context and not r.sector_context.available
                     and r.sector != "Unknown"})
    if sparse:
        print(f"\n  Note: {len(sparse)} sector(s) had too few peers (<5) for a "
              f"median — expected for small universes: {', '.join(sparse)}.")


def _vs(value, ctx, metric, label, nd: int = 1) -> str:
    """Render `Label V (sector med M)` for one metric against its sector median."""
    med = ctx.medians.get(metric)
    val = _peg(value) if metric == "peg" else _f(value, nd)
    med_s = _peg(med) if metric == "peg" else _f(med, nd)
    return f"{label} {val} (sector med {med_s})"


def _med(st, metric: str, nd: int = 1) -> str:
    med = st.medians.get(metric)
    return "N/A" if med is None else f"{med:.{nd}f}"


# --- small formatting helpers ---
def _f(x: float, nd: int = 1) -> str:
    return "N/A" if x is None or isnan(x) else f"{x:.{nd}f}"


def _peg(peg) -> str:
    return "N/A" if peg is None else f"{peg:.2f}"


def _short(s: str, n: int = 24) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _flags(r: Result) -> str:
    """Render basis / override / low-confidence annotations for a result line."""
    tags = []
    if r.manual_override:
        tags.append("manual override: " + ", ".join(r.company.overridden_fields))
    if r.basis and r.basis != "TTM":
        tags.append(f"basis: {r.basis}")
    if r.company.low_confidence:
        tags.append("low-confidence: " + ", ".join(r.company.low_confidence))
    return f"  [{' | '.join(tags)}]" if tags else ""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
