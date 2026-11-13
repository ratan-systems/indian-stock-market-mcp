"""Per-stock reports built from indicators and settings.

These functions take a stock's validated history and the settings in force and
return the tool's data plus its warnings. They read no files, so the tools stay
thin and the numbers can be tested without a dataset.
"""

from typing import Any

import pandas as pd

from .benchmarks import Benchmark, BenchmarkBook, BenchmarkError
from .envelope import ResponseWarning, WarningCode, make_warning
from .indicators import (
    InsufficientHistoryError,
    SeriesCoverageError,
    YearRange,
    annualized_volatility,
    atr,
    average_traded_value,
    ema,
    large_moves,
    level_return,
    max_drawdown,
    period_return,
    rsi,
    year_range,
)
from .quality import DEFAULT_JUMP_THRESHOLD_PERCENT

CRORE = 10_000_000
PRICE_DECIMALS = 4
PERCENT_DECIMALS = 2


def rounded(value: float, decimals: int = PRICE_DECIMALS) -> float:
    return round(float(value), decimals)


def indicator_report(
    symbol: str, history: pd.DataFrame, settings: dict[str, Any]
) -> tuple[dict[str, Any], list[ResponseWarning]]:
    """Latest EMA, RSI, ATR and average traded value for one stock.

    An indicator without enough history is reported as null and named in one
    short_history warning; it never stops the others from being calculated.
    """
    close = history["close"]
    latest_close = float(close.iloc[-1])
    short: list[str] = []
    warnings: list[ResponseWarning] = []

    def latest(name: str, calculate) -> float | None:
        try:
            return float(calculate().iloc[-1])
        except InsufficientHistoryError as error:
            short.append(f"{name} ({error})")
            return None

    emas = []
    for period in settings["ema_periods"]:
        value = latest(f"EMA {period}", lambda p=period: ema(close, p))
        emas.append(
            {
                "period": period,
                "value": None if value is None else rounded(value),
                "close_vs_ema_percent": None
                if value is None
                else rounded((latest_close / value - 1) * 100, PERCENT_DECIMALS),
            }
        )

    rsi_value = latest(
        f"RSI {settings['rsi_period']}", lambda: rsi(close, settings["rsi_period"])
    )
    atr_value = latest(
        f"ATR {settings['atr_period']}",
        lambda: atr(history["high"], history["low"], close, settings["atr_period"]),
    )

    traded = None
    sessions = settings["traded_value_sessions"]
    if history["volume"].notna().any():
        try:
            traded = average_traded_value(close, history["volume"], sessions)
        except InsufficientHistoryError as error:
            short.append(f"average traded value {sessions} ({error})")
    else:
        warnings.append(
            make_warning(
                WarningCode.VOLUME_ALL_MISSING,
                "This stock has no volume data, so average traded value is not available.",
                [symbol],
            )
        )

    if short:
        warnings.append(
            make_warning(
                WarningCode.SHORT_HISTORY,
                f"Not enough history for: {'; '.join(short)}.",
                [symbol],
            )
        )
    crore = None if traded is None else traded / CRORE
    if crore is not None and crore < settings["illiquid_below_crore"]:
        warnings.append(
            make_warning(
                WarningCode.ILLIQUID,
                f"Average traded value is Rs {crore:.2f} crore a day, below the "
                f"Rs {settings['illiquid_below_crore']:g} crore illiquid limit.",
                [symbol],
            )
        )

    data = {
        "symbol": symbol,
        "as_of": history["date"].iloc[-1].strftime("%Y-%m-%d"),
        "close": rounded(latest_close),
        "sessions_available": len(history),
        "ema": emas,
        "rsi": {
            "period": settings["rsi_period"],
            "value": None
            if rsi_value is None
            else rounded(rsi_value, PERCENT_DECIMALS),
        },
        "atr": {
            "period": settings["atr_period"],
            "value": None if atr_value is None else rounded(atr_value),
            "percent_of_close": None
            if atr_value is None
            else rounded(atr_value / latest_close * 100, PERCENT_DECIMALS),
        },
        "average_traded_value": {
            "sessions": sessions,
            "rupees": None if traded is None else rounded(traded, 0),
            "crore": None if crore is None else rounded(crore, PERCENT_DECIMALS),
        },
    }
    return data, warnings


