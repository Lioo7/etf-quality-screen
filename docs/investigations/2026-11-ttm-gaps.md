# TTM sums across gaps in Yahoo's quarterly columns (issue #71, 2026-11-23)

Question: how often does a TTM sum skip a missing quarter and include the year-ago
one, how wrong does that make it, and what changes once every TTM line reads one
dated, gap-free window (D23)?

Data: the 2026-11-22 raw Yahoo snapshot behind #20, D21 and D22 (QQQ via the Nasdaq
API, SPY via Wikipedia: 518 names). It was replayed offline through `main` (26e895a +
docs) and through this branch. Reference figures come from SEC companyfacts, using
reported duration facts only.

## 1. The bug

`_row()` drops NaN cells, so `vals[:4]` was "the newest 4 *finite* cells". APH shows it:

| Quarter ending | 2026-09-30 | 2026-06-30 | 2026-03-31 | 2025-12-31 | 2025-09-30 |
|---|---|---|---|---|---|
| Yahoo revenue ($B) | 10.230 | NaN | 7.620 | 6.439 | 6.194 |
| Cash-flow column | present | **absent** | present | present | present |

`main` summed 2026-09 + 2026-03 + 2025-12 + 2025-09: a "trailing year" holding the
year-ago quarter in place of the missing one. The same read applied to OCF, capex,
SBC, net income, EBIT, the 8-quarter prior sum, and the year-ago share count
(`shares_q[4]`).

## 2. Where the gaps sit

These are the gaps that moved a name off TTM, 53 in all. Position counts down the
quarter columns from the newest one with finite revenue (the window anchor): 1–4 is
the TTM window, 5 is the year-ago column.

| Kind | Names | Line (count) | Position | Fiscal quarter |
|---|---|---|---|---|
| Hole at calendar 2026-06-30 | 28 | revenue 26, capex 1 (CFG), net income 1 (FITB) | 2 | Q2 (all Dec FY) |
| Year-ago share count missing | 14 | diluted shares | 5 | Q2 9, Q3 5 |
| Line missing at the newest quarter | 8 | capex 4 (HIG, HON, LEN, WRB), shares 2 (AAPL, VRSK), OCF 1 (GM), SBC 1 (NEM) | 1 | Q3 7, Q4 1 (AAPL) |
| Hole at fiscal Q4 | 3 | revenue (AWK), OCF (IQV), SBC (PPL) | 4 | Q4 |

By fiscal quarter, the 53 split Q2 37, Q3 12, Q4 4. Revenue alone: Q2 26, Q4 1.

**The derived-Q4 hypothesis is not supported.** US filers don't file Q4 on a 10-Q,
so a vendor derives it as FY minus 9 months, and a missing derivation would show up
at fiscal Q4. Only 4 of 53 gaps are fiscal Q4. Most are one calendar-quarter hole
(2026-06-30) across many December-FY filers, a hole in the vendor's data for a
quarter that *was* filed on a 10-Q. #54's SEC data could fill these from the 10-Q
3-month facts directly, and the rare Q4 gaps from 10-K minus 10-Q facts, instead of
the annual fallback. That isn't built here.

The two smaller kinds:

- **Year-ago share count (14: AMAT, COR, HD, KEYS, LOW, NVDA, PTC, RJF, ROST, TER,
  TGT, TJX, WMT, WSM).** Yahoo carries a newer partial column with diluted shares but
  NaN revenue (AMAT 2026-10-31, WMT 2026-10-31), and the oldest column has revenue but
  NaN shares. `main` read a valid 12-month share pair that ends one quarter after the
  revenue window. The branch reads shares at the window dates, finds the year-ago cell
  empty and moves the name to annual. All 14 are REJECTED either way.
- **Newest-quarter line missing (8).** The income statement has the new quarter but a
  cash-flow line doesn't. `main` summed revenue for quarters 1–4 and capex for 2–5:
  two different years in one FCF margin.

## 3. Same-snapshot verdict diff (main vs branch)

518 names in both runs. 53 move from TTM to annual; none moves the other way, and no
name newly skips.

| | REJECTED | WATCHLIST | PASS |
|---|---|---|---|
| The 53 on the branch | 51 | 1 (AAPL, unchanged) | 1 (NEM, unchanged) |

