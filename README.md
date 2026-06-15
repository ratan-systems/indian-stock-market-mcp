# indian-stock-market-mcp

Plug-and-play MCP server for Indian stock market research and backtesting.

This project is MCP-first: the product is the server itself, designed to work
with Claude, Codex, Cursor, and other MCP-compatible clients without requiring
OpenAI or Anthropic API keys internally.

## Goals

- Provide a small, demoable v1
- Focus on Indian market research workflows
- Expose clean MCP tools instead of agent-specific logic
- Stay easy to run locally

## Planned v1

- Symbol lookup for Indian equities
- Basic company snapshot and market data tools
- Historical OHLCV fetch for backtesting inputs
- Simple backtest runner for strategy experiments
- Example client configuration and usage

## Project Structure

```text
data/
docs/screenshots/
examples/
src/indian_stock_market_mcp/
tests/
```

## Status

Repository initialized. v1 scope and first implementation tasks are being
defined.
