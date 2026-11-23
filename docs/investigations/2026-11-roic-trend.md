# Should ROIC gate Track A in trend mode? (issue #45, 2026-11-23)

Question: ROIC >= 10% rejects Track A names only under `strict`. The CLI defaults to
`trend`, so in normal runs ROIC never rejects. Should trend mode gate it too, on the
current figure or on the multi-year median?

Data: the latest exports, `results/etf_screen_qqq_2026-11-19.md` and
`results/etf_screen_spy_2026-11-19.md` (yfinance, trend mode, data as of 2026-11-18).
The QQQ 2026-08-03 and SPY 2026-07-11 exports were checked too. "Track A" here
means GAAP-profitable (`net_income_ttm > 0`): every PASS on Track A, plus every
WATCHLIST name, all of them profitable in these runs. Units: the export's `roic` is a
fraction and `roic_median` a percentage (#17). Below, both are in %.

## 1. The definition

`ROIC = operating income (EBIT, pre-tax) / (total debt + total equity - cash)`.
Current ROIC uses TTM EBIT and the latest balance sheet. The median (`roic_history()`)
covers up to the last 10 fiscal years and skips years with no EBIT or with invested
capital <= 0. Fewer than 3 usable years is flagged as thin.

Blind spots, all visible in these exports:

- **Buyback-driven low or negative equity.** Invested capital goes to zero or below,
  so ROIC is not computable (`N/A`, never a fail): FTNT and VRSN (both WATCHLIST)
  have no usable year at all. When equity is small but still positive, ROIC blows up:
  MA 106% (median 102%).
- **Financials.** For a bank or broker, debt is raw material and cash is client
  money, not excess capital: IBKR 3,333% (median 1,642%), AMP 308%, COF `N/A`. The
  number means nothing here (the financial note already says so).
- **Intangible-heavy software.** R&D and sales spend is expensed, never capitalised,
  so invested capital is understated and ROIC overstated when margins are high. While
  that spend still exceeds gross profit, EBIT is negative and the ratio says nothing
  about the moat. In both cases ROIC is a poor fit for software.
- **Turnarounds and short histories.** The median takes in years before the business
  turned profitable, or years of a cyclical trough. It measures the past path, not the
  current return: SHOP median 6.2% vs 23% now; PLTR 7.0% vs 42%.

## 2. Track A PASS / WATCHLIST names below 10%

No Track A PASS or WATCHLIST name in either export has **current** ROIC below 10%.
Four have a **median** below 10%:

| Universe | Ticker | Status | ROIC now | Median | Usable years | Why the median is low |
|---|---|---|---|---|---|---|
| QQQ, SPY, ARKK | SHOP | PASS | 23% | 6.2% | 4 | Loss years before 2023 |
| QQQ, SPY | SNDK | PASS | 160% | 5.2% | 3 | Spun off from WDC in 2025; memory-cycle trough years |
| SPY | RDDT | PASS | 40% | −6.5% | 2 (thin) | Pre-profit years after its 2024 IPO |
| QQQ, SPY, ARKK | PLTR | WATCHLIST | 42% | 7.0% | 4 | GAAP losses before 2023 |

Every name has a current ROIC far above the floor. The low medians come from
the early or trough years of a business that has since turned.

Track A snapshot qualifiers with current ROIC below 10% exist, but **every one is
already rejected by another gate**:

| Ticker (universe) | ROIC now | Rejected by |
|---|---|---|
| KDP (QQQ, SPY) | 7% | Net debt / FCF 11.9; earnings quality |
| MRVL (QQQ, SPY) | 8% | Goodwill / assets 50%; consistency |
| SNPS (QQQ, SPY) | 3% | Goodwill / assets 56%; earnings quality |
| ALB (SPY) | 9% | Consistency |
| HPE (SPY) | 8% | Net debt / FCF 3.4; earnings quality |
| OMC (SPY) | 9% | Consistency |
| WAT (SPY) | 2% | Net debt / FCF 16.9; earnings quality |

The older exports show the same pattern: QQQ 2026-08-03 and SPY 2026-07-11 have
no Track A PASS/WATCHLIST below 10% now. MCHP, LITE, FIS, BG, AMCR, SNPS, WAT and HPE
fall below 10% now and are rejected by leverage, goodwill, earnings quality or
consistency. The only median-only cases are again SHOP and PLTR. SNDK sits at
INSUFFICIENT_HISTORY there.

## 3. Flips under each option

Gating adds a balance-sheet failure, and REJECTED beats the history gate, so a
PASS or WATCHLIST name that fails would become REJECTED.

| Option | QQQ 2026-11-19 | SPY 2026-11-19 | Older exports |
|---|---|---|---|
| (a) gate current ROIC in trend | 0 flips | 0 flips | 0 flips |
| (b) gate median ROIC (any years) | SHOP, SNDK PASS → REJECTED; PLTR WATCHLIST → REJECTED | SHOP, SNDK, RDDT PASS → REJECTED; PLTR WATCHLIST → REJECTED | SHOP PASS, PLTR WATCHLIST → REJECTED |
| (b′) gate median, only with ≥ 3 usable years | as (b) | as (b) minus RDDT | as (b) |
| (c) keep advisory | 0 flips | 0 flips | 0 flips |

(a) is a no-op on three snapshots. Leverage, goodwill, earnings quality and the
consistency gate already reject every profitable qualifier below the floor. It would add a
second reason to rejections that already exist, and nothing else.

(b) rejects exactly the names trend mode exists to admit. Each of them clears the
Rule of 40 and the Track A consistency trend now, and earns a high return on capital
now. The median penalises them for losses they have left behind. That is the job
`strict` already does with its own consistency rule. A median gate in trend mode would
bring strict-style hindsight into the mode meant to look at the trajectory. Gating on a 2- or
3-year median (RDDT, SNDK) would also gate on thin, admittedly noisy evidence.

## 4. Recommendation: (c), keep ROIC advisory in trend mode

- (a) changes no verdict on any available snapshot. It would leave the strict/trend
  split as a consistency rule alone, for no measured gain.
- (b) rejects four names whose ROIC is 23–160% now. The cause is history that the
  trend mode is explicitly built to forgive. Its errors are blind spots (turnarounds,
  spin-offs, IPOs), not a weak moat.
- The median stays visible as `roic_median` / `ROIC Med(y)` and the `roic_durable`
  tag, so a reader can see SHOP's 6.2% without the screen acting on it.

**Revisit** when a trend-mode Track A PASS or WATCHLIST name shows current ROIC
below 10% that no blind spot above explains (not a financial, not negative equity).
That name would be the first case (a) catches that the other gates miss. Check this
in the monthly runs (D13).

## Caveats

- These exports predate D22 (one growth figure) and D23 (gap-free TTM window).
  Current ROIC's EBIT is a TTM sum and could move slightly under D23. A verdict
  could also shift (D23 moved APH PASS → REJECTED on leverage). Neither changes the
  balance-sheet inputs, or the finding that no qualifier sits near 10% now. The
  raw 2026-11-22 snapshot used for D22/D23 was no longer available to replay.
- Three snapshots, all in 2026. A market where capital-heavy, low-return names grow
  fast enough to pass the Rule of 40 would test (a) harder than this one.

## Script

`roic.py` (scratchpad, not kept): parses an export's Markdown table, keeps names with
`net_income_ttm > 0`, and lists PASS/WATCHLIST names plus any snapshot qualifier
whose `roic < 0.10` or `roic_median < 10`.
