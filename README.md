# etf-quality-screen

A small, well-tested CLI that screens the current Invesco QQQ (Nasdaq-100)
constituents — or any ETF/ticker list — through a four-filter, two-track
quality-growth methodology — with balance-sheet and multi-year durability gates
layered on top — pulling live fundamentals from a **pluggable** data provider.

> ⚠️ **Disclaimer — not investment advice.** This tool is for **educational and
> screening purposes only**. It is not investment advice or a recommendation to
> buy or sell any security. Data is pulled from free/third-party sources and may
> be **inaccurate, incomplete, or stale** (see the confidence flags). Always
> verify figures independently and consult a qualified professional before making
> any financial decision.

Screening logic and data sourcing are kept strictly separate:

| Module | Responsibility | Network? |
| --- | --- | --- |
| `etf_screen/screen.py` | `Company`, `evaluate()` (filters, tracks, gates), ranking | No |
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

**Pre-revenue firewall.** Before any of the above runs, a name with no trailing
revenue (`revenue_ttm ≤ 0`) is short-circuited to `PRE_REVENUE` — no growth, no
margin, no divide-by-zero. (A name with revenue now but a zero *prior* year is a
separate "not evaluable" case.)

**Balance-sheet gates (current snapshot).** A name that clears the snapshot is
then checked against its latest balance sheet — these can only *reject*, never
rescue:

- **Leverage** — net debt ÷ FCF ≤ 3 (net-cash names pass automatically; net debt
  with non-positive FCF fails).
- **Goodwill** — goodwill ≤ 40% of total assets (catches goodwill-heavy serial
  acquirers).
- **ROIC** — pre-tax operating income ÷ invested capital ≥ 10%. Applied **only to
  a Track-A name under `strict` consistency mode**; advisory (never a failure)
  when it can't be computed.
- **Earnings quality** — cumulative 3-year adjusted FCF ÷ cumulative 3-year net
  income ≥ 0.8, for names with cumulative net income > 0 (either track).
  Profits that never become cash are the classic accounting red flag —
  aggressive revenue recognition, ballooning receivables, capitalized costs.
  A single year is noisy from working-capital swings, so this cumulates over 3
  years (fewer if that's all the history has, and the shorter basis is
  reported); **N/A, never a fail**, when cumulative net income isn't positive
  — Track B's own quality gate already covers unprofitable names.

  An EQ failure has **two readings**, and the ratio's arithmetic cannot tell
  them apart. The bad one is accrual rot: reported profits that never become
  cash because revenue was recognized aggressively or receivables are
  ballooning. The benign one is heavy reinvestment: a company pouring cash
  into growth capex and working-capital build (fabs, datacenters, inventory
  ahead of demand) depresses adjusted FCF for reasons that have nothing to do
  with accounting quality — NVDA (~0.76) and TSM (~0.58) fail the gate this
  way. The threshold never moves, but when cumulative capex over the same
  3-year window exceeds **15% of revenue**, the rejection carries an
  informational note — *"EQ shortfall may reflect capex intensity, not
  accruals"* — and the capex/revenue figure is exported, so a failed name can
  be re-judged by a human with the right frame instead of being mistaken for
  an accounting red flag.

**Multi-year consistency gate.** Optional durability check over annual history
(`--consistency-years N`, default 3) layered on a snapshot qualifier:

- **Track A** — Rule of 40 across the last *N* windowed years. `--consistency-mode`
  picks the rule: **`strict`** (≥ 40 every year) or **`trend`** (the default —
  passes if the latest year is ≥ 40, the *N*-year mean is ≥ 30, and there's no
  lumpy collapse below 25 from a higher prior year; a soft launch year is
  forgiven). Trend rewards a durable trajectory without punishing one dip.
- **Track B** — adjusted FCF > 0 in every available year (mode-independent).
- Too little history → the name is set aside as `INSUFFICIENT_HISTORY`, reported
  separately rather than silently passed or failed.

