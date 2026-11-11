"""Per-stock reports built from indicators and settings.

These functions take a stock's validated history and the settings in force and
return the tool's data plus its warnings. They read no files, so the tools stay
thin and the numbers can be tested without a dataset.
"""

from typing import Any

import pandas as pd

from .envelope import ResponseWarning, WarningCode, make_warning
from .indicators import (
    InsufficientHistoryError,
    YearRange,
    atr,
    average_traded_value,
    ema,
    rsi,
    year_range,
)

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
