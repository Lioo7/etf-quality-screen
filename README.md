# etf-quality-screen

A small, well-tested CLI that screens the current Invesco QQQ (Nasdaq-100)
constituents — or any ETF/ticker list — through a four-filter, two-track
quality-growth methodology, pulling live fundamentals from a **pluggable** data
provider.

> ⚠️ **Disclaimer — not investment advice.** This tool is for **educational and
> screening purposes only**. It is not investment advice or a recommendation to
> buy or sell any security. Data is pulled from free/third-party sources and may
> be **inaccurate, incomplete, or stale** (see the confidence flags). Always
> verify figures independently and consult a qualified professional before making
> any financial decision.

Screening logic and data sourcing are kept strictly separate:

| Module | Responsibility | Network? |
| --- | --- | --- |
| `etf_screen/screen.py` | `Company`, `evaluate()`, ranking | No |
| `etf_screen/providers.py` | `DataProvider` ABC + yfinance / FMP / Mock | Yes |
| `etf_screen/constituents.py` | resolve ETF holdings → tickers (Wikipedia, static, or issuer CSV) | Yes |
| `etf_screen/cache.py` | per-day disk cache | No |
| `etf_screen/cli.py` | argparse entrypoint + reporting | — |

## Methodology

![ETF Quality-Growth Screening Architecture: data inputs and derivation, the
four absolute quality gates, and the two-track routing with a valuation-trap
firewall.](docs/architecture.png)

All metrics are computed from **raw statement lines** — never pre-packaged
ratios.

- **Revenue growth** = TTM revenue vs. prior TTM, %.
- **FCF** = operating cash flow − capex. **Adjusted FCF** = FCF − stock-based
  comp (SBC is treated as a real economic cost). Margins use adjusted FCF.
- **P/S** = market cap / TTM revenue. **SBC %** = SBC / revenue.
- **Net dilution** = YoY change in diluted shares, %.
- **PEG** = forward P/E ÷ forward EPS growth %. If GAAP-unprofitable (no forward
  P/E), PEG is **N/A** — never coerced to a number.

**Four filters:** ① Rule of 40 (`growth% + adj FCF margin% ≥ 40`) · ② PEG ≤ 2.0
(Track A only) · ③ P/S guardrail (`P/S ≤ 0.5 × growth%`) · ④ SBC ≤ 15% of revenue.

**Two tracks** (a name passes if it clears *either*):

- **Track A — profitable compounders:** must pass all four filters, including PEG.
- **Track B — hyper-growth, PEG-exempt:** Rule of 40 + P/S guardrail + SBC,
  **plus** a quality gate (`adjusted FCF > 0` AND `net dilution < revenue growth`).
  Reachable **only** when PEG is genuinely N/A.

> ⚠️ **Critical rule:** Track B is for names where PEG is genuinely N/A
> (GAAP-unprofitable). A *profitable* name that fails PEG is **rejected** — it must
> not slip into Track B. The `PriceyButProfitable` acceptance test enforces this.

Survivors are ranked by Rule of 40 (desc), tie-broken by PEG (asc), then P/S
headroom.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.11+. The default provider is **yfinance**, which needs **no API
key**.

## Usage

```bash
# Default: screen the live Nasdaq-100 via yfinance (no key needed)
python -m etf_screen.cli --universe qqq

# Cheap validation against an explicit ticker list
python -m etf_screen.cli --provider yfinance --tickers MSFT,GOOGL,CRWD

# Offline / deterministic demo
python -m etf_screen.cli --provider mock --tickers MSFT,GOOGL,CRWD

# Screen the S&P 500 instead, throttling requests
python -m etf_screen.cli --universe spy --throttle 1 --limit 50
```

Flags: `--provider {yfinance,fmp,mock}` · `--universe <etf>` · `--holdings PATH`
· `--tickers a,b,c` · `--limit N` · `--refresh` (ignore cache) · `--no-cache`
· `--throttle SECONDS` · `--overrides PATH` · `--export {csv,md}` · `--out PATH`
· `--sector-context` · `--rank {rule40,sector-relative}`.

### Sector context (informational)

Absolute thresholds compare every name to the same yardstick, but a P/S that is
cheap for software is expensive for a utility. To read a name *relative to its
peers*, the screen annotates each result with its **sector median** for the key
metrics and adds a **Sector** column to the table and export.

- `--sector-context` prints a verbose "compare like-with-like" view — each
  survivor's `P/S`, `PEG`, and `Rule of 40` next to its sector median — plus a
  per-sector summary (`#names`, median Rule40 / P/S / PEG).
- A sector median is only computed when the run has **≥ 5 evaluable peers** in
  that sector; smaller sectors (and `Unknown`-sector names) are marked *"too few
  peers"* rather than guessed. Small universes (e.g. ARKK) will show many such
  sectors — that is expected, not a bug.
- `--rank sector-relative` re-orders the survivor list by within-sector
  attractiveness (Rule of 40 above its sector median, P/S below it).

> Sector context is **informational only**. It never changes a pass/fail
> verdict, track routing, or which names survive — the four filters and two
> tracks remain the sole gates. A regression test proves verdicts are identical
> with and without it.