When multiple gates fail, a balance-sheet failure is definitive and takes
precedence over the history checks in the verdict.

**Watchlist — quality passes, valuation fails.** The most common screening
outcome for a genuinely great company is "wonderful business, wrong price".
A name that clears **every quality gate** (its would-be track, the
balance-sheet gates, earnings quality, and the consistency gate) but fails
**only** the valuation filters (P/S guardrail and/or a computable-but-failed
PEG) is classified `WATCHLIST` instead of being discarded — reported in its own
section and export rows with the **unlock price**: the price at which P/S would
equal `0.5 × growth%` (and, where PEG was the blocker, the price at which PEG
= 2.0), computed on the same share count and revenue base as the P/S itself.
The buy trigger is computed on a calm day so a future dip doesn't have to be
re-judged under FOMO. Boundaries: a name failing quality **and** valuation
stays plain rejected; zero/negative growth → unlock `N/A` (a cheap price can't
fix a shrinking company — it must re-qualify on quality first). `WATCHLIST`
names are never in the shortlist.

**Needs data — "PEG untestable" is not "PEG failed".** A profitable name must
clear PEG on Track A, but some names simply carry no forward estimates —
unsponsored ADRs are the classic case (Keyence/KYCCF in the live Japan run).
Rejecting them would conflate a failed test with an unadministrable one, and
no price movement can fix missing estimates. So a name whose **only** blocker
is an uncomputable PEG — profitable, and every *testable* gate passing (P/S
included, plus the same balance-sheet / earnings-quality gates a `WATCHLIST`
candidate must clear; *insufficient* history is tolerated as one more missing-
data condition, since unsponsored ADRs pair thin estimates with thin history,
but a real consistency **failure** still rejects) — is classified
`NEEDS_DATA`: its own report
and export section, never in the shortlist, with the pointer that verified
`forward_pe` / `forward_eps_growth` values in `overrides.json` complete the
screen — and, when the Yahoo market cap looks wrong (unsponsored OTC ADRs
routinely do; Keyence/KYCCF showed P/S 0.1 against a mid-single-digit sales
multiple on the listed line), a verified `market_cap` too. Completing PEG
against a fictional cap would finish the screen against a fictional valuation.
Boundaries: a *computable* failed PEG keeps its normal fate
(`WATCHLIST`/`REJECTED`); a name that also fails P/S or any quality gate stays
plain rejected — more estimate data could not have saved it; Track B is
untouched (PEG is structurally N/A there). Five-letter OTC ADR tickers (ending
in `Y` or `F`) also carry the informational `otc_adr_data_suspect` flag —
Yahoo caps/statements for those lines are routinely broken; the flag never
gates.

**IPO seasoning.** A name listed within the last 12 months
(`--ipo-seasoning-months`, `0` disables) is set aside as `RECENT_IPO` — never
in the shortlist, reported in its own section like `INSUFFICIENT_HISTORY`.
Recent IPOs structurally break the methodology's inputs (no history for the
history gates, SBC/dilution distorted by the offering, lockup expirations
scheduling known supply shocks), and the long-run base rates for IPO-year
buyers are hostile (Ritter). When the source exposes no first-trading date it
is **not guessed**: the name is screened normally and flagged
`ipo_date_unknown`.

**Gross-margin level & stability (advisory).** Gross margin is where pricing
power shows up first: a high *and stable* margin through cycles means customers
keep paying up and competitors can't force discounting. Each snapshot qualifier
reports its latest gross margin and the stddev across up to 10 annual years
(`GM% (σ,y)`); GM ≥ 40% with stddev < 5pp over ≥ 5 years earns the
`pricing_power` tag. Names that don't report cost of revenue (banks, insurers)
are marked `gm_not_applicable` — never guessed. Advisory only, **not** a gate:
hard-gating on GM would double-punish semis/industrials that can be excellent
businesses at structurally lower margins.

