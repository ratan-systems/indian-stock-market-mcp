# Data Files

## Public sample (synthetic)

`sample_equity_daily.csv` contains **synthetic** daily prices for `RELIANCE`,
`TCS`, and `INFY`: five consecutive weekday sessions each (2026-07-27 to
2026-07-31). It lets you try the server, run the tests, and see weekly
calculations without needing a market-data file.

The prices are made up. They come from a seeded pseudo-random walk in
[`scripts/generate_sample_data.py`](../scripts/generate_sample_data.py), so the
file is identical every time it is generated. No real market data is used, and
the values bear no relation to actual trading. The ticker symbols are real only
so the sample works with the bundled Nifty 50 universe. Do not use this file
for analysis.

The file is released under the repository's MIT license.

## One-year sample (synthetic)

`sample_equity_year.csv` has 260 weekday sessions (ending 2026-07-31) for
twelve Nifty 50 symbols. It is also synthetic, from the same generator with its
own seed. Use it to try the tools that need history: indicators, the 52-week
range, rankings over a period, screens, comparison and market breadth. The
stocks are given different average drifts so there are winners and losers, and
`SBIN`'s last session closes at a new high on heavy volume so the breakout
tools have something to find.

To regenerate or verify both files:

```bash
python scripts/generate_sample_data.py          # rewrite the sample files
python scripts/generate_sample_data.py --check  # fail if one is out of date
```

The test suite also fails if the committed file stops matching the generator.

## Columns

The sample uses these columns:

- `date`: trading date in `YYYY-MM-DD` format
- `symbol`: normalized equity symbol
- `open`, `high`, `low`, `close`: daily price values
- `volume`: daily traded volume

The server reads both parquet and CSV files.

## Using your own data

The sample is only for trying the server quickly. For real use, set
`INDIAN_STOCK_DATA_PATH` to your own local CSV or parquet file. You are
responsible for the source and license of any data you use.
