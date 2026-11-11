"""Price indicators: thin wrappers around TA-Lib, the standard indicator library.

Nothing here reads files or knows about MCP. Each function takes price series
(oldest first) and returns a series of the same length, empty (NaN) until
enough history exists. A function raises InsufficientHistoryError when the
series is too short to produce even one value, so callers can turn that into
a warning instead of a column of blanks.

TA-Lib supplies the formulas, so new indicators (MACD, Bollinger bands, ...)
are one wrapper each. Its edge cases are the industry's: RSI is 100 when there
were no losses, 50 when the price did not move, and 0 when there were no gains.
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import talib


class InsufficientHistoryError(ValueError):
    """Raised when a series has fewer sessions than an indicator needs."""

    def __init__(self, needed: int, available: int) -> None:
        super().__init__(f"needs {needed} sessions, have {available}")
        self.needed = needed
        self.available = available


def require_sessions(series: pd.Series, needed: int) -> None:
    if len(series) < needed:
        raise InsufficientHistoryError(needed, len(series))


def as_floats(series: pd.Series):
    """TA-Lib works on plain float arrays."""
    return series.to_numpy(dtype=float)


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average: recent prices weigh more (alpha = 2/(n+1)),
    seeded with the simple average of the first `period` closes."""
    require_sessions(close, period)
    return pd.Series(talib.EMA(as_floats(close), timeperiod=period), index=close.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0 to 100) with Wilder smoothing.
    Needs period + 1 closes, because it works on the changes between them."""
    require_sessions(close, period + 1)
    return pd.Series(talib.RSI(as_floats(close), timeperiod=period), index=close.index)


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Average True Range with Wilder smoothing, in the price's own units.
    Needs period + 1 sessions, because each true range uses the previous close."""
    require_sessions(close, period + 1)
    values = talib.ATR(
        as_floats(high), as_floats(low), as_floats(close), timeperiod=period
    )
    return pd.Series(values, index=close.index)


def average_traded_value(
    close: pd.Series, volume: pd.Series, sessions: int = 20
) -> float:
    """Average of close x volume over the last `sessions` sessions (in rupees).

    Sessions with no volume are ignored, but a full window of valid sessions
    is still required.
    """
    value = (close * volume).dropna()
    require_sessions(value, sessions)
    return float(value.tail(sessions).mean())


@dataclass(frozen=True)
class YearRange:
    """A stock's high and low over a trailing window, and where it closed."""

    sessions: int  # sessions the window actually covers
    high: float
    high_date: pd.Timestamp
    low: float
    low_date: pd.Timestamp
    close: float
    percent_below_high: float  # how far the close is under the high, 0 at the high
    percent_above_low: float  # how far the close is over the low, 0 at the low
    is_new_high: bool  # the latest session's high is the window's high
    is_new_low: bool  # the latest session's low is the window's low


def year_range(history: pd.DataFrame, sessions: int = 252) -> YearRange:
    """The 52-week range from a stock's history (date, high, low, close).

    The window is the last `sessions` sessions, or all of them when there are
    fewer; `sessions` on the result says which. Highs and lows are the intraday
    extremes, and the distances are measured from the latest close.
    """
    require_sessions(history["close"], 2)
    window = history.tail(sessions)
    highs, lows = window["high"], window["low"]
    # Of several sessions at the same extreme, report the most recent one.
    high_date = window["date"].loc[highs[::-1].idxmax()]
    low_date = window["date"].loc[lows[::-1].idxmin()]
    high, low = float(highs.max()), float(lows.min())
    close = float(window["close"].iloc[-1])
    return YearRange(
        sessions=len(window),
        high=high,
        high_date=high_date,
        low=low,
        low_date=low_date,
        close=close,
        percent_below_high=(high - close) / high * 100,
        percent_above_low=(close - low) / low * 100,
        is_new_high=bool(highs.iloc[-1] >= high),
        is_new_low=bool(lows.iloc[-1] <= low),
    )


