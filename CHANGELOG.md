# Changelog

## [0.1.1] - Unreleased

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

### Changed

- The bundled sample dataset is now synthetic: generated prices from a fixed
  seed, not real market data. Regenerate it with
  `python scripts/generate_sample_data.py`.
- Dataset validation now also rejects non-positive prices, inconsistent OHLC
  rows, and non-numeric, non-finite, or negative volume.
- Tool descriptions were rewritten.
- The Nifty 50 list ships inside the package (`resources/nifty50.json`)
  instead of a duplicate copy in `data/`.
- Package description and keywords no longer mention backtesting, which is not
  supported.

## [0.1.0]

- Initial release: price history, ticker validation, five-session return,
  weekly performer ranking, and the available and Nifty 50 universes.
