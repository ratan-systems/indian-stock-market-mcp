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
from .quality import (
    DEFAULT_JUMP_THRESHOLD_PERCENT,
    DEFAULT_MIN_HISTORY_SESSIONS,
    DEFAULT_STALE_AFTER_SESSIONS,
    get_quality_report,
)
from .symbols import normalize_symbol
from .universes import (
    DEFAULT_UNIVERSE,
    STALE_AFTER_DAYS,
    Universe,
    get_universe,
    load_universes,
    universe_age_days,
)

mcp = FastMCP(
    "Indian Stock Market MCP",
    instructions=(
        "Provides Indian equity price history, ticker validation, "
        "Nifty 50 weekly performance rankings, and dataset diagnostics "
        "from configured market data. Call get_data_capabilities first to "
        "check whether the dataset is usable."
    ),
)


UNIVERSES_SOURCE = {"kind": "bundled", "file_name": "universes.json", "format": "json"}
MAX_LISTED_MISSING = 20

SKIP_CODES = {
    "symbol_not_found": WarningCode.SYMBOL_NOT_FOUND,
    "short_history": WarningCode.SHORT_HISTORY,
}


def outdated_universe_warnings(universes: list[Universe]) -> list:
    """Warn about universe lists older than STALE_AFTER_DAYS."""
    old = [u for u in universes if universe_age_days(u) > STALE_AFTER_DAYS]
    if not old:
        return []
    detail = ", ".join(f"{u.id} ({universe_age_days(u)} days)" for u in old)
    return [
        make_warning(
            WarningCode.UNIVERSE_OUTDATED,
            f"Universe list(s) older than {STALE_AFTER_DAYS} days: {detail}. "
            "Index membership changes about twice a year.",
        )
    ]


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
def rank_weekly_performers(
    top_n: int = 5, universe: str = DEFAULT_UNIVERSE
) -> Envelope:
    """Rank the stocks of an index universe (default Nifty 50) by weekly
    price change, best to worst. Use list_universes to see the available
    universes. Also lists any symbols that were skipped because they were
    not in the dataset or didn't have enough recent data."""
    result = rank_weekly_performers_data(top_n, universe)
    by_code: dict[str, list[str]] = {}
    for skipped in result["skipped"]:
        by_code.setdefault(skipped["code"], []).append(skipped["symbol"])

    warnings = [
        make_warning(
            SKIP_CODES[code],
            f"{len(symbols)} symbol(s) of {result['universe']} were skipped "
            "from the ranking.",
            symbols,
        )
        for code, symbols in by_code.items()
    ]
    warnings += outdated_universe_warnings([get_universe(result["universe"])])
    end_dates = [row["end_date"] for row in result["rankings"]]
    return build_response(
        result,
        source=dataset_source(),
        as_of=max(end_dates) if end_dates else None,
        parameters={"top_n": top_n, "universe": result["universe"]},
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
    universe = get_universe("NIFTY_50")
    symbols = get_nifty50_universe_data()
    return build_response(
        {"universe": universe.id, "count": len(symbols), "symbols": symbols},
        source=UNIVERSES_SOURCE,
        as_of=universe.as_of.isoformat(),
        parameters={},
        warnings=outdated_universe_warnings([universe]),
    )


@mcp.tool()
def list_universes() -> Envelope:
    """List the bundled index universes (Nifty 50, Next 50, 100, 200, 500,
    Bank and the main sector indices) with their symbol counts and as-of
    dates. Warns about lists that are old and, when a dataset is configured,
    about universe symbols the dataset does not contain."""
    universes = list(load_universes().values())
    try:
        dataset_symbols = set(get_available_universe_data())
    except (ValueError, OSError):
        dataset_symbols = None

    listed, gaps = [], []
    for universe in universes:
        entry = {
            "id": universe.id,
            "name": universe.name,
            "symbol_count": len(universe.symbols),
            "as_of": universe.as_of.isoformat(),
            "age_days": universe_age_days(universe),
            "outdated": universe_age_days(universe) > STALE_AFTER_DAYS,
        }
        if dataset_symbols is not None:
            missing = [s for s in universe.symbols if s not in dataset_symbols]
            entry["symbols_in_dataset"] = len(universe.symbols) - len(missing)
            entry["symbols_missing_from_dataset"] = len(missing)
            entry["missing_sample"] = missing[:MAX_LISTED_MISSING]
            if missing:
                gaps.append(f"{universe.id} {len(missing)}")
        listed.append(entry)

    warnings = outdated_universe_warnings(universes)
    if gaps:
        warnings.append(
            make_warning(
                WarningCode.UNIVERSE_SYMBOLS_MISSING,
                "Universe symbols missing from the dataset: " + ", ".join(gaps) + ".",
            )
        )
    return build_response(
        {
            "universes": listed,
            "dataset_checked": dataset_symbols is not None,
            "outdated_after_days": STALE_AFTER_DAYS,
        },
        source=UNIVERSES_SOURCE,
        as_of=None,
        parameters={},
        warnings=warnings,
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


@mcp.tool()
def get_data_quality_report(
    jump_threshold_percent: float = DEFAULT_JUMP_THRESHOLD_PERCENT,
    min_history_sessions: int = DEFAULT_MIN_HISTORY_SESSIONS,
    stale_after_sessions: int = DEFAULT_STALE_AFTER_SESSIONS,
) -> Envelope:
    """Check whether the configured dataset can be trusted for research and
    backtesting. Finds duplicate rows, impossible prices, one-day moves beyond
    the jump threshold (often unadjusted splits or bonuses), missing trading
    days, stale symbols and short histories, and gives a backtest_ready
    verdict with reasons. Scans every symbol, so it is slower than
    get_data_capabilities."""
    report = get_quality_report(
        jump_threshold_percent=jump_threshold_percent,
        min_history_sessions=min_history_sessions,
        stale_after_sessions=stale_after_sessions,
    )
    warnings = [
        make_warning(
            WarningCode(finding["code"]), finding["message"], finding.get("symbols")
        )
        for finding in report["findings"]
    ]
    date_range = report["summary"]["date_range"]
    return build_response(
        report,
        source=dataset_source(),
        as_of=date_range["end"] if date_range else None,
        parameters=report["settings"],
        warnings=warnings,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