class SeriesCoverageError(ValueError):
    """Raised when an index series does not cover the dates asked for."""


def period_return(close: pd.Series, sessions: int) -> float:
    """Percent change of the latest close over the last `sessions` sessions."""
    require_sessions(close, sessions + 1)
    return float((close.iloc[-1] / close.iloc[-1 - sessions] - 1) * 100)


def level_return(levels: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Percent change of an index level (a series by date) between two dates.

    The series must run from the start date to the end date. One that stops
    earlier is refused rather than quietly using an old value, because a
    benchmark that ends before the stock does would understate the market.
    """
    if levels.empty:
        raise SeriesCoverageError("it has no values")
    if levels.index.max() < end:
        raise SeriesCoverageError(
            f"its values end on {levels.index.max():%Y-%m-%d}, before {end:%Y-%m-%d}"
        )
    if levels.index.min() > start:
        raise SeriesCoverageError(
            f"its values start on {levels.index.min():%Y-%m-%d}, after {start:%Y-%m-%d}"
        )
    return float((levels.asof(end) / levels.asof(start) - 1) * 100)


TRADING_DAYS = 252  # sessions in a year, used to annualise daily volatility
MIN_VOLATILITY_SESSIONS = 21  # 20 daily returns


@dataclass(frozen=True)
class Volatility:
    annualized_percent: float
    daily_percent: float
    returns: int  # daily returns the figure is based on


def annualized_volatility(close: pd.Series, sessions: int = TRADING_DAYS) -> Volatility:
    """Volatility of the last `sessions` closes: the standard deviation of the
    daily percent returns, scaled by the square root of 252 sessions a year.

    Needs 21 closes (20 returns); fewer would not give a meaningful figure.
    """
    window = close.tail(sessions)
    require_sessions(window, MIN_VOLATILITY_SESSIONS)
    returns = window.pct_change().dropna()
    daily = float(returns.std(ddof=1))
    return Volatility(
        annualized_percent=daily * math.sqrt(TRADING_DAYS) * 100,
        daily_percent=daily * 100,
        returns=len(returns),
    )


@dataclass(frozen=True)
class Drawdown:
    """The worst fall from a peak close to a later close inside a window."""

    max_percent: float  # the fall, as a positive percent (0 when the stock never fell)
    peak_date: pd.Timestamp | None
    peak_close: float | None
    trough_date: pd.Timestamp | None
    trough_close: float | None
    recovered: bool  # the close got back to the peak after the trough
    current_percent: float  # how far the latest close is below the window's peak


def max_drawdown(history: pd.DataFrame, sessions: int = TRADING_DAYS) -> Drawdown:
    """The worst peak-to-trough fall in closes over the last `sessions` sessions
    (history needs date and close columns)."""
    require_sessions(history["close"], 2)
    window = history.tail(sessions)
    close = window["close"].to_numpy(dtype=float)
    running_peak = np.maximum.accumulate(close)
    fall = close / running_peak - 1
    current = float((running_peak[-1] - close[-1]) / running_peak[-1] * 100)
    trough = int(np.argmin(fall))
    if fall[trough] == 0:
        return Drawdown(0.0, None, None, None, None, True, current)
    peak_close = float(running_peak[trough])
    # The peak is the last session at that price before the trough, the day
    # the fall began.
    peak = int(np.flatnonzero(close[: trough + 1] == peak_close)[-1])
    return Drawdown(
        max_percent=float(-fall[trough] * 100),
        peak_date=window["date"].iloc[peak],
        peak_close=peak_close,
        trough_date=window["date"].iloc[trough],
        trough_close=float(close[trough]),
        recovered=bool((close[trough:] >= peak_close).any()),
        current_percent=current,
    )


def large_moves(history: pd.DataFrame, threshold_percent: float) -> pd.DataFrame:
    """The sessions whose close moved by more than `threshold_percent` from the
    previous close, either way (history needs date and close columns)."""
    change = history["close"].pct_change() * 100
    return history.assign(change_percent=change)[change.abs() > threshold_percent]
