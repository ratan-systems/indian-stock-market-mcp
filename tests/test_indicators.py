"""Tests of the indicator functions against known values and small examples."""

import numpy as np
import pandas as pd
import pytest

from indian_stock_market_mcp.indicators import (
    InsufficientHistoryError,
    atr,
    average_traded_value,
    ema,
    rsi,
)

# Worked examples from the StockCharts ChartSchool pages for RSI and EMA.
RSI_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
    46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
    43.42, 42.66, 43.13,
]  # fmt: skip
RSI_PUBLISHED = [
    70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38,
    54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77,
]  # fmt: skip

EMA_CLOSES = [
    22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29,
    22.15, 22.39, 22.38, 22.61, 23.36, 24.05, 23.75, 23.83, 23.95, 23.63,
    23.82, 23.87, 23.65, 23.19, 23.10, 23.33, 22.68, 23.10, 22.40, 22.17,
]  # fmt: skip
EMA_PUBLISHED = [
    22.22, 22.21, 22.24, 22.27, 22.33, 22.52, 22.80, 22.97, 23.13, 23.28,
    23.34, 23.43, 23.51, 23.54, 23.47, 23.40, 23.39, 23.26, 23.23, 23.08,
    22.92,
]  # fmt: skip


def test_ema_matches_the_published_ten_day_example():
    result = ema(pd.Series(EMA_CLOSES), 10)

    assert result.iloc[:9].isna().all()
    assert result.dropna().round(2).tolist() == pytest.approx(EMA_PUBLISHED, abs=0.011)


def test_ema_first_value_is_the_simple_average_of_the_first_period():
    result = ema(pd.Series([2.0, 4.0, 6.0, 8.0]), 3)

    assert result.iloc[2] == pytest.approx(4.0)
    # alpha = 2 / (3 + 1) = 0.5
    assert result.iloc[3] == pytest.approx(0.5 * 8.0 + 0.5 * 4.0)


def test_rsi_first_value_matches_a_hand_calculation():
    # The first 14 changes hold gains of 3.34 and losses of 1.40 in total, so
    # average gain 3.34 / 14 and average loss 1.40 / 14 give RS 2.3857 and
    # RSI 100 - 100 / 3.3857 = 70.46.
    result = rsi(pd.Series(RSI_CLOSES), 14)

    assert result.iloc[:14].isna().all()
    assert result.iloc[14] == pytest.approx(70.4641, abs=0.0001)


def test_rsi_follows_the_published_series_within_its_rounding():
    # The published table rounds its intermediate averages, which moves the
    # values by up to 0.07 compared with unrounded arithmetic.
    result = rsi(pd.Series(RSI_CLOSES), 14).dropna()

    assert result.tolist() == pytest.approx(RSI_PUBLISHED, abs=0.08)


def test_rsi_stays_between_zero_and_one_hundred():
    rng = np.random.default_rng(7)
    close = pd.Series(100 + rng.normal(0, 1, 300).cumsum())

    result = rsi(close, 14).dropna()

    assert result.between(0, 100).all()


def test_rsi_is_100_when_the_price_only_rose():
    assert rsi(pd.Series(np.arange(100.0, 120.0)), 14).iloc[-1] == 100.0


def test_rsi_is_0_when_the_price_only_fell():
    assert rsi(pd.Series(np.arange(120.0, 100.0, -1.0)), 14).iloc[-1] == 0.0


def test_rsi_is_50_when_the_price_did_not_move():
    # The convention of TA-Lib and pandas-ta: no gains and no losses is neutral.
    assert rsi(pd.Series([100.0] * 20), 14).iloc[-1] == 50.0


# EMA needs `period` closes; RSI and ATR need period + 1, because they work on
# the change from the previous close.
SESSIONS_NEEDED = [("ema", 5), ("rsi", 6), ("atr", 6)]


@pytest.mark.parametrize(("name", "needed"), SESSIONS_NEEDED)
def test_indicators_need_enough_sessions(name, needed):
    def run(sessions):
        series = pd.Series(np.arange(1.0, sessions + 1.0))
        if name == "ema":
            return ema(series, 5)
        if name == "rsi":
            return rsi(series, 5)
        return atr(series + 1, series, series, 5)

    with pytest.raises(InsufficientHistoryError) as error:
        run(needed - 1)

    assert error.value.needed == needed
    assert error.value.available == needed - 1
    assert run(needed).notna().sum() == 1  # exactly one value at the minimum


def test_atr_matches_a_hand_calculation():
    # True ranges from day 2 on: 2, 3, 1, then 3.5 (day 5, after a gap up).
    # Period 3: the first ATR is the average of the first three, then Wilder
    # smoothing: (previous ATR x 2 + new true range) / 3.
    high = pd.Series([10.0, 11.0, 13.0, 12.0, 15.0])
    low = pd.Series([8.0, 9.0, 10.0, 11.0, 14.0])
    close = pd.Series([9.0, 10.0, 12.0, 11.5, 14.5])

    result = atr(high, low, close, period=3)

    assert result.iloc[:3].isna().all()
    assert result.iloc[3] == pytest.approx((2 + 3 + 1) / 3)
    assert result.iloc[4] == pytest.approx(((2 + 3 + 1) / 3 * 2 + 3.5) / 3)


def test_average_traded_value_is_the_mean_of_close_times_volume():
    close = pd.Series([10.0, 20.0, 30.0])
    volume = pd.Series([100.0, 200.0, 300.0])

    assert average_traded_value(close, volume, sessions=2) == pytest.approx(
        (20 * 200 + 30 * 300) / 2
    )


def test_average_traded_value_ignores_sessions_without_volume():
    close = pd.Series([10.0, 20.0, 30.0])
    volume = pd.Series([100.0, np.nan, 300.0])

    assert average_traded_value(close, volume, sessions=2) == pytest.approx(
        (10 * 100 + 30 * 300) / 2
    )


def test_average_traded_value_needs_a_full_window_of_volume():
    close = pd.Series([10.0, 20.0])
    volume = pd.Series([100.0, np.nan])

    with pytest.raises(InsufficientHistoryError):
        average_traded_value(close, volume, sessions=2)
