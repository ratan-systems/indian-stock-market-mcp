import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import settings as settings_store
from . import watchlists as watchlist_store
from .adjustments import adjustment_warnings
from .analysis import (
    indicator_report,
    scan_year_ranges,
    short_range_warning,
    year_range_report,
)
from .data import (
    dataset_as_of,
    dataset_source,
    diagnose_dataset,
    get_adjustment_info,
    get_recent_price_history,
    load_selection_rows,
    load_symbol_history,
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
WATCHLIST_FORMAT = "json"
SETTINGS_FORMAT = "json"

SKIP_CODES = {
    "symbol_not_found": WarningCode.SYMBOL_NOT_FOUND,
    "short_history": WarningCode.SHORT_HISTORY,
    "stale_data": WarningCode.STALE_DATA,
}
MAX_RESULTS_LIMIT = 500


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


def dataset_symbol_set() -> set[str] | None:
    """The symbols in the configured dataset, or None when it cannot be read."""
    try:
        return set(get_available_universe_data())
    except (ValueError, OSError):
        return None


def watchlist_source() -> dict:
    """Describe the watchlists file by name only, never its full path."""
    return {
        "kind": "local_file",
        "file_name": watchlist_store.get_watchlist_path().name,
        "format": WATCHLIST_FORMAT,
    }


def settings_source() -> dict:
    """Describe the settings file by name only, never its full path."""
    return {
        "kind": "local_file",
        "file_name": settings_store.get_settings_path().name,
        "format": SETTINGS_FORMAT,
    }


def watchlist_symbol_warnings(symbols: list[str], *, report_unchecked: bool) -> list:
    """Warn about symbols that the configured dataset does not contain."""
    dataset = dataset_symbol_set()
    if dataset is None:
        if not report_unchecked:
            return []
        return [
            make_warning(
                WarningCode.SYMBOLS_UNCHECKED,
                "Symbols were not checked because no dataset could be read.",
            )
        ]
    missing = [symbol for symbol in symbols if symbol not in dataset]
    if not missing:
        return []
    return [
        make_warning(
            WarningCode.SYMBOL_NOT_FOUND,
            f"{len(missing)} symbol(s) are not in the dataset.",
            missing[:MAX_LISTED_MISSING],
        )
    ]


def watchlist_data(watchlist: watchlist_store.Watchlist) -> dict:
    return {
        "id": watchlist.id,
        "name": watchlist.name,
        "symbols": list(watchlist.symbols),
        "symbol_count": len(watchlist.symbols),
        "created": watchlist.created.isoformat(),
        "updated": watchlist.updated.isoformat(),
    }


@mcp.tool()
def get_price_history(
    symbol: str,
    sessions: int = 5,
) -> Envelope:
    """Return recent daily OHLCV data for an Indian equity symbol, as the
    exchange published it. Prices are not adjusted for splits or bonuses here;
    the weekly return, ranking and indicator tools do adjust them."""
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
    """Rank the stocks of an index universe (default Nifty 50) or of a saved
    watchlist by weekly price change, best to worst. Use list_universes to
    see the bundled universes and get_watchlists for your watchlists. Also lists any symbols that were skipped because they were
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
    bundled = load_universes().get(result["universe"])
    warnings += outdated_universe_warnings([bundled] if bundled else [])
    warnings += adjustment_warnings(
        get_adjustment_info(), [row["symbol"] for row in result["rankings"]]
    )
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
        warnings=adjustment_warnings(get_adjustment_info(), [result["symbol"]]),
    )


@mcp.tool()
def get_stock_indicators(
    symbol: str,
    ema_periods: list[int] | None = None,
    rsi_period: int | None = None,
    atr_period: int | None = None,
    traded_value_sessions: int | None = None,
) -> Envelope:
    """Return the latest technical indicators for one stock: EMAs and how far
    the close is above or below each, RSI, ATR (in rupees and as a percent of
    the close) and average daily traded value (close x volume, in rupees and
    crore). Periods come from the saved indicator settings unless given here
    for this one question. An indicator without enough history is null and
    explained in a short_history warning. Warns when the stock is illiquid."""
    settings = settings_store.effective_settings(
        ema_periods=ema_periods,
        rsi_period=rsi_period,
        atr_period=atr_period,
        traded_value_sessions=traded_value_sessions,
    )
    history = load_symbol_history(symbol)
    normalized = normalize_symbol(symbol)
    data, warnings = indicator_report(normalized, history, settings)
    warnings += adjustment_warnings(get_adjustment_info(), [normalized])
    used = {
        name: settings[name]
        for name in (
            "ema_periods",
            "rsi_period",
            "atr_period",
            "traded_value_sessions",
            "illiquid_below_crore",
        )
    }
    return build_response(
        data,
        source=dataset_source(),
        as_of=data["as_of"],
        parameters={"symbol": normalized, **used},
        warnings=warnings,
    )


def year_scan(
    universe: str, year_sessions: int
) -> tuple[Any, list[dict], list[dict], str | None]:
    """Scan a universe or watchlist for 52-week ranges.

    Returns the selection, the range records, the skipped symbols and the
    latest date in the data.
    """
    chosen, rows_by_symbol = load_selection_rows(universe)
    records, skipped, as_of = scan_year_ranges(
        chosen.symbols, rows_by_symbol, year_sessions
    )
    return chosen, records, skipped, as_of


def scan_warnings(
    chosen: Any, skipped: list[dict], shown: list[dict], year_sessions: int
) -> list:
    """Warnings for a list of stocks: what was skipped, which of the stocks
    shown have under a year of history, outdated lists and adjustment gaps."""
    by_code: dict[str, list[str]] = {}
    for entry in skipped:
        by_code.setdefault(entry["code"], []).append(entry["symbol"])
    warnings = [
        make_warning(
            SKIP_CODES[code],
            f"{len(symbols)} symbol(s) of {chosen.id} were skipped.",
            symbols,
        )
        for code, symbols in by_code.items()
    ]
    short = [row["symbol"] for row in shown if row["sessions"] < year_sessions]
    if short:
        warnings.append(short_range_warning(short, year_sessions))
    if chosen.universe is not None:
        warnings += outdated_universe_warnings([chosen.universe])
    warnings += adjustment_warnings(
        get_adjustment_info(), [row["symbol"] for row in shown]
    )
    return warnings


@mcp.tool()
def get_52_week_range(symbol: str, year_sessions: int | None = None) -> Envelope:
    """Return one stock's 52-week high and low (intraday extremes, with the
    dates they were reached), how far the latest close is below the high and
    above the low, and whether the latest session set a new high or low. The
    window is the saved year_sessions setting (default 252) unless given here.
    Warns when the stock has less than a year of history."""
    settings = settings_store.effective_settings(year_sessions=year_sessions)
    history = load_symbol_history(symbol)
    normalized = normalize_symbol(symbol)
    data, warnings = year_range_report(normalized, history, settings)
    warnings += adjustment_warnings(get_adjustment_info(), [normalized])
    return build_response(
        data,
        source=dataset_source(),
        as_of=data["as_of"],
        parameters={"symbol": normalized, "year_sessions": settings["year_sessions"]},
        warnings=warnings,
    )


@mcp.tool()
def find_near_52_week_high(
    universe: str = DEFAULT_UNIVERSE,
    within_percent: float | None = None,
    max_results: int = 50,
    year_sessions: int | None = None,
) -> Envelope:
    """List the stocks of a universe (default Nifty 50) or saved watchlist whose
    latest close is within a percentage of their 52-week high, closest first.
    within_percent defaults to the saved near_high_percent setting (5). Stocks
    whose data stops before the latest date are skipped as stale."""
    if not isinstance(max_results, int) or not 1 <= max_results <= MAX_RESULTS_LIMIT:
        raise ValueError(
            f"max_results must be an integer between 1 and {MAX_RESULTS_LIMIT}"
        )
    settings = settings_store.effective_settings(
        near_high_percent=within_percent, year_sessions=year_sessions
    )
    within = settings["near_high_percent"]
    chosen, records, skipped, as_of = year_scan(universe, settings["year_sessions"])
    matches = sorted(
        (row for row in records if row["percent_below_high"] <= within),
        key=lambda row: (row["percent_below_high"], row["symbol"]),
    )
    shown = matches[:max_results]
    return build_response(
        {
            "universe": chosen.id,
            "within_percent": within,
            "checked": len(records),
            "total_matches": len(matches),
            "truncated": len(matches) > len(shown),
            "matches": shown,
            "skipped": skipped,
        },
        source=dataset_source(),
        as_of=as_of,
        parameters={
            "universe": chosen.id,
            "within_percent": within,
            "max_results": max_results,
            "year_sessions": settings["year_sessions"],
        },
        warnings=scan_warnings(chosen, skipped, shown, settings["year_sessions"]),
    )


@mcp.tool()
def find_new_52_week_extremes(
    universe: str = DEFAULT_UNIVERSE,
    kind: str = "high",
    year_sessions: int | None = None,
) -> Envelope:
    """List the stocks of a universe (default Nifty 50) or saved watchlist that
    set a new 52-week high (kind="high") or a new 52-week low (kind="low") in
    the latest session. Stocks whose data stops before the latest date are
    skipped as stale."""
    if kind not in ("high", "low"):
        raise ValueError('kind must be "high" or "low"')
    settings = settings_store.effective_settings(year_sessions=year_sessions)
    chosen, records, skipped, as_of = year_scan(universe, settings["year_sessions"])
    matches = sorted(
        (row for row in records if row[f"is_new_{kind}"]),
        key=lambda row: row["symbol"],
    )
    return build_response(
        {
            "universe": chosen.id,
            "kind": kind,
            "checked": len(records),
            "count": len(matches),
            "matches": matches,
            "skipped": skipped,
        },
        source=dataset_source(),
        as_of=as_of,
        parameters={
            "universe": chosen.id,
            "kind": kind,
            "year_sessions": settings["year_sessions"],
        },
        warnings=scan_warnings(chosen, skipped, matches, settings["year_sessions"]),
    )


@mcp.tool()
def get_indicator_settings() -> Envelope:
    """Show the saved indicator settings (EMA, RSI and ATR periods, traded-value
    window, illiquid limit, one-year length, near-high distance, relative
    strength periods and default benchmarks), the built-in defaults, and which
    settings have been customised."""
    return build_response(
        settings_store.describe_settings(),
        source=settings_source(),
        as_of=None,
        parameters={},
    )


@mcp.tool()
def update_indicator_settings(changes: dict[str, Any]) -> Envelope:
    """Save new defaults for the analysis tools, for example
    {"rsi_period": 21, "ema_periods": [10, 30]}. Only the settings named are
    changed, each is checked against its allowed range, and a value set back
    to its default is no longer stored. Use get_indicator_settings to see the
    setting names and current values."""
    updated, customised = settings_store.update_settings(changes)
    return build_response(
        {"settings": updated, "customised": customised, "changed": sorted(changes)},
        source=settings_source(),
        as_of=None,
        parameters={"changes": changes},
    )


@mcp.tool()
def reset_indicator_settings(names: list[str] | None = None) -> Envelope:
    """Put indicator settings back to the built-in defaults: the named settings,
    or all of them when no names are given."""
    updated, reset = settings_store.reset_settings(names)
    return build_response(
        {
            "settings": updated,
            "reset": reset,
            "customised": sorted(
                name
                for name in updated
                if updated[name] != settings_store.DEFAULTS[name]
            ),
        },
        source=settings_source(),
        as_of=None,
        parameters={"names": names},
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
    """List the bundled index universes with their group (broad market, size
    segment such as Midcap 150 or Smallcap 250, or sector such as Bank, IT or
    Pharma), symbol counts and as-of dates. Any of these names works as a
    universe in the ranking tools. Warns about lists that are old and, when a
    dataset is configured, about universe symbols the dataset does not
    contain."""
    universes = list(load_universes().values())
    dataset_symbols = dataset_symbol_set()

    listed, gaps = [], []
    for universe in universes:
        entry = {
            "id": universe.id,
            "name": universe.name,
            "group": universe.group,
            "index_name": universe.index_name,
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
def create_watchlist(name: str, symbols: list[str]) -> Envelope:
    """Save a new watchlist: a named list of symbols. Every symbol is cleaned
    (for example tcs.ns becomes TCS) and repeats are dropped. Symbols not in
    the configured dataset are saved and reported in a warning. Names of the
    bundled universes cannot be used. A watchlist name can be used anywhere a
    universe name is accepted, such as rank_weekly_performers."""
    watchlist = watchlist_store.create_watchlist(name, symbols)
    return build_response(
        watchlist_data(watchlist),
        source=watchlist_source(),
        as_of=None,
        parameters={"name": name.strip(), "symbols": list(watchlist.symbols)},
        warnings=watchlist_symbol_warnings(
            list(watchlist.symbols), report_unchecked=True
        ),
    )


@mcp.tool()
def get_watchlists(name: str | None = None) -> Envelope:
    """Show one saved watchlist by name, or all of them when no name is given.
    Warns about symbols that are no longer in the configured dataset."""
    if name is None:
        chosen = list(watchlist_store.load_watchlists().values())
    else:
        chosen = [watchlist_store.get_watchlist(name)]
    symbols = list(dict.fromkeys(s for item in chosen for s in item.symbols))
    return build_response(
        {"watchlists": [watchlist_data(item) for item in chosen]},
        source=watchlist_source(),
        as_of=None,
        parameters={} if name is None else {"name": name.strip()},
        warnings=watchlist_symbol_warnings(symbols, report_unchecked=False),
    )


@mcp.tool()
def update_watchlist(
    name: str,
    add_symbols: list[str] | None = None,
    remove_symbols: list[str] | None = None,
    new_name: str | None = None,
) -> Envelope:
    """Change a saved watchlist: add symbols, remove symbols, rename it, or any
    combination in one call. Added symbols are cleaned and checked against the
    configured dataset like in create_watchlist. A watchlist cannot be emptied;
    delete it instead."""
    watchlist, added, not_found = watchlist_store.update_watchlist(
        name,
        add_symbols=add_symbols,
        remove_symbols=remove_symbols,
        new_name=new_name,
    )
    return build_response(
        {
            **watchlist_data(watchlist),
            "added": added,
            "not_in_watchlist": not_found,
        },
        source=watchlist_source(),
        as_of=None,
        parameters={
            "name": name.strip(),
            "add_symbols": add_symbols or [],
            "remove_symbols": remove_symbols or [],
            "new_name": new_name,
        },
        warnings=watchlist_symbol_warnings(added, report_unchecked=False),
    )


@mcp.tool()
def delete_watchlist(name: str) -> Envelope:
    """Permanently delete a saved watchlist. The symbols themselves are not
    affected; only the saved list is removed."""
    removed = watchlist_store.delete_watchlist(name)
    return build_response(
        {**watchlist_data(removed), "deleted": True},
        source=watchlist_source(),
        as_of=None,
        parameters={"name": name.strip()},
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