def year_range_row(symbol: str, range_: YearRange) -> dict[str, Any]:
    """One stock's 52-week range as a JSON-safe record."""
    return {
        "symbol": symbol,
        "close": rounded(range_.close),
        "high_52w": rounded(range_.high),
        "high_date": range_.high_date.strftime("%Y-%m-%d"),
        "low_52w": rounded(range_.low),
        "low_date": range_.low_date.strftime("%Y-%m-%d"),
        "percent_below_high": rounded(range_.percent_below_high, PERCENT_DECIMALS),
        "percent_above_low": rounded(range_.percent_above_low, PERCENT_DECIMALS),
        "is_new_high": range_.is_new_high,
        "is_new_low": range_.is_new_low,
        "sessions": range_.sessions,
    }


def short_range_warning(symbols: list[str], year_sessions: int) -> ResponseWarning:
    """The 52-week figures of these stocks cover less than a full year."""
    return make_warning(
        WarningCode.SHORT_HISTORY,
        f"{len(symbols)} stock(s) have fewer than {year_sessions} sessions of "
        "history, so their 52-week range covers a shorter period.",
        symbols,
    )


def year_range_report(
    symbol: str, history: pd.DataFrame, settings: dict[str, Any]
) -> tuple[dict[str, Any], list[ResponseWarning]]:
    """The 52-week range of one stock, with a warning when it is short."""
    year_sessions = settings["year_sessions"]
    try:
        row = year_range_row(symbol, year_range(history, year_sessions))
    except InsufficientHistoryError as error:
        return (
            {
                "symbol": symbol,
                "as_of": history["date"].iloc[-1].strftime("%Y-%m-%d"),
                "year_sessions": year_sessions,
                "range": None,
            },
            [
                make_warning(
                    WarningCode.SHORT_HISTORY,
                    f"Not enough history for a 52-week range ({error}).",
                    [symbol],
                )
            ],
        )
    warnings = []
    if row["sessions"] < year_sessions:
        warnings.append(short_range_warning([symbol], year_sessions))
    return (
        {
            "symbol": symbol,
            "as_of": history["date"].iloc[-1].strftime("%Y-%m-%d"),
            "year_sessions": year_sessions,
            "range": row,
        },
        warnings,
    )


def scan_year_ranges(
    symbols: tuple[str, ...],
    rows_by_symbol: dict[str, pd.DataFrame],
    year_sessions: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], str | None]:
    """The 52-week range of every symbol that has current data.

    Returns the range records, the symbols skipped (with a code and reason) and
    the latest date in the data. A stock whose last session is earlier than the
    latest date in the data is skipped as stale, so a list never mixes dates.
    """
    available = {s: rows_by_symbol[s] for s in symbols if s in rows_by_symbol}
    latest = max((rows["date"].max() for rows in available.values()), default=None)
    records, skipped = [], []
    for symbol in symbols:
        history = available.get(symbol)
        if history is None:
            skipped.append(
                {
                    "symbol": symbol,
                    "code": "symbol_not_found",
                    "reason": f"Symbol '{symbol}' was not found",
                }
            )
            continue
        history = history.sort_values("date")
        if history["date"].iloc[-1] < latest:
            skipped.append(
                {
                    "symbol": symbol,
                    "code": "stale_data",
                    "reason": f"Last session {history['date'].iloc[-1]:%Y-%m-%d} is "
                    f"before the latest date {latest:%Y-%m-%d}",
                }
            )
            continue
        try:
            records.append(year_range_row(symbol, year_range(history, year_sessions)))
        except InsufficientHistoryError as error:
            skipped.append(
                {"symbol": symbol, "code": "short_history", "reason": str(error)}
            )
    return records, skipped, None if latest is None else latest.strftime("%Y-%m-%d")


# --- Relative strength -------------------------------------------------------------


def resolve_benchmarks(
    book: BenchmarkBook, keys: list[str], symbol: str
) -> tuple[list[Benchmark], list[dict[str, str]]]:
    """Resolve benchmark names for one stock.

    Returns the benchmarks that could be built (one per index, the first name
    that reaches it winning) and, for each that could not, its name and reason.
    """
    resolved: dict[str, Benchmark] = {}
    unavailable = []
    for key in keys:
        try:
            benchmark = book.resolve(key, symbol)
        except BenchmarkError as error:
            unavailable.append({"key": key, "reason": str(error)})
            continue
        if benchmark is None:
            unavailable.append(
                {
                    "key": key,
                    "reason": f"{symbol} has no {key.removeprefix('auto_')} index",
                    "no_match": True,
                }
            )
        elif benchmark.id not in resolved:
            resolved[benchmark.id] = benchmark
    return list(resolved.values()), unavailable