**ROIC history (advisory).** A single year's ROIC is noisy — one restructuring
charge or a cyclical margin peak can swing it violently. Alongside the snapshot
gate above (unchanged), each snapshot qualifier also gets the **median** ROIC
over up to the last 10 annual periods, plus the year count actually used
(e.g. `ROIC Med(y)` shows `18.2 (7y)`). A year missing operating income, or with
non-positive invested capital, is skipped rather than fabricated. Median ≥ 15%
over ≥ 5 usable years earns the `roic_durable` tag — evidence of a moat wide
enough to survive one bad year, not a rounding error. This is purely
informational: it never changes `passed`, `track`, or ranking.

One honest blind spot, by construction: companies with **negative or near-zero
equity** (usually from aggressive buybacks — BKNG is the canonical example)
have non-positive or tiny invested capital, so their ROIC renders `N/A` or an
unstable, meaninglessly huge median. That is the metric being truthful about
its own denominator, not a bug: ROIC measures returns on the capital actually
employed, and a balance sheet that has bought back its equity base has removed
the denominator. Read such names on the other quality evidence instead.

**Entry timing (informational).** The screen answers "what is worth owning" —
deliberately not "when to buy". But the downstream workflow applies an
anti-FOMO entry rule, so each name carries two timing columns from
**split/dividend-adjusted** price history (yfinance `history(period="max",
auto_adjust=True)` closes — raw closes would make any splitter's ATH
meaningless): distance from the all-time high (`ATH%`) and the trailing
3-month total return (`3M%`). A 3-month return above **+50%** while within
**20% of the ATH** earns the `HOT` tag — a *patience* case, never a rejection.
Listings younger than 3 months show `N/A` (no return exists, none is invented),
and the export records how many years of history the ATH actually covers.
**Strictly informational — never affects pass/fail, tracks, or ranking.**

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

# Strict durability and a snapshot-only baseline (no history gate)
python -m etf_screen.cli --universe qqq --consistency-mode strict
python -m etf_screen.cli --universe qqq --consistency-years 0
```

Flags: `--provider {yfinance,fmp,mock}` · `--universe <etf>` · `--holdings PATH`
· `--tickers a,b,c` · `--tickers-file PATH` · `--limit N` · `--refresh` (ignore cache) · `--no-cache`
· `--throttle SECONDS` · `--overrides PATH` · `--export {csv,md}` · `--out PATH`
· `--sector-context` · `--rank {rule40,sector-relative}`
· `--consistency-years N` (default 3; `0` bypasses the history gate)
· `--consistency-mode {strict,trend}` (default `trend`)
· `--ipo-seasoning-months N` (default 12; `0` disables the RECENT_IPO set-aside).

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

**Financial Services caveat (informational).** Banks, payment networks, exchanges,
and similar balance-sheet businesses (Yahoo sector `Financial Services`) carry an
annotation: *"balance-sheet business — leverage/FCF/GM metrics unreliable;
analyze via sector-appropriate measures."* Net-debt/FCF, OCF, and gross margin
are structurally noisy for them; the flag surfaces in the console and export and
**never** changes a verdict. Survivors like COF/AMP and watchlist names like
MA/V/CME still screen under the fixed gates — the asterisk is a reading note,
not a loophole.

### Exporting

`--export {csv,md}` writes the **full** result set — survivors, rejected, *and*
skipped — with every metric, the track label, the rejection/skip reason, and the
confidence/basis flags, in addition to the console output. A provenance header
(provider, source, as-of, run date) is included as CSV comment lines or a
Markdown header block. Exports land in `results/` by default (git-ignored,
regenerable point-in-time snapshots) as
`results/etf_screen_<universe>_<YYYY-MM-DD>.<ext>` — override the full path with
`--out`. Committed, curated example runs live under [`examples/`](examples/).

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
`market_cap`, `forward_pe`, `forward_eps_growth`, `net_income_ttm`, and the
balance-sheet lines `total_debt`, `cash_and_equivalents`, `goodwill`,
`total_assets`, `operating_income`, `total_equity`. Point `--overrides` elsewhere
to use a different file.

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
  rejected by balance-sheet gate (names may fail more than one):
    Leverage >3x  : 0
    Goodwill >40% : 0
    ROIC <10%     : 0
    Earnings qlty : 0
  consistency gate: 0 demoted, 0 insufficient history (3-yr window)
  ...

Shortlist (1 names) — ranked by Rule of 40

  #  Ticker  Company        Sector      Track    Growth%  AdjFCF%  Rule40   P/S  ...  NetDebt/FCF  GW/Assets%  ROIC%  ROIC Med(y)  EQ(y)  Rule40 Hist
---  ------  -------------  ----------  -------  -------  -------  ------  ----  ---  -----------  ----------  -----  -----------  -----  --------------
  1  CRWD    CrowdStrike…   Technology  Track B     45.8     13.7    59.5  22.9  ...          0.0           2  -25.0  N/A          N/A    [63.8 -> 59.5]

Why survivors passed:
  CRWD (CrowdStrike Holdings,…): Track B (Rule40 59.5, P/S 22.9 vs guardrail 22.9, SBC 14.3%)
```

