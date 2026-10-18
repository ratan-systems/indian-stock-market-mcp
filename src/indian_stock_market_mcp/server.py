import json

from mcp.server.fastmcp import FastMCP

from .data import (
    dataset_as_of,
    dataset_source,
    diagnose_dataset,
    get_recent_price_history,
)
from .data import get_available_universe as get_available_universe_data
from .data import get_nifty50_universe as get_nifty50_universe_data
from .data import get_weekly_performance as get_weekly_performance_data
from .data import rank_weekly_performers as rank_weekly_performers_data
from .data import (
    validate_ticker as validate_ticker_data,
)
from .envelope import Envelope, WarningCode, build_response, make_warning
from .symbols import normalize_symbol

mcp = FastMCP(
    "Indian Stock Market MCP",
    instructions=(
        "Provides Indian equity price history, ticker validation, "
        "Nifty 50 weekly performance rankings, and dataset diagnostics "
        "from configured market data. Call get_data_capabilities first to "
        "check whether the dataset is usable."
    ),
)


NIFTY50_SOURCE = {"kind": "bundled", "file_name": "nifty50.json", "format": "json"}

SKIP_CODES = {
    "symbol_not_found": WarningCode.SYMBOL_NOT_FOUND,
    "short_history": WarningCode.SHORT_HISTORY,
}


@mcp.tool()
def get_price_history(
    symbol: str,
    sessions: int = 5,
) -> Envelope:
    """Return recent daily OHLCV data for an Indian equity symbol."""
    frame = get_recent_price_history(symbol, sessions)
    as_of = frame["date"].max().strftime("%Y-%m-%d")
    data = frame.copy()
    data["date"] = data["date"].dt.strftime("%Y-%m-%d")
    prices = json.loads(data.to_json(orient="records"))
    normalized = normalize_symbol(symbol)

    warnings = []
    if len(prices) < sessions:
        warnings.append(
            make_warning(
                WarningCode.SHORT_HISTORY,
                f"Only {len(prices)} of {sessions} requested sessions are available.",
                [normalized],
            )
        )

    return build_response(
        {"symbol": normalized, "session_returned": len(prices), "prices": prices},
        source=dataset_source(),
        as_of=as_of,
        parameters={"symbol": normalized, "sessions": sessions},
        warnings=warnings,
    )


@mcp.tool()
def rank_weekly_performers(top_n: int = 5) -> Envelope:
    """Rank Nifty 50 stocks by weekly price change, best to worst.
    Also lists any symbols that were skipped because they didn't have
    enough recent data."""
    result = rank_weekly_performers_data(top_n)
    by_code: dict[str, list[str]] = {}
    for skipped in result["skipped"]:
        by_code.setdefault(skipped["code"], []).append(skipped["symbol"])

    warnings = [
        make_warning(
            SKIP_CODES[code],
            f"{len(symbols)} Nifty 50 symbol(s) were skipped from the ranking.",
            symbols,
        )
        for code, symbols in by_code.items()
    ]
    end_dates = [row["end_date"] for row in result["rankings"]]
    return build_response(
        result,
        source=dataset_source(),
        as_of=max(end_dates) if end_dates else None,
        parameters={"top_n": top_n},
        warnings=warnings,
    )


@mcp.tool()
def validate_ticker(symbol: str) -> Envelope:
    """Check whether an Indian equity ticker is available."""
    result = validate_ticker_data(symbol)
    warnings = []
    if not result["valid"]:
        warnings.append(
            make_warning(
                WarningCode.SYMBOL_NOT_FOUND,
                result["message"],
                [result["ticker"]],
            )
        )
    return build_response(
        result,
        source=dataset_source(),
        as_of=dataset_as_of(),
        parameters={"symbol": result["ticker"]},
        warnings=warnings,
    )


@mcp.tool()
def get_stock_weekly_return(symbol: str) -> Envelope:
    """Return how much one stock's price changed over the last week
    (5 trading days), as a percentage."""
    result = get_weekly_performance_data(symbol)
    return build_response(
        result,
        source=dataset_source(),
        as_of=result["end_date"],
        parameters={"symbol": result["symbol"]},
    )


@mcp.tool()
def get_available_universe() -> Envelope:
    """List every stock symbol available in the currently configured
    dataset."""
    symbols = get_available_universe_data()
    return build_response(
        {"universe": "configured_dataset", "count": len(symbols), "symbols": symbols},
        source=dataset_source(),
        as_of=dataset_as_of(),
        parameters={},
    )


@mcp.tool()
def get_nifty50_universe() -> Envelope:
    """List all 50 stock symbols in the Nifty 50 index."""
    symbols = get_nifty50_universe_data()
    return build_response(
        {"universe": "NIFTY_50", "count": len(symbols), "symbols": symbols},
        source=NIFTY50_SOURCE,
        as_of=None,
        parameters={},
    )


@mcp.tool()
def get_data_capabilities() -> Envelope:
    """Diagnose the configured dataset: whether it is readable, its format,
    columns, row and symbol counts, date range, data-quality problems, which
    analyses are available (including volume), and warnings such as unknown
    price-adjustment status. Returns status healthy, incomplete or invalid."""
    report = diagnose_dataset()
    warnings = report.pop("warnings")
    dataset = report.pop("source")
    date_range = report["summary"]["date_range"]
    return build_response(
        {**report, "dataset": dataset},
        source={
            "kind": "dataset",
            "file_name": dataset["file_name"],
            "format": dataset["format"],
        },
        as_of=date_range["end"] if date_range else None,
        parameters={},
        warnings=warnings,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
