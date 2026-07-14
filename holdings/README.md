# Holdings drop zone

Drop issuer holdings CSVs here (git-ignored `*.csv`), then screen via `--holdings`.

## QQQ (preferred over Wikipedia / static)

1. Download the official Invesco QQQ holdings CSV from Invesco's site.
2. Save it as **`holdings/qqq-holdings.csv`** (this path).
3. Regenerate the example:

```bash
.venv/bin/python -m etf_screen.cli \
  --holdings holdings/qqq-holdings.csv \
  --consistency-years 3 --sector-context \
  --export md --out examples/qqq-$(date +%F).md
```

The loader finds the ticker column by name and tolerates preamble/footer rows —
see `etf_screen.constituents.holdings_from_csv`.
