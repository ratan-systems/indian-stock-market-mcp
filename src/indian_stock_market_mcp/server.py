import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .data import (
    get_recent_price_history,
    rank_weekly_performers,
)
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
