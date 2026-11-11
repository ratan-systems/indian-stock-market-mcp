# Changelog

## [Unreleased]

### Breaking changes

- The Nifty 50 list moved from `resources/nifty50.json` into
  `resources/universes.json`, which holds twelve universes, each with an as-of
  date. The Nifty 50 list was refreshed from NSE: `BSE` joins and `WIPRO`
  leaves, so rankings change.
- `rank_weekly_performers` returns the universe it ranked in a new `universe`
  field.
- Every tool now returns a standard envelope (`envelope_version`, `data`,
  `source`, `as_of`, `parameters`, `warnings`). The previous response of each
  tool is now `data`. `get_price_history` no longer returns `session_requested`
  (see `parameters.sessions`). `get_data_capabilities` names its `source` block
  `dataset`, and its warnings are the envelope's `warnings`. Skipped symbols in
  `rank_weekly_performers` gain a `code` field.

### Added

- `get_stock_indicators` tool: EMA (default 20, 50, 200), RSI (14, Wilder),
  ATR (14, Wilder) and average traded value (20 sessions) for one stock, with
  `short_history`, `illiquid` and `volume_all_missing` warnings. The formulas
  come from TA-Lib, which is now a dependency (pre-built wheels for Python
  3.11+ on macOS, Linux and Windows).
- Saved indicator settings, changed through `get_indicator_settings`,
  `update_indicator_settings` and `reset_indicator_settings` and stored in
  `~/.indian-stock-market-mcp/indicator_settings.json` (or the path in
  `INDIAN_STOCK_SETTINGS_PATH`). Every value is range-checked, and tool
  arguments override a setting for one question.
- Saved watchlists: `create_watchlist`, `get_watchlists`, `update_watchlist`
  and `delete_watchlist`, stored in a local JSON file
  (`~/.indian-stock-market-mcp/watchlists.json`, or the path in
  `INDIAN_STOCK_WATCHLIST_PATH`). Symbols are cleaned on save and any not in the
  dataset are reported. A watchlist name works wherever a universe name does
  (`rank_weekly_performers`), and universe names are reserved.
- Eleven more bundled universes (Nifty Next 50, 100, 200, 500, Bank, IT,
  Pharma, Auto, FMCG, Metal, Energy), built by `scripts/build_universes.py`
  from NSE Indices lists.
- `list_universes` tool, and a `universe` argument on
  `rank_weekly_performers`. Warnings for universe lists older than 180 days
  and for universe symbols missing from the dataset.
- `get_data_quality_report` tool: finds duplicate rows, impossible prices,
  one-day moves beyond a threshold (default 25%), missing trading days, stale
  symbols and short histories, and gives a `backtest_ready` verdict with
  reasons. Thresholds are tool parameters.
- `indian-stock-market-mcp import-bhavcopy <folder>` merges daily NSE or BSE
  bhavcopy files (current common layout, `.csv` or `.csv.zip`) into one Parquet
  file, keeping equity series, skipping dates already imported and printing a
  summary. Running the command with no arguments still starts the server.
- Symbol cleanup for every tool that takes a symbol: spaces, case and the
  `.NS`, `.BO`, `NSE:` and `BSE:` forms are accepted.
- A bundled mapping of Nifty 50 BSE scrip codes to NSE symbols, built by
  `scripts/build_bse_codes.py`. Symbol cleanup uses it, so a BSE scrip code such
  as `500325` is accepted anywhere a symbol is and means `RELIANCE`. A number
  that is not in the mapping is still treated as an unknown symbol.
- Registered warning codes; a short history, an unknown symbol in
  `validate_ticker` or a skipped ranking symbol now produce warnings.

### Fixed

- `rank_weekly_performers` loads and validates the dataset once instead of once
  per symbol, so ranking large universes stays fast.
- `scripts/verify_installed.py` (the installed-package check run in CI) now
  matches the envelope responses and the current tool list.

## [0.1.1] - 2026-10-08

### Breaking changes

- Renamed `get_weekly_performance_summary` to `rank_weekly_performers`.
- Renamed `get_weekly_performance` to `get_stock_weekly_return`.
- Arguments and response shapes are unchanged. The old names were removed
  without a deprecation period; see [Migrating from v0.1.0](README.md#migrating-from-v010).

### Added

- `get_data_capabilities` tool: reports dataset readability, schema, row and
  symbol counts, date range, data-quality problems, available analyses
  (including volume), and warnings such as unknown price-adjustment status.
- MCP-level tests for tool inventory, schemas, defaults, boundaries, success
  responses, and failure behavior.
- Python 3.12 and 3.13 classifiers; MIT license metadata.

### Fixed

- Removed a duplicate copy of the Nifty 50 list from `data/`, so the list that
  ships inside the package is the only source.

### Changed

- The bundled sample dataset is now synthetic: generated prices from a fixed
  seed, not real market data. Regenerate it with
  `python scripts/generate_sample_data.py`.
- Dataset validation now also rejects non-positive prices, inconsistent OHLC
  rows, and non-numeric, non-finite, or negative volume.
- Tool descriptions were rewritten.
- The README demo screenshot was retaken to show the current tool names and the
  synthetic sample data.
- The Nifty 50 list ships inside the package (`resources/nifty50.json`)
  instead of a duplicate copy in `data/`.
- Package description and keywords no longer mention backtesting, which is not
  supported.

### Compatibility

- Python 3.11, 3.12, and 3.13 (tested in CI on Ubuntu).
- MCP Python SDK `mcp>=1,<2`.
- The two renamed tools are the only breaking change; see the migration table
  in the README.

### Known limitations

- Reads local daily OHLCV files only; no live prices, fundamentals, news, or
  order execution.
- Weekly return and Nifty 50 ranking are the only analyses.
- Price-adjustment status for splits and dividends is unknown and is always
  reported as a warning.
- The bundled sample data is synthetic and for demonstration only.
- CI covers Ubuntu only; macOS and Windows are not yet tested.
- Distributed as a GitHub Release, not on PyPI.

## [0.1.0]

- Initial release: price history, ticker validation, five-session return,
  weekly performer ranking, and the available and Nifty 50 universes.