def benchmark_label(benchmark: Benchmark) -> dict[str, Any]:
    return {
        "key": benchmark.key,
        "id": benchmark.id,
        "name": benchmark.name,
        "kind": "official" if benchmark.official else "approximate",
        "segment": benchmark.segment,
    }


def excess_entries(
    benchmarks: list[Benchmark],
    stock_return: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[dict[str, Any]]:
    """The stock's return less each benchmark's, over one window."""
    entries = []
    for benchmark in benchmarks:
        entry: dict[str, Any] = {"key": benchmark.key, "id": benchmark.id}
        try:
            change = level_return(benchmark.levels, start, end)
        except SeriesCoverageError as error:
            entry |= {
                "return_percent": None,
                "excess_percent": None,
                "reason": f"{benchmark.name}: {error}",
            }
        else:
            entry |= {
                "return_percent": rounded(change, PERCENT_DECIMALS),
                "excess_percent": rounded(stock_return - change, PERCENT_DECIMALS),
            }
        entries.append(entry)
    return entries


def benchmark_warnings(
    benchmarks: list[Benchmark],
    unavailable: list[dict[str, str]],
    entries: list[dict[str, Any]],
) -> list[ResponseWarning]:
    """Warnings for approximate benchmarks, benchmarks that could not be built
    and benchmarks whose values do not cover a period."""
    warnings = [
        make_warning(WarningCode.BENCHMARK_APPROXIMATE, f"{b.name}: {b.note}.")
        for b in benchmarks
        if not b.official
    ]
    warnings += [
        make_warning(
            WarningCode.BENCHMARK_UNAVAILABLE,
            f"{item['key']}: {item['reason']}.",
            item.get("symbols"),
        )
        for item in unavailable
    ]
    uncovered = dict.fromkeys(
        entry["reason"] for entry in entries if entry.get("reason")
    )
    warnings += [
        make_warning(WarningCode.BENCHMARK_UNAVAILABLE, f"{reason}.")
        for reason in uncovered
    ]
    return warnings


def relative_strength_report(
    symbol: str,
    history: pd.DataFrame,
    book: BenchmarkBook,
    keys: list[str],
    periods: dict[str, int],
) -> tuple[dict[str, Any], list[ResponseWarning]]:
    """One stock's return and excess return against each benchmark, per period."""
    benchmarks, unavailable = resolve_benchmarks(book, keys, symbol)
    if not benchmarks:
        raise BenchmarkError(
            "None of the benchmarks could be used: "
            + "; ".join(f"{item['key']}: {item['reason']}" for item in unavailable)
        )
    close = history["close"]
    results, short, all_entries = [], [], []
    for label, sessions in periods.items():
        try:
            stock_return = period_return(close, sessions)
        except InsufficientHistoryError as error:
            short.append(f"{label} ({error})")
            results.append(
                {
                    "period": label,
                    "sessions": sessions,
                    "start_date": None,
                    "end_date": None,
                    "stock_return_percent": None,
                    "benchmarks": [],
                }
            )
            continue
        start, end = history["date"].iloc[-1 - sessions], history["date"].iloc[-1]
        entries = excess_entries(benchmarks, stock_return, start, end)
        all_entries += entries
        results.append(
            {
                "period": label,
                "sessions": sessions,
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
                "stock_return_percent": rounded(stock_return, PERCENT_DECIMALS),
                "benchmarks": entries,
            }
        )
    warnings = benchmark_warnings(benchmarks, unavailable, all_entries)
    if short:
        warnings.append(
            make_warning(
                WarningCode.SHORT_HISTORY,
                f"Not enough history for: {'; '.join(short)}.",
                [symbol],
            )
        )
    data = {
        "symbol": symbol,
        "as_of": history["date"].iloc[-1].strftime("%Y-%m-%d"),
        "benchmarks": [benchmark_label(b) for b in benchmarks],
        "results": results,
    }
    return data, warnings


def rank_relative_strength_data(
    symbols: tuple[str, ...],
    rows_by_symbol: dict[str, pd.DataFrame],
    book: BenchmarkBook,
    keys: list[str],
    rank_by: str,
    sessions: int,
) -> dict[str, Any]:
    """Every stock's return and excess return over one period, best first by
    its excess return over the `rank_by` benchmark."""
    available = {s: rows_by_symbol[s] for s in symbols if s in rows_by_symbol}
    latest = max((rows["date"].max() for rows in available.values()), default=None)
    ranked, skipped = [], []
    used: dict[str, Benchmark] = {}
    unavailable: dict[str, dict[str, Any]] = {}
    entries_seen: list[dict[str, Any]] = []

    def skip(symbol: str, code: str, reason: str) -> None:
        skipped.append({"symbol": symbol, "code": code, "reason": reason})

    for symbol in symbols:
        history = available.get(symbol)
        if history is None:
            skip(symbol, "symbol_not_found", f"Symbol '{symbol}' was not found")
            continue
        history = history.sort_values("date")
        if history["date"].iloc[-1] < latest:
            skip(symbol, "stale_data", f"Last session is before {latest:%Y-%m-%d}")
            continue
        try:
            stock_return = period_return(history["close"], sessions)
        except InsufficientHistoryError as error:
            skip(symbol, "short_history", str(error))
            continue
        benchmarks, missing = resolve_benchmarks(book, keys, symbol)
        for item in missing:
            group = unavailable.setdefault(
                item["key"], {"reason": item["reason"], "symbols": []}
            )
            if item.get("no_match"):
                group["symbols"].append(symbol)
        start, end = history["date"].iloc[-1 - sessions], history["date"].iloc[-1]
        entries = excess_entries(benchmarks, stock_return, start, end)
        by_key = {entry["key"]: entry for entry in entries}
        chosen = by_key.get(rank_by)
        if chosen is None or chosen["excess_percent"] is None:
            reason = (chosen or {}).get(
                "reason"
            ) or f"No {rank_by} benchmark for {symbol}"
            skip(symbol, "benchmark_unavailable", reason)
            continue
        entries_seen += entries
        used.update({b.id: b for b in benchmarks})
        ranked.append(
            {
                "symbol": symbol,
                "stock_return_percent": rounded(stock_return, PERCENT_DECIMALS),
                "excess_percent": chosen["excess_percent"],
                "benchmarks": entries,
            }
        )
    ranked.sort(key=lambda row: (-row["excess_percent"], row["symbol"]))
    return {
        "ranked": ranked,
        "skipped": skipped,
        "benchmarks": list(used.values()),
        "unavailable": [
            _unavailable_item(key, group) for key, group in unavailable.items()
        ],
        "entries": entries_seen,
        "as_of": None if latest is None else latest.strftime("%Y-%m-%d"),
    }


# --- Risk ----------------------------------------------------------------------------

MAX_LISTED_MOVES = 5


def risk_report(
    symbol: str, history: pd.DataFrame, settings: dict[str, Any]
) -> tuple[dict[str, Any], list[ResponseWarning]]:
    """Volatility, worst drawdown and ATR as a percent of price for one stock.

    Volatility and drawdown cover the last `year_sessions` sessions, or all the
    history when there is less. Each figure that cannot be calculated is null
    and named in one short_history warning; large one-day moves left in the
    window (splits that were not adjusted, or genuine shocks) get their own
    large_price_move warning because they dominate these figures.
    """
    year_sessions = settings["year_sessions"]
    close = history["close"]
    window = history.tail(year_sessions)
    short: list[str] = []
    warnings: list[ResponseWarning] = []

    volatility = None
    try:
        found = annualized_volatility(close, year_sessions)
        volatility = {
            "annualized_percent": rounded(found.annualized_percent, PERCENT_DECIMALS),
            "daily_percent": rounded(found.daily_percent, PERCENT_DECIMALS),
            "returns_used": found.returns,
        }
    except InsufficientHistoryError as error:
        short.append(f"volatility ({error})")

    drawdown = None
    try:
        found_drawdown = max_drawdown(history, year_sessions)
        drawdown = {
            "max_percent": rounded(found_drawdown.max_percent, PERCENT_DECIMALS),
            "peak_date": _date_text(found_drawdown.peak_date),
            "peak_close": _price_or_none(found_drawdown.peak_close),
            "trough_date": _date_text(found_drawdown.trough_date),
            "trough_close": _price_or_none(found_drawdown.trough_close),
            "recovered": found_drawdown.recovered,
            "current_percent": rounded(
                found_drawdown.current_percent, PERCENT_DECIMALS
            ),
        }
    except InsufficientHistoryError as error:
        short.append(f"drawdown ({error})")

    atr_period = settings["atr_period"]
    atr_data = {"period": atr_period, "value": None, "percent_of_close": None}
    try:
        value = float(atr(history["high"], history["low"], close, atr_period).iloc[-1])
        atr_data["value"] = rounded(value)
        atr_data["percent_of_close"] = rounded(
            value / float(close.iloc[-1]) * 100, PERCENT_DECIMALS
        )
    except InsufficientHistoryError as error:
        short.append(f"ATR {atr_period} ({error})")

    if len(window) < year_sessions:
        short.append(f"the window covers {len(window)} of {year_sessions} sessions")
    if short:
        warnings.append(
            make_warning(
                WarningCode.SHORT_HISTORY,
                f"Limited history: {'; '.join(short)}.",
                [symbol],
            )
        )

    moves = large_moves(window, DEFAULT_JUMP_THRESHOLD_PERCENT)
    if not moves.empty:
        listed = ", ".join(
            f"{row.date:%Y-%m-%d} ({row.change_percent:+.1f}%)"
            for row in moves.head(MAX_LISTED_MOVES).itertuples()
        )
        more = (
            ""
            if len(moves) <= MAX_LISTED_MOVES
            else f" and {len(moves) - MAX_LISTED_MOVES} more"
        )
        warnings.append(
            make_warning(
                WarningCode.LARGE_PRICE_MOVE,
                f"{len(moves)} one-day move(s) of more than "
                f"{DEFAULT_JUMP_THRESHOLD_PERCENT:g}% in the window: {listed}{more}. "
                "They may be an unadjusted split or bonus, and they dominate the "
                "volatility and drawdown figures.",
                [symbol],
            )
        )

    data = {
        "symbol": symbol,
        "as_of": history["date"].iloc[-1].strftime("%Y-%m-%d"),
        "window_sessions": len(window),
        "year_sessions": year_sessions,
        "volatility": volatility,
        "drawdown": drawdown,
        "atr": atr_data,
    }
    return data, warnings


def _date_text(value: pd.Timestamp | None) -> str | None:
    return None if value is None else value.strftime("%Y-%m-%d")


def _price_or_none(value: float | None) -> float | None:
    return None if value is None else rounded(value)


def _unavailable_item(key: str, group: dict[str, Any]) -> dict[str, Any]:
    """A benchmark that could not be used for some stocks: when it is the stock
    that has no match (no size or sector index), list every such stock."""
    if not group["symbols"]:
        return {"key": key, "reason": group["reason"]}
    count = len(group["symbols"])
    return {
        "key": key,
        "reason": f"{count} stock(s) have no {key.removeprefix('auto_')} index",
        "symbols": group["symbols"],
    }


# --- Comparing stocks ----------------------------------------------------------------

MIN_COMPARED = 2
MAX_COMPARED = 10


def merge_warnings(warnings: list[ResponseWarning]) -> list[ResponseWarning]:
    """Join warnings that say the same thing, keeping every symbol they name."""
    merged: dict[tuple[str, str], ResponseWarning] = {}
    for warning in warnings:
        key = (warning["code"], warning["message"])
        if key not in merged:
            merged[key] = dict(warning)  # type: ignore[assignment]
        elif "symbols" in warning:
            known = merged[key].setdefault("symbols", [])
            known += [s for s in warning["symbols"] if s not in known]
    return list(merged.values())


def compare_report(
    symbols: list[str],
    rows_by_symbol: dict[str, pd.DataFrame],
    book: BenchmarkBook,
    keys: list[str],
    periods: dict[str, int],
    settings: dict[str, Any],
) -> tuple[dict[str, Any], list[ResponseWarning]]:
    """Returns, indicators, 52-week position, relative strength and risk for
    several stocks, side by side.

    Every number uses only the dates all the stocks share (from the latest
    first session to the earliest last session), so the figures are comparable.
    A warning says so when that shortens any stock's history.
    """
    missing = [symbol for symbol in symbols if symbol not in rows_by_symbol]
    found = [symbol for symbol in symbols if symbol in rows_by_symbol]
    warnings: list[ResponseWarning] = []
    if missing:
        warnings.append(
            make_warning(
                WarningCode.SYMBOL_NOT_FOUND,
                f"{len(missing)} symbol(s) are not in the dataset and were left out.",
                missing,
            )
        )
    if len(found) < MIN_COMPARED:
        raise ValueError(
            f"A comparison needs at least {MIN_COMPARED} symbols that are in the "
            f"dataset; found {len(found)}"
        )
    full = {s: rows_by_symbol[s].sort_values("date") for s in found}
    start = max(history["date"].iloc[0] for history in full.values())
    end = min(history["date"].iloc[-1] for history in full.values())
    if start >= end:
        raise ValueError("These stocks have no trading days in common")
    shared = {
        s: history[(history["date"] >= start) & (history["date"] <= end)]
        for s, history in full.items()
    }
    shortened = [s for s in found if len(shared[s]) < len(full[s])]
    if shortened:
        shortest = min(found, key=lambda s: (len(full[s]), s))
        warnings.append(
            make_warning(
                WarningCode.DIFFERENT_HISTORIES,
                f"The stocks have different histories, so every figure uses only the "
                f"dates they share: {start:%Y-%m-%d} to {end:%Y-%m-%d} "
                f"({len(shared[shortest])} sessions for {shortest}).",
                shortened,
            )
        )

    stocks, benchmarks_used = [], {}
    short_year: list[str] = []
    for symbol in found:
        history = shared[symbol]
        indicators, indicator_warnings = indicator_report(symbol, history, settings)
        year, year_warnings = year_range_report(symbol, history, settings)
        risk, risk_warnings = risk_report(symbol, history, settings)
        warnings += indicator_warnings + risk_warnings
        if year["range"] is None:
            warnings += year_warnings
        elif year["range"]["sessions"] < settings["year_sessions"]:
            short_year.append(symbol)
        try:
            strength, strength_warnings = relative_strength_report(
                symbol, history, book, keys, periods
            )
        except BenchmarkError as error:
            strength = None
            warnings.append(
                make_warning(WarningCode.BENCHMARK_UNAVAILABLE, f"{error}.", [symbol])
            )
        else:
            warnings += strength_warnings
            benchmarks_used |= {b["id"]: b for b in strength["benchmarks"]}
        returns = []
        for label, sessions in periods.items():
            entry: dict[str, Any] = {
                "period": label,
                "sessions": sessions,
                "return_percent": None,
                "benchmarks": [],
            }
            if strength is not None:
                result = next(r for r in strength["results"] if r["period"] == label)
                entry["return_percent"] = result["stock_return_percent"]
                entry["benchmarks"] = result["benchmarks"]
            else:
                try:
                    entry["return_percent"] = rounded(
                        period_return(history["close"], sessions), PERCENT_DECIMALS
                    )
                except InsufficientHistoryError:
                    pass
            returns.append(entry)
        stocks.append(
            {
                "symbol": symbol,
                "close": indicators["close"],
                "sessions": len(history),
                "returns": returns,
                "ema": indicators["ema"],
                "rsi": indicators["rsi"]["value"],
                "atr_percent": indicators["atr"]["percent_of_close"],
                "traded_value_crore": indicators["average_traded_value"]["crore"],
                "range_52w": year["range"],
                "volatility": risk["volatility"],
                "drawdown": risk["drawdown"],
            }
        )
    if short_year:
        warnings.append(short_range_warning(short_year, settings["year_sessions"]))
    data = {
        "symbols": found,
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "benchmarks": list(benchmarks_used.values()),
        "stocks": stocks,
    }
    return data, merge_warnings(warnings)


def comparison_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One flat row per stock, for a CSV."""
    rows = []
    for stock in data["stocks"]:
        row: dict[str, Any] = {"symbol": stock["symbol"], "close": stock["close"]}
        for entry in stock["returns"]:
            row[f"return_{entry['period']}_percent"] = entry["return_percent"]
            for benchmark in entry["benchmarks"]:
                row[f"excess_{entry['period']}_vs_{benchmark['id']}_percent"] = (
                    benchmark["excess_percent"]
                )
        for item in stock["ema"]:
            row[f"close_vs_ema_{item['period']}_percent"] = item["close_vs_ema_percent"]
        range_ = stock["range_52w"] or {}
        volatility, drawdown = stock["volatility"] or {}, stock["drawdown"] or {}
        row |= {
            "rsi": stock["rsi"],
            "atr_percent": stock["atr_percent"],
            "traded_value_crore": stock["traded_value_crore"],
            "high_52w": range_.get("high_52w"),
            "low_52w": range_.get("low_52w"),
            "percent_below_high": range_.get("percent_below_high"),
            "percent_above_low": range_.get("percent_above_low"),
            "volatility_annualized_percent": volatility.get("annualized_percent"),
            "max_drawdown_percent": drawdown.get("max_percent"),
            "sessions": stock["sessions"],
        }
        rows.append(row)
    return rows