(The real table also carries the `Dil%`, `SBC%`, and `PEG` columns between `P/S`
and the balance-sheet block — elided as `...` here for width.)

## Reading a result

The screen now has enough states that output literacy *is* the user interface.
This run of the full mock universe produces every one of them:

```bash
python -m etf_screen.cli --provider mock \
    --tickers MSFT,GOOGL,CRWD,DURABLE,MOAT,LEAKY,FRESH,OPAQUE,DECEL,NEWLY,BURN,ROLLUP,LEVERED
```

**A clean pass, fully annotated — MOAT.** Walk it through every layer:

1. *Pre-revenue firewall*: revenue is positive → proceed.
2. *IPO seasoning*: no recent listing date → proceed.
3. *Routing*: GAAP-profitable → Track A only.
4. *Four filters*: Rule 40 = 69.3 (growth 33.3% + adj FCF margin 36.0%) ✓ ·
   P/S 6.0 vs guardrail 16.7 ✓ · SBC 4.0% ✓ · PEG 1.56 ✓ → snapshot Track A.
5. *Balance-sheet gates*: net cash (`NetDebt/FCF 0.0`) ✓ · goodwill 2% of
   assets ✓ · ROIC 31.4% (advisory here — trend mode) ✓.
6. *Earnings quality*: `EQ(y) N/A` — its mock history carries no net-income
   line, so the gate honestly reports "not applicable" instead of guessing.
7. *Consistency (3-yr trend)*: `Rule40 Hist [51.3 -> 48.1 -> 50.3]` — latest ≥
   40, mean ≥ 30, no collapse → PASS.
8. *Advisory annotations*: `ROIC Med(y) 28.6 (6y)` → **roic_durable** (median ≥
   15% over ≥ 5y); `GM% 75.0 (σ0.1, 6y)` → **pricing_power** (≥ 40%, stable,
   ≥ 5y). Tags appear on its "why it passed" line.
9. *Entry timing*: `ATH% / 3M%` are `N/A` (no mock price data for this name) —
   reported as unknown, never invented.

**The watchlist case — MSFT.** Quality is never the problem: profitable, Rule
40 = 48.2, clean balance sheet, trend consistency passes. But `P/S 12.7 >
guardrail 7.8` and `PEG 2.3 > 2.0` — valuation is the *only* blocker, so
instead of vanishing into the rejected pile it lands here:

```
WATCHLIST (1) — wonderful business, wrong price; ...
  MSFT (Microsoft Corporation): P/S = 12.7 (> guardrail 7.8); PEG = 2.3 (> 2.0);
  profitable, Track B barred -> unlock: P/S $256.64, PEG $365.07
```

Read the unlock prices as pre-computed buy triggers: below ~$257 the P/S
guardrail clears; below ~$365 PEG clears. Both must clear for a pass, so the
*binding* trigger is the lower one. They were computed on a calm day — when the
price gets there, the decision is already made.

