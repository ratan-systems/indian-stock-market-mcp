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
    atr,
    average_traded_value,
    ema,
    rsi,
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