**One flip: APH PASS → REJECTED,** Net Debt/FCF 3.1 (> 3.0).

| APH | FCF ($B) | Net debt / FCF |
|---|---|---|
| `main` skip-sum (wrong window) | 5.133 | 2.67 |
| Branch annual (FY2025) | 4.378 | 3.13 |
| SEC true TTM to 2026-09-30 | 5.121 | 2.68 |

Net debt is the 2026-09-30 balance sheet: 17.87 debt − 4.17 cash = $13.70B. APH raised
debt in Q4 2025 (8.07 → 15.50) and spent the cash in Q1 2026 (11.13 → 4.13): an
acquisition. FY2025 FCF predates most of the acquired business, while the debt is
current. **The branch's verdict is wrong for APH.** The fallback is a whole, sourced
year, but a stale one. The skip-sum got close here by accident: the year-ago quarter
it pulled in roughly matched the missing one. Yahoo exposes no 2026-06-30 cash-flow
column, so no gap-free TTM exists in this source. The remedy is a sourced quarter
(#54), not a filled one.

## 4. SEC check (10 names with a revenue gap at 2026-06-30)

(a) is the old skip-sum against SEC's true TTM ending at Yahoo's newest quarter.
(b) is the branch's annual figure (Yahoo's newest FY) against SEC's 12-month FY fact.

| Ticker | Old skip-sum ($B) | SEC TTM | (a) error | FY end | Branch annual | SEC FY | (b) error |
|---|---|---|---|---|---|---|---|
| ABT | 47.005 | 48.229 | −2.5% | 2025-12-31 | 44.328 | 44.328 | 0.000% |
| AMZN | 777.018 | 797.455 | −2.6% | 2025-12-31 | 716.924 | 716.924 | 0.000% |
| APH | 30.483 | 33.047 | −7.8% | 2025-12-31 | 23.095 | 23.095 | 0.000% |
| BA | 93.623 | 94.913 | −1.4% | 2025-12-31 | 89.463 | 89.463 | 0.000% |
| BSX | 20.692 | 21.070 | −1.8% | 2025-12-31 | 20.074 | 20.074 | 0.000% |
| CHD | 6.301 | 6.246 | +0.9% | 2025-12-31 | 6.203 | 6.203 | 0.000% |
| ETN | 30.441 | 31.984 | −4.8% | 2025-12-31 | 27.448 | 27.448 | 0.000% |
| EW | 6.487 | 6.675 | −2.8% | 2025-12-31 | 6.068 | 6.068 | 0.000% |
| IT | 6.292 | 6.444 | −2.4% | 2025-12-31 | 6.497 | 6.497 | 0.000% |
| MDLZ | 40.287 | 39.898 | +1.0% | 2025-12-31 | 38.537 | 38.537 | 0.000% |

- The skip-sum error runs −7.8% to +1.0%, median −2.5%. Growing names are understated,
  because a year-old quarter stands in for a recent one.
- All 10 annual figures match SEC exactly.
- The concept is `RevenueFromContractWithCustomerExcludingAssessedTax`, except BA and
  MDLZ (`Revenues`).

## 5. Caveats

- One snapshot. The 2026-06-30 hole is a vendor-side event and may clear on a later
  pull; the annual fallback then gives way to TTM by itself.
- The annual fallback is exact but stale: up to 9 months on this snapshot, since most
  FYs end 2025-12-31 against a 2026-09-30 newest quarter. APH shows that cost.
- The 14 year-ago share-count moves come from Yahoo's partial newest columns. Their old
  share pair was a real 12-month change, ending one quarter past the revenue window.
- The SEC check covers revenue only. The APH FCF row uses
  `NetCashProvidedByUsedInOperatingActivities` and
  `PaymentsToAcquirePropertyPlantAndEquipment`.

## Scripts

These are git-ignored, in `scratch/`:
- `scratch/growth_diff.py <code_root> <raw_dir> <out.json>`: replays the snapshot
  through one checkout (run once for `main`, once for the branch).
- `scratch/ttm_gaps.py <raw_dir> <main.json> <branch.json>`: gap tabulation and the
  verdict diff.
- `scratch/ttm_sec.py <raw_dir> <out.json>`: the SEC check. Run it from the repo root
  after `set -a; source .env; set +a`, because it needs `SEC_USER_AGENT` (never printed).
