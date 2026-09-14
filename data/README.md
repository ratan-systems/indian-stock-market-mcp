# Data Files

## Public sample

`sample_equity_daily.csv` contains five recent sessions for `RELIANCE`,
`TCS`, and `INFY`, so the repository can demonstrate symbol filtering and
weekly calculations without publishing the complete dataset.

## Columns

The sample uses these columns:

- `date`: trading date in `YYYY-MM-DD` format
- `symbol`: normalized equity symbol
- `open`, `high`, `low`, `close`: daily price values
- `volume`: daily traded volume

The server reads both parquet and CSV files. The CSV is a small public fixture
for examples, tests, and a quick first MCP configuration.

## Full local snapshot

`sample_equity_daily.csv` is a small demo dataset, included only to try the
server quickly. It is not the full historical dataset. For real use, set
`INDIAN_STOCK_DATA_PATH` to point at your own local CSV or parquet file.
