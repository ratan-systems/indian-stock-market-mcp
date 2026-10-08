# MCP Usage Examples

These files configure the same local stdio server. Replace the executable path
and `INDIAN_STOCK_DATA_PATH` with paths on your computer.

| Client | Configuration file | Where to use it |
| --- | --- | --- |
| Codex | [`codex-config.toml`](codex-config.toml) | Copy the server block into `~/.codex/config.toml` |
| Claude Code | [`claude-code.mcp.json`](claude-code.mcp.json) | Merge the server block into your Claude MCP configuration |
| Cursor | [`cursor-mcp.json`](cursor-mcp.json) | Copy it to `.cursor/mcp.json` for one project, or `~/.cursor/mcp.json` globally |
| Another stdio client | [`generic-stdio-mcp.json`](generic-stdio-mcp.json) | Use it if the client accepts an `mcpServers` JSON object |

## Example Prompts

Use normal research requests. The MCP client decides which registered tool to
call.

| Goal | Prompt |
| --- | --- |
| Check the dataset | `Is my market-data file healthy, and which analyses can it support?` |
| Validate a ticker | `Is RELIANCE available in my configured market data?` |
| Get recent prices | `Show the latest five sessions for RELIANCE.` |
| Calculate one return | `What was INFY's five-session performance?` |
| Rank Nifty performers | `Show the top five Nifty 50 weekly performers and skipped symbols.` |
| Inspect dataset coverage | `Which symbols are available in the configured market-data file?` |
| Inspect Nifty coverage | `Which symbols are in the bundled Nifty 50 universe?` |

## Representative Responses

Ticker validation:

```json
{
  "valid": true,
  "ticker": "RELIANCE",
  "message": "Ticker is available"
}
```

Five-session performance:

```json
{
  "symbol": "INFY",
  "start_date": "2026-07-27",
  "end_date": "2026-07-31",
  "start_close": 1544.1,
  "end_close": 1618.76,
  "return_percent": 4.835,
  "session_count": 5
}
```

Weekly ranking response shape:

```json
{
  "top_n": 2,
  "rankings": [
    {
      "symbol": "HDFCBANK",
      "start_date": "2026-07-27",
      "end_date": "2026-07-31",
      "start_close": 1000.0,
      "end_close": 1210.2,
      "return_percent": 21.02,
      "session_count": 5
    }
  ],
  "skipped": []
}
```

Actual prices, dates, rankings, and skipped symbols depend on the local data
file. The values above illustrate response structure only.

## Complete Research Workflow

1. Check that the dataset is healthy and see which analyses it supports.
2. Ask which symbols are available in the configured market-data file.
3. Validate the ticker you want to research, for example `RELIANCE`.
4. Request its latest five sessions to inspect the OHLCV records.
5. Request its five-session performance to get the close-to-close return.
6. Request the top five Nifty 50 weekly performers to compare the ticker with
   the configured Nifty 50 universe.
7. Check `skipped` before drawing conclusions from a ranking. A skipped ticker
   may have missing data, fewer than five sessions, or an invalid return.

This workflow is research support, not a trading recommendation. Confirm data
freshness and corporate-action treatment before relying on a result.
