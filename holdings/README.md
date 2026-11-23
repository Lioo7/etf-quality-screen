# Holdings drop zone

Holdings CSVs live here (git-ignored `*.csv`). `--universe qqq` and `--universe arkk`
read `holdings/<etf>-holdings.csv` and keep it fresh on their own (#8, D26).
`--holdings PATH` screens any other issuer file.

## Auto-refresh (`--universe qqq|arkk`)

- **Age** = the file's own as-of date column. If the file has no date column, its
  modification time is used. Today's date is never used.
- **Missing, or older than `--max-holdings-age` (default 14 days)** → the file is
  downloaded and validated:
  - it must parse;
  - the ticker count must be in range;
  - the as-of date must be within the max age.
- **On success**, the new file is written atomically and the old one is kept as
  `<etf>-holdings.csv.bak`. The run prints `refreshed from <url>, as-of <date>`.
- **Any failure** (HTTP error, schema change, count out of range, stale date) exits
  non-zero with the manual steps below. It never falls back to an old list silently.
  `--allow-stale` accepts the old file, or the bundled static list, and marks the
  run STALE (Source line and export header).

| Universe | Source (checked 2026-11-23) | Label shown | Tickers |
|---|---|---|---|
| `qqq` | `https://api.nasdaq.com/api/quote/list-type/nasdaq100` (JSON; `data.date` is the as-of) | `Nasdaq-100 index constituents (Nasdaq API, as-of <date>)` | 90–110 |
| `arkk` | `https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv` | `ARK ARKK holdings CSV (ark-funds.com), as-of <date>` | 20–70 |
| `spy` | Wikipedia S&P 500 list, live fetch (no file here) | `Wikipedia (live fetch; page freshness not verified)` | 495–510 |
| `mid` / `sp400` | Wikipedia S&P 400 list, live fetch (no file here) | `Wikipedia (live fetch; page freshness not verified)` | 395–410 |

**QQQ uses the index list, not Invesco's file.** Invesco's QQQ holdings download
endpoint rejects scripted requests (HTTP 406), so it has no stable direct URL. QQQ
fully replicates the Nasdaq-100, so the index constituents are the right set. They
are labelled as the index list, never as "Invesco QQQ holdings". The Nasdaq
endpoint is public but unofficial, so expect it to break eventually. When it does,
the run fails loudly.

Both endpoints reject non-browser User-Agents. The downloader sends a browser-style
one.

**SPY, possible future source:** SSGA publishes an official daily SPY holdings file
(xlsx) on ssga.com. It isn't used yet.

## Manual download (when a refresh fails)

1. **QQQ:** save the Nasdaq-100 constituents as `holdings/qqq-holdings.csv`. Either
   convert the Nasdaq API JSON to columns `Date,Fund,Name,Ticker`, or save the
   official Invesco QQQ holdings CSV from invesco.com.
   **ARKK:** download the CSV from the URL above, saved as `holdings/arkk-holdings.csv`.
2. Rerun. A file without a date column is aged by its modification time.

## Regenerate the QQQ example

```bash
.venv/bin/python -m etf_screen.cli --universe qqq \
  --consistency-years 3 --sector-context \
  --export md --out examples/qqq-$(date +%F).md
```

The loader finds the ticker column by name and tolerates preamble/footer rows —
see `etf_screen.constituents.holdings_from_csv`.
