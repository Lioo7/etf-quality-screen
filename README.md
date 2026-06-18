# qqq-quality-screen

A small, well-tested CLI that screens the current Invesco QQQ (Nasdaq-100)
constituents — or any ETF/ticker list — through a four-filter, two-track
quality-growth methodology, pulling live fundamentals from a **pluggable** data
provider.

Screening logic and data sourcing are kept strictly separate:

| Module | Responsibility | Network? |
| --- | --- | --- |
| `qqq_screen/screen.py` | `Company`, `evaluate()`, ranking | No |
| `qqq_screen/providers.py` | `DataProvider` ABC + yfinance / FMP / Mock | Yes |
| `qqq_screen/constituents.py` | resolve ETF holdings → tickers | Yes |
| `qqq_screen/cache.py` | per-day disk cache | No |
| `qqq_screen/cli.py` | argparse entrypoint + reporting | — |

## Methodology

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
python -m qqq_screen.cli --universe qqq

# Cheap validation against an explicit ticker list
python -m qqq_screen.cli --provider yfinance --tickers MSFT,GOOGL,CRWD

# Offline / deterministic demo
python -m qqq_screen.cli --provider mock --tickers MSFT,GOOGL,CRWD

# Screen the S&P 500 instead, throttling requests
python -m qqq_screen.cli --universe spy --throttle 1 --limit 50
```

Flags: `--provider {yfinance,fmp,mock}` · `--universe <etf>` · `--tickers a,b,c`
· `--limit N` · `--refresh` (ignore cache) · `--no-cache` · `--throttle SECONDS`
· `--overrides PATH` · `--export {csv,md}` · `--out PATH`.

### Exporting

`--export {csv,md}` writes the **full** result set — survivors, rejected, *and*
skipped — with every metric, the track label, the rejection/skip reason, and the
confidence/basis flags, in addition to the console output. A provenance header
(provider, source, as-of, run date) is included as CSV comment lines or a
Markdown header block. Default filename: `qqq_screen_<universe>_<YYYY-MM-DD>.<ext>`
(override with `--out`).

```bash
python -m qqq_screen.cli --universe qqq --export csv
python -m qqq_screen.cli --tickers MSFT,GOOGL,CRWD --export md --out shortlist.md
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
`market_cap`, `forward_pe`, `forward_eps_growth`. Point `--overrides` elsewhere to
use a different file.

### Example output (mock)

```
==============================================================================
qqq-quality-screen v0.1.0  |  run 2026-06-17
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

  #  Ticker    Company                   Track     Growth%  AdjFCF%  Rule40   P/S  Dil%  SBC%  PEG
---  --------  ------------------------  -------  --------  -------  ------  ----  ----  ----  ----
  1  CRWD      CrowdStrike Holdings,…    Track B      45.8     13.7    59.5  22.9   4.3  14.3  N/A

Why survivors passed:
  CRWD (CrowdStrike Holdings,…): Track B (Rule40 59.5, P/S 22.9 vs guardrail 22.9, SBC 14.3%)
```

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
python -m qqq_screen.cli --provider fmp --tickers MSFT,GOOGL,CRWD
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
source, but its download endpoints are brittle and undocumented, so it is
deliberately not a hard dependency.

## Integrity

The screen **never invents numbers.** Missing or blocked data → the name is
skipped and listed under "could not source." A short (or empty) shortlist is an
acceptable, expected result for a strict screen — thresholds are never loosened
to populate the table.

## Development

```bash
pytest -q          # 29 tests: acceptance fixtures, edge cases, data layer,
                   # basis consistency, SBC-assumed-0, overrides, export
flake8 qqq_screen tests
```

The acceptance fixtures in `tests/test_screen.py` lock in the validated
behavior — especially the `PriceyButProfitable` rejection that keeps the PEG
filter from being toothless.
