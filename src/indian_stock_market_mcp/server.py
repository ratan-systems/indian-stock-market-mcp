import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .data import get_available_universe as get_available_universe_data
from .data import get_nifty50_universe as get_nifty50_universe_data
from .data import get_recent_price_history, rank_weekly_performers
from .data import get_weekly_performance as get_weekly_performance_data
from .data import (
    validate_ticker as validate_ticker_data,
)

mcp = FastMCP(
    "Indian Stock Market MCP",
    instructions=(
        "Provides Indian equity price history, ticker validation, "
        "and Nifty 50 weekly performance rankings from configured market data."
    ),
)
@mcp.tool()
def get_price_history(
    symbol: str,
    sessions: int = 5,
) -> dict[str, Any]:
    """Return recent daily OHLCV data for an Indian equity symbol."""
    data = get_recent_price_history(symbol, sessions).copy()
    data["date"] = data["date"].dt.strftime("%Y-%m-%d")
    prices = json.loads(data.to_json(orient="records"))

    return {
        "symbol": symbol.strip().upper(),
        "session_requested": sessions,
        "session_returned": len(prices),
        "prices": prices,
    }


@mcp.tool()
def get_weekly_performance_summary(top_n: int = 5) -> dict:
    """Return the top weekly performers and skipped Nifty 50 symbols."""
    return rank_weekly_performers(top_n)


@mcp.tool()
def validate_ticker(symbol: str) -> dict:
    """Check whether an Indian equity ticker is available."""
    return validate_ticker_data(symbol)

@mcp.tool()
def get_weekly_performance(symbol: str) -> dict:
    """Return the top weekly performance of a stock"""
    return get_weekly_performance_data(symbol)


@mcp.tool()
def get_available_universe() -> dict[str, Any]:
    """Return all normalized symbols found in the configured market data."""
    symbols = get_available_universe_data()
    return {
        "universe": "configured_dataset",
        "count": len(symbols),
        "symbols": symbols,
    }


@mcp.tool()
def get_nifty50_universe() -> dict[str, Any]:
    """Return the normalized Nifty 50 symbol universe."""
    symbols = get_nifty50_universe_data()
    return {
        "universe": "NIFTY_50",
        "count": len(symbols),
        "symbols": symbols,
    }



def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
