import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .data import get_available_universe as get_available_universe_data
from .data import get_nifty50_universe as get_nifty50_universe_data
from .data import get_recent_price_history
from .data import get_weekly_performance as get_weekly_performance_data
from .data import rank_weekly_performers as rank_weekly_performers_data
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
def rank_weekly_performers(top_n: int = 5) -> dict:
    """Rank Nifty 50 stocks by weekly price change, best to worst.
    Also lists any symbols that were skipped because they didn't have
    enough recent data."""
    return rank_weekly_performers_data(top_n)


@mcp.tool()
def validate_ticker(symbol: str) -> dict:
    """Check whether an Indian equity ticker is available."""
    return validate_ticker_data(symbol)


@mcp.tool()
def get_stock_weekly_return(symbol: str) -> dict:
    """Return how much one stock's price changed over the last week
    (5 trading days), as a percentage."""
    return get_weekly_performance_data(symbol)


@mcp.tool()
def get_available_universe() -> dict[str, Any]:
    """List every stock symbol available in the currently configured
    dataset."""
    symbols = get_available_universe_data()
    return {
        "universe": "configured_dataset",
        "count": len(symbols),
        "symbols": symbols,
    }


@mcp.tool()
def get_nifty50_universe() -> dict[str, Any]:
    """List all 50 stock symbols in the Nifty 50 index."""
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