### Exporting

`--export {csv,md}` writes the **full** result set — survivors, rejected, *and*
skipped — with every metric, the track label, the rejection/skip reason, and the
confidence/basis flags, in addition to the console output. A provenance header
(provider, source, as-of, run date) is included as CSV comment lines or a
Markdown header block. Default filename: `etf_screen_<universe>_<YYYY-MM-DD>.<ext>`
(override with `--out`).

```bash
python -m etf_screen.cli --universe qqq --export csv
python -m etf_screen.cli --tickers MSFT,GOOGL,CRWD --export md --out shortlist.md
```

### Manual overrides

When the provider is missing or wrong about a value (e.g. a real SBC figure you
looked up), supply verified numbers in a **git-ignored** `overrides.json` keyed by
ticker. Overrides take precedence over the provider, and any name they touch is
flagged `manual override`. A partial override patches individual fields; a
*complete* override can even rescue a name the provider skipped entirely.

```json
{
  "CEG": { "name": "Constellation Energy", "sbc_ttm": 120000000 }
}
```

Overridable fields: `name`, `revenue_ttm`, `revenue_ttm_prior`, `ocf_ttm`,
`capex_ttm`, `sbc_ttm`, `diluted_shares_now`, `diluted_shares_prior`,
`market_cap`, `forward_pe`, `forward_eps_growth`, `net_income_ttm`. Point
`--overrides` elsewhere to use a different file.

### Example output (mock)

```
==============================================================================
etf-quality-screen v0.1.0  |  run 2026-06-17
Provider : mock
Universe : explicit (3 tickers)
Source   : explicit --tickers
As-of    : 2026-06-17
==============================================================================

Phase-1 elimination counts
  screened        : 3
  evaluated       : 3
  could not source: 0
  passed          : 1
  rejected        : 2
  ...

Shortlist (1 names) — ranked by Rule of 40

  #  Ticker  Company                   Sector      Track    Growth%  AdjFCF%  Rule40   P/S  Dil%  SBC%  PEG
---  ------  ------------------------  ----------  -------  -------  -------  ------  ----  ----  ----  ----
  1  CRWD    CrowdStrike Holdings, I…  Technology  Track B     45.8     13.7    59.5  22.9   4.3  14.3  N/A

Why survivors passed:
  CRWD (CrowdStrike Holdings,…): Track B (Rule40 59.5, P/S 22.9 vs guardrail 22.9, SBC 14.3%)
```

## Example runs

Two real runs are checked in under [`examples/`](examples/):

- **[QQQ (Nasdaq-100)](examples/qqq-2026-06-19.md)** — the flagship full-universe
  run: 8 survivors from 100 evaluated, with dense sector context (most sectors
  clear the 5-peer bar).
- **[ARKK (ARK Innovation ETF)](examples/arkk-2026-06-19.md)** — resolved from
  ARK's official holdings CSV via `--holdings`; a small, concentrated universe
  that shows the sparse-sector case and many hyper-growth names correctly barred
  from Track B by its quality gate. The basket snapshot is committed alongside as
  [`examples/arkk-holdings.csv`](examples/arkk-holdings.csv).

Each doc carries a provenance header, the ranked shortlist, the sector-context
view, and the full result set (collapsed). They are point-in-time snapshots —
the header records exactly which data produced them.

## Data providers

### yfinance (default, free, no key)

Backed by Yahoo Finance. **Known limitations — handled honestly, never papered
over:**

- **One accounting basis per company.** When Yahoo exposes a full trailing
  twelve months of *both* income and cash-flow quarters, every metric is computed
  on a true TTM basis. Otherwise the whole company falls back to an **annual**
  basis — revenue *and* OCF/capex/SBC all from the latest fiscal year — so
  growth, FCF margin, SBC%, and Rule of 40 share one base. The basis used is
  shown on each name and flagged `low-confidence` when annual.
- **SBC may be absent.** If the cash-flow statement is retrieved but has no SBC
  line, SBC is treated as 0, the name is flagged `sbc_assumed_zero`, and it is
  listed in a dedicated "SBC assumed 0" review section (utilities/staples like CEG
  genuinely report ~0). Only when the cash-flow statement can't be retrieved at
  all is the name skipped. Supply a verified figure via `overrides.json` to
  resolve it.
- Forward EPS growth is a rough Yahoo proxy, always flagged `low-confidence`.

### FMP (optional upgrade — requires a paid tier)

`FMPProvider` implements the FMP v3 REST endpoints (quarterly statements, quote,
analyst estimates). It is **not required** for any default workflow. To use it:

```bash
export FMP_API_KEY=your_key_here     # never commit this
python -m etf_screen.cli --provider fmp --tickers MSFT,GOOGL,CRWD
```

FMP fundamentals require a **Starter/Premium** plan. On a free key the statement
endpoints return `403`; the provider fails with a clear message naming the
endpoint and the tier needed — it does not silently fill gaps.

### Mock

Canned data for offline runs and the test suite.

## Constituents

`constituents.resolve(etf)` resolves holdings **key-lessly**, keyed by ETF ticker:

1. **Wikipedia index table** (primary) — e.g. the Nasdaq-100 "Current components"
   table for `QQQ`, the S&P 500 list for `SPY`. Records the URL as `source` and
   the fetch date as `as_of`.
2. **Bundled static list** (fallback) — a dated snapshot, emitted with a loud
   `STALE / as-of` warning. The index rebalances, so provenance is always shown.

The issuer's official holdings file (Invesco / SSGA) is the most authoritative
source, but its download *endpoints* are brittle and undocumented, so they are
deliberately not a hard dependency. Instead, you can hand the tool a file you
downloaded yourself — see below.

### Screen any ETF from its holdings CSV

Built-in universes cover QQQ and SPY. For **any other ETF** — including actively
managed funds with no clean index table, like ARKK — download the issuer's
holdings CSV (ARK, iShares, SSGA/SPDR, Vanguard, Invesco all publish one) and
point `--holdings` at it:

```bash
python -m etf_screen.cli --holdings ~/Downloads/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv \
    --sector-context --export md
```

The loader is generic: it finds the ticker column by name (tolerating preamble
rows above the header) and discards anything that isn't a ticker — blank rows,
footer disclaimers, cash and derivative lines. Provenance (`as-of` date, fund
name) is read from the file's own columns when present, so the run is properly
dated. No network, no API key, no per-issuer configuration. Precedence is
`--tickers` > `--holdings` > `--universe`.

> Holdings files are point-in-time snapshots and (for active funds) change often
> — the export header records exactly which file and as-of date produced a run.

## Integrity

The screen **never invents numbers.** Missing or blocked data → the name is
skipped and listed under "could not source." A short (or empty) shortlist is an
acceptable, expected result for a strict screen — thresholds are never loosened
to populate the table.

That integrity is about *faithfully reporting what the sources say* — it is not a
guarantee the underlying data is correct. As noted up top, this is an educational
screening tool, **not investment advice**; verify independently before acting on
any output.

## Using the tool well (and its limits)

**What it is.** A GARP / quality-growth **first-pass filter** — a way to narrow a
universe down to a handful of names that *warrant a closer look*. It is **not** a
strategy, a backtest, or a buy list. Surviving the screen is the start of the
work, not the end of it.

**A workflow that respects that:**

1. **Screen** a universe (QQQ, SPY, a ticker list) down to a shortlist.
2. **Do the fundamental due diligence** on each survivor. Is the growth durable
   or cyclical? Is there a real moat? Is a high Rule of 40 a one-off — a biotech
   milestone payment, a one-time licensing deal, a pull-forward — or a repeatable
   engine? The screen sees one trailing window; you have to supply the narrative.
3. **Read the trust flags.** The "could not source", "SBC assumed 0", `annual`
   basis, and `low-confidence` annotations tell you how much to trust each row.
   A pass built on assumed-zero SBC or an annual-basis fallback is a softer pass.
4. **Use the sector context** to compare like-with-like (see above) — a
   capital-intensive name that looks expensive on an absolute P/S may be cheap
   for its sector, and vice-versa.
5. **Diversify.** The output skews toward high-beta growth; a shortlist is not a
   portfolio. Don't mistake five names that all pass the same growth filter for
   five independent bets.
6. **Size by risk, demand margin of safety.** The higher the multiple, the more
   has to go right and the more a position should be sized for being wrong.
7. **Accept the misses.** A disciplined screen will exclude some big winners —
   the hyper-grower at a nosebleed P/E, the turnaround that hasn't shown up in
   the trailing numbers yet. That is the *cost* of the discipline, not a flaw to
   tune away by loosening thresholds.

**Known biases and limits — know what the screen is structurally bad at:**

- **It favors asset-light, high-margin businesses.** Software and similar models
  clear the Rule-of-40 / FCF-margin bars easily; **capital-intensive** sectors
  (semis, utilities, industrials) are penalized because heavy capex compresses
  free cash flow. That is a real bias, not a verdict on business quality — the
  sector context exists partly to make it legible.
- **PEG is weak on the free provider.** Forward EPS growth from yfinance is a
  rough proxy (always flagged `low-confidence`), so the PEG filter and Track A/B
  split are softer than they look on the default provider.
- **The value/quality premium is regime-dependent.** It has gone through long
  droughts. A screen like this is better understood as *managing downside and
  avoiding overpaying* than as a guarantee of outperformance — it tilts the odds,
  it does not promise a result.

**Not investment advice.** Data may be stale or inaccurate; the methodology is
opinionated and incomplete by design. Verify every figure independently and
consult a qualified professional before any financial decision.

## Development

```bash
pytest -q          # 44 tests: acceptance fixtures, edge cases, data layer,
                   # basis consistency, SBC-assumed-0, overrides, export,
                   # sector context (incl. the verdicts-unchanged regression),
                   # and the issuer-holdings-CSV loader
flake8 etf_screen tests
```

The acceptance fixtures in `tests/test_screen.py` lock in the validated
behavior — especially the `PriceyButProfitable` rejection that keeps the PEG
filter from being toothless.