**The seasoning case — FRESH.** Would pass Track A on its numbers, but it
listed ~4 months ago, so it is set aside — not rejected, not passed:

```
RECENT_IPO (1) — listed within the last 12 months; ...
  FRESH (Fresh Listing Inc.): listed 2026-03-10
```

**Every other terminal state in the same run:**

| Name | Verdict | Why |
| --- | --- | --- |
| DURABLE, DECEL | PASS (Track A) | DECEL's dip year (33.6) is forgiven by `trend`; it fails under `--consistency-mode strict` |
| CRWD | PASS (Track B) | PEG genuinely N/A; also tagged **HOT** (+58% in 3 mo, 5% off ATH) — a patience flag, not a rejection |
| OPAQUE | NEEDS_DATA | every testable gate passes but PEG has no estimates to test — supply `forward_pe` (and a verified `market_cap` if the Yahoo cap looks wrong) via `overrides.json` |
| GOOGL | REJECTED | Rule of 40 = 33.0 — a quality fail, so no watchlist candidacy |
| LEAKY | REJECTED | earnings quality: 3-yr AdjFCF/NI = 0.67 < 0.8 — profits not becoming cash |
| ROLLUP / LEVERED | REJECTED | goodwill 60% of assets / Net Debt 3.7× FCF — balance-sheet gates |
| BURN | REJECTED | a negative adjusted-FCF year in the Track-B consistency window |
| NEWLY | INSUFFICIENT_HISTORY | cleared the snapshot, but 2 of 4 required annual years |

## Example runs

Four real runs are checked in under [`examples/`](examples/) (QQQ refreshed
2026-07-14; others 2026-07-11; all with `--consistency-years 3`):

- **[QQQ (Nasdaq-100)](examples/qqq-2026-07-14.md)** — the flagship full-universe
  run against the 2026-07-09 Wikipedia snapshot (103 tickers, including SPCX):
  3 survivors + 2 watchlist. Prefer regenerating from the official Invesco CSV
  at [`holdings/qqq-holdings.csv`](holdings/qqq-holdings.csv) via `--holdings`
  when you have it (see below).
- **[SPY (S&P 500)](examples/spy-2026-07-11.md)** — the widest run (~500 names);
  the same fixed gates at scale.
- **[Israel tech ADRs](examples/israel-tech-adr-2026-07-11.md)** — a curated
  `--tickers-file` thesis list (`--sector-context`); GLBE passes Track A, CHKP
  lands on the watchlist.
- **[Japan automation ADRs](examples/japan-automation-adr-2026-07-11.md)** —
  another thesis list; Keyence (KYCCF) is the live `NEEDS_DATA` case — a
  profitable name whose only blocker is an untestable PEG (no forward estimates
  on the OTC F-share), with `otc_adr_data_suspect` set.

Each doc carries a provenance header, the ranked shortlist, the sector-context
view, and the full result set (collapsed). They are point-in-time snapshots —
the header records exactly which data produced them.

**Refreshing the QQQ example from the official Invesco holdings CSV** (preferred
source of truth over Wikipedia/static):

```bash
# Drop the downloaded Invesco QQQ holdings CSV here (git-ignored):
#   holdings/qqq-holdings.csv
.venv/bin/python -m etf_screen.cli \
  --holdings holdings/qqq-holdings.csv \
  --consistency-years 3 --sector-context \
  --export md --out examples/qqq-$(date +%F).md
```

## Data providers

### yfinance (default, free, no key)

Backed by Yahoo Finance. **Known limitations — handled honestly, never papered
over:**

