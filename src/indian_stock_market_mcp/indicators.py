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
