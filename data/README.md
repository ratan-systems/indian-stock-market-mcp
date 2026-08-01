# Data Files

## Public sample

`sample_equity_daily.csv` is a small fixture generated from the local parquet
snapshot. It contains five recent sessions for `RELIANCE`, `TCS`, and `INFY`,
so the repository can demonstrate symbol filtering and weekly calculations
without publishing the complete dataset.

## Columns

The sample uses these columns:

- `date`: trading date in `YYYY-MM-DD` format
- `symbol`: normalized equity symbol
- `open`, `high`, `low`, `close`: daily price values
- `volume`: daily traded volume

The server currently reads parquet files. The CSV is a small public fixture for
examples and tests until CSV input support is added.

## Price adjustment status

The source parquet metadata does not identify the prices as adjusted or
unadjusted. The adjustment status is therefore **unknown** and must not be
assumed. Any backtest using this sample should document this limitation and
verify corporate-action treatment before relying on long-term returns.

## Full local snapshot

The complete parquet dataset is intentionally not included in this v1
repository. Publishing it is a final release task, pending confirmation of
redistribution rights and a decision about large-file hosting. The runtime
configuration can still point to a user's local parquet file.