- **One accounting basis per company.** When Yahoo exposes a full trailing
  twelve months of *both* income and cash-flow quarters, every level metric
  (revenue, OCF/capex/SBC, net income) is a true TTM sum of the newest four.
  Yahoo caps quarterly statements at ~5 columns (observed 2026-07), so the
  *prior-year* TTM — quarters 5–8 — is usually unpublished; **growth** then
  comes from the newest quarter vs its date-aligned year-ago counterpart
  (both raw sourced lines, flagged `growth from latest-quarter YoY`), and the
  true 8-quarter prior-TTM sum is used whenever Yahoo does provide it. A name
  without even an aligned quarter pair falls back to the honest **annual**
  basis — revenue *and* OCF/capex/SBC all from the latest fiscal year — so
  growth, FCF margin, SBC%, and Rule of 40 share one base. The basis used is
  shown on each name and flagged `low-confidence` when annual.
- **SBC may be absent.** If the cash-flow statement is retrieved but has no SBC
  line, SBC is treated as 0, the name is flagged `sbc_assumed_zero`, and it is
  listed in a dedicated "SBC assumed 0" review section (utilities/staples like CEG
  genuinely report ~0). Only when the cash-flow statement can't be retrieved at
  all is the name skipped. Supply a verified figure via `overrides.json` to
  resolve it.
- **Balance sheet, same discipline.** Cash, total assets, and total equity are
  required (their absence skips the name); goodwill and total debt are treated as
  0 when absent and flagged (`goodwill_assumed_zero` / `debt_assumed_zero`); a
  missing operating-income (EBIT) line just makes ROIC advisory rather than
  guessing it. If Yahoo exposes no balance sheet at all, the name is skipped.
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
`--tickers` > `--tickers-file` > `--holdings` > `--universe`.

### Curated ticker-list files (`--tickers-file`)

Universes should follow *theses*, not flags on a map. Scanning whole foreign
exchanges multiplies data-quality problems (thin yfinance fundamentals for
non-US listings) for negligible shortlist yield, so coverage is extended
through deliberate, hand-curated lists instead — plain text, one ticker per
line, `#` comments allowed:

```bash
python -m etf_screen.cli --tickers-file universes/israel-tech-adr.txt
```

Two starter lists are committed under [`universes/`](universes/), each with a
header comment stating its thesis:

- **[`israel-tech-adr.txt`](universes/israel-tech-adr.txt)** — US-listed
  Israeli tech (cybersecurity, SaaS, semis). All primary/dual US listings, so
  the data is US-filing quality.
- **[`japan-automation-adr.txt`](universes/japan-automation-adr.txt)** —
  Japanese factory-automation/robotics ADRs. Mostly unsponsored OTC listings:
  expect a high "could not source" rate and assumed-0 flags — that trade-off
  is documented in the file itself and is the tool being honest, not broken.

Duplicates are dropped with a note; an empty file is a loud error, never a
silent empty run; a junk line raises with its line number (a typo in a
hand-curated file should fail, not screen the wrong universe).

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
3. **Read the trust flags.** The "could not source", "SBC assumed 0",
   "balance-sheet line assumed 0", `annual` basis, and `low-confidence`
   annotations tell you how much to trust each row. A pass built on assumed-zero
   SBC/goodwill or an annual-basis fallback is a softer pass.
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
pytest -q          # ~190 tests (4 live-network, skipped unless RUN_LIVE_TESTS=1):
                   # acceptance fixtures, edge cases, data layer,
                   # basis consistency, SBC-assumed-0, overrides, export,
                   # sector context (incl. the verdicts-unchanged regression),
                   # the issuer-holdings-CSV loader, the multi-year consistency
                   # gate (strict + trend), the balance-sheet gates, the
                   # pre-revenue firewall, the ROIC-history annotation, the
                   # earnings-quality gate, the gross-margin annotation, the
                   # RECENT_IPO seasoning classification, the entry-timing
                   # annotations, WATCHLIST mode (incl. unlock-price math),
                   # the curated ticker-list loader, NEEDS_DATA, and the
                   # informational honesty flags (OTC ADR + Financial Services)
flake8 etf_screen tests
```

The acceptance fixtures in `tests/test_screen.py` lock in the validated
behavior — especially the `PriceyButProfitable` rejection that keeps the PEG
filter from being toothless.
