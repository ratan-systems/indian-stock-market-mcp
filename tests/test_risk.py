"""Tests of the risk snapshot: volatility, drawdown and large moves."""

import asyncio
import json
import math
from itertools import pairwise

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import adjustments, bhavcopy, data
from indian_stock_market_mcp import server as server_module
from indian_stock_market_mcp.indicators import (
    InsufficientHistoryError,
    annualized_volatility,
    large_moves,
    max_drawdown,
)


def frame(closes, start="2026-01-01"):
    return pd.DataFrame(
        {"date": pd.bdate_range(start, periods=len(closes)), "close": closes}
    )


def alternating(returns, count):
    """Closes from 100 with daily returns alternating between +r and -r."""
    closes = [100.0]
    for i in range(count):
        closes.append(closes[-1] * (1 + (returns if i % 2 == 0 else -returns)))
    return closes


def reference_drawdown(closes):
    """The worst fall from a running peak, by the plainest possible loop."""
    peak, worst = closes[0], 0.0
    for price in closes:
        peak = max(peak, price)
        worst = max(worst, (peak - price) / peak * 100)
    return worst


def reference_volatility(closes):
    """Annualised volatility from the sample standard deviation of returns."""
    returns = [b / a - 1 for a, b in pairwise(closes)]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252) * 100


# --- Volatility ------------------------------------------------------------------------


def test_volatility_is_the_daily_standard_deviation_scaled_to_a_year():
    closes = alternating(0.01, 60)  # 60 returns of +1%, -1%, ...

    result = annualized_volatility(pd.Series(closes))

    # The returns are 1% either way, so their mean is about 0 and their
    # sample standard deviation is 1% x sqrt(60 / 59).
    daily = 0.01 * math.sqrt(60 / 59)
    assert result.daily_percent == pytest.approx(daily * 100, rel=0.01)
    assert result.annualized_percent == pytest.approx(
        daily * math.sqrt(252) * 100, rel=0.01
    )
    assert result.returns == 60


def test_a_stock_that_never_moves_has_no_volatility():
    assert annualized_volatility(pd.Series([100.0] * 30)).annualized_percent == 0.0


def test_only_the_last_sessions_of_the_window_count():
    calm_then_wild = [100.0] * 50 + alternating(0.05, 30)

    wild_only = annualized_volatility(pd.Series(calm_then_wild), sessions=30)
    everything = annualized_volatility(pd.Series(calm_then_wild), sessions=252)

    assert wild_only.returns == 29
    assert wild_only.annualized_percent > everything.annualized_percent


def test_volatility_needs_twenty_returns():
    with pytest.raises(InsufficientHistoryError) as error:
        annualized_volatility(pd.Series(alternating(0.01, 19)))  # 20 closes

    assert (error.value.needed, error.value.available) == (21, 20)
    assert annualized_volatility(pd.Series(alternating(0.01, 20))).returns == 20


# --- Drawdown --------------------------------------------------------------------------


def test_the_worst_drawdown_is_the_largest_fall_from_a_peak_close():
    result = max_drawdown(frame([100, 120, 90, 110, 130]))

    assert result.max_percent == pytest.approx(25.0)  # 120 -> 90
    assert (result.peak_close, result.trough_close) == (120.0, 90.0)
    assert result.peak_date == pd.Timestamp("2026-01-02")
    assert result.trough_date == pd.Timestamp("2026-01-05")
    assert result.recovered is True  # it later closed above 120
    assert result.current_percent == 0.0


def test_a_fall_that_has_not_recovered_says_so_and_shows_the_current_gap():
    result = max_drawdown(frame([100, 120, 90, 100]))

    assert result.recovered is False
    assert result.max_percent == pytest.approx(25.0)
    assert result.current_percent == pytest.approx((120 - 100) / 120 * 100)


def test_the_worst_of_several_falls_is_chosen_not_the_first_or_last():
    # 100 -> 90 (10%), then 200 -> 120 (40%), then 150 -> 135 (10%)
    result = max_drawdown(frame([100, 90, 200, 120, 150, 135]))

    assert result.max_percent == pytest.approx(40.0)
    assert result.peak_close == 200.0
    assert result.trough_close == 120.0


def test_the_peak_is_the_last_session_at_that_price_before_the_trough():
    result = max_drawdown(frame([100, 100, 100, 80]))

    assert result.peak_date == pd.Timestamp("2026-01-05")  # day the fall began


def test_a_stock_that_never_fell_has_no_drawdown():
    result = max_drawdown(frame([100, 101, 105]))

    assert result.max_percent == 0.0
    assert (result.peak_date, result.trough_date) == (None, None)
    assert result.recovered is True
    assert result.current_percent == 0.0


def test_only_the_window_counts_for_drawdown():
    crash_then_calm = frame([200, 100, 100, 101, 102])

    assert max_drawdown(crash_then_calm, sessions=3).max_percent == 0.0
    assert max_drawdown(crash_then_calm, sessions=5).max_percent == pytest.approx(50.0)


def test_drawdown_needs_two_sessions():
    with pytest.raises(InsufficientHistoryError):
        max_drawdown(frame([100]))


# --- Large moves --------------------------------------------------------------------------


def test_moves_larger_than_the_threshold_in_either_direction_are_found():
    moves = large_moves(frame([100, 70, 70, 100, 101]), 25.0)

    assert moves["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-01-02",
        "2026-01-06",
    ]
    assert moves["change_percent"].round(1).tolist() == [-30.0, 42.9]


def test_a_move_equal_to_the_threshold_is_not_counted():
    assert large_moves(frame([100, 75]), 25.0).empty


# --- The tool -------------------------------------------------------------------------------


def call_envelope(name, arguments=None):
    result = asyncio.run(server_module.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def codes(envelope):
    return [warning["code"] for warning in envelope["warnings"]]


def write_prices(tmp_path, monkeypatch, symbol, closes, *, parquet=False, spread=0.5):
    days = pd.bdate_range("2025-01-01", periods=len(closes))
    rows = pd.DataFrame(
        {
            "date": days,
            "symbol": symbol,
            "open": closes,
            "high": [c + spread for c in closes],
            "low": [c - spread for c in closes],
            "close": closes,
            "volume": 1000.0,
        }
    )
    if parquet:
        path = tmp_path / "prices.parquet"
        bhavcopy.write_output(rows, "NSE", path)
    else:
        path = tmp_path / "prices.csv"
        rows.to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    return path


def test_a_full_year_gives_every_figure_and_no_warnings(tmp_path, monkeypatch):
    closes = alternating(0.01, 259)  # 260 sessions
    write_prices(tmp_path, monkeypatch, "TCS", closes)

    envelope = call_envelope("get_risk_snapshot", {"symbol": "tcs.ns"})
    result = envelope["data"]
    window = closes[-252:]

    assert envelope["warnings"] == []
    assert result["symbol"] == "TCS"
    assert (result["window_sessions"], result["year_sessions"]) == (252, 252)
    assert result["volatility"]["annualized_percent"] == pytest.approx(
        reference_volatility(window), abs=0.01
    )
    assert result["volatility"]["returns_used"] == 251
    assert result["drawdown"]["max_percent"] == pytest.approx(
        reference_drawdown(window), abs=0.01
    )
    assert result["atr"]["period"] == 14
    assert result["atr"]["percent_of_close"] == pytest.approx(
        result["atr"]["value"] / closes[-1] * 100, abs=0.01
    )
    assert envelope["parameters"] == {
        "symbol": "TCS",
        "year_sessions": 252,
        "atr_period": 14,
    }
    assert envelope["as_of"] == result["as_of"]


def test_atr_as_a_percent_of_the_close_makes_stocks_comparable(tmp_path, monkeypatch):
    # A flat price with a 2-point daily range (spread 1 either side).
    write_prices(tmp_path, monkeypatch, "FLAT", [100.0] * 60, spread=1.0)

    atr = call_envelope("get_risk_snapshot", {"symbol": "FLAT"})["data"]["atr"]

    assert atr["value"] == pytest.approx(2.0)
    assert atr["percent_of_close"] == pytest.approx(2.0)


def test_a_short_history_gives_what_it_can_and_one_warning(tmp_path, monkeypatch):
    write_prices(tmp_path, monkeypatch, "NEWCO", alternating(0.01, 14))  # 15 closes

    envelope = call_envelope("get_risk_snapshot", {"symbol": "NEWCO"})
    result = envelope["data"]

    assert result["volatility"] is None  # needs 21 closes
    assert result["drawdown"]["max_percent"] == pytest.approx(
        reference_drawdown(alternating(0.01, 14)), abs=0.01
    )
    assert result["atr"]["value"] is not None  # a 14-day ATR needs 15 closes
    assert result["window_sessions"] == 15
    assert codes(envelope) == ["short_history"]
    message = envelope["warnings"][0]["message"]
    assert "volatility" in message and "covers 15 of 252 sessions" in message
    assert "ATR" not in message


def test_a_stock_with_a_few_sessions_has_nothing_to_report_but_warns(
    tmp_path, monkeypatch
):
    write_prices(tmp_path, monkeypatch, "TINY", [100.0, 101.0])

    result = call_envelope("get_risk_snapshot", {"symbol": "TINY"})

    assert result["data"]["volatility"] is None
    assert result["data"]["atr"]["value"] is None
    assert result["data"]["drawdown"]["max_percent"] == 0.0
    assert codes(result) == ["short_history"]


def test_a_one_day_move_above_25_percent_is_called_out_with_its_date(
    tmp_path, monkeypatch
):
    closes = [100.0] * 30 + [150.0] * 30  # +50% on session 31
    write_prices(tmp_path, monkeypatch, "JUMPY", closes)

    envelope = call_envelope(
        "get_risk_snapshot", {"symbol": "JUMPY", "year_sessions": 200}
    )

    moves = [w for w in envelope["warnings"] if w["code"] == "large_price_move"]
    assert len(moves) == 1
    assert "1 one-day move(s) of more than 25%" in moves[0]["message"]
    jump_day = pd.bdate_range("2025-01-01", periods=60)[30]
    assert f"{jump_day:%Y-%m-%d} (+50.0%)" in moves[0]["message"]
    assert moves[0]["symbols"] == ["JUMPY"]


def test_a_move_outside_the_window_is_not_reported(tmp_path, monkeypatch):
    closes = [100.0, 150.0] + [
        150.0 + (i % 2) for i in range(258)
    ]  # jump on day 2 of 260
    write_prices(tmp_path, monkeypatch, "OLDJUMP", closes)

    envelope = call_envelope("get_risk_snapshot", {"symbol": "OLDJUMP"})

    assert "large_price_move" not in codes(envelope)


def test_a_split_is_a_large_move_until_it_is_adjusted(tmp_path, monkeypatch):
    # A 4:1 split: the price fell from 1000 to 250 overnight, then wobbled.
    closes = [1000.0] * 40 + [250.0 + (i % 2) for i in range(40)]
    path = write_prices(tmp_path, monkeypatch, "SPLIT", closes, parquet=True)

    raw = call_envelope("get_risk_snapshot", {"symbol": "SPLIT", "year_sessions": 200})
    assert raw["data"]["drawdown"]["max_percent"] == pytest.approx(75.0, abs=0.1)
    assert {"large_price_move", "unadjusted_prices"} <= set(codes(raw))

    ex_date = pd.bdate_range("2025-01-01", periods=41)[-1].strftime("%Y-%m-%d")
    adjustments.write_sidecar(
        path, "NSE", {f"SPLIT|{ex_date}": {"status": "adjusted", "factor": 4.0}}
    )
    adjusted = call_envelope(
        "get_risk_snapshot", {"symbol": "SPLIT", "year_sessions": 200}
    )

    assert adjusted["data"]["drawdown"]["max_percent"] < 1.0
    assert "large_price_move" not in codes(adjusted)
    assert (
        adjusted["data"]["volatility"]["annualized_percent"]
        < raw["data"]["volatility"]["annualized_percent"]
    )


def test_the_window_and_atr_period_can_be_changed_per_question(tmp_path, monkeypatch):
    write_prices(tmp_path, monkeypatch, "TCS", alternating(0.01, 259))

    envelope = call_envelope(
        "get_risk_snapshot", {"symbol": "TCS", "year_sessions": 200, "atr_period": 5}
    )

    assert envelope["data"]["window_sessions"] == 200
    assert envelope["data"]["atr"]["period"] == 5
    assert envelope["parameters"]["year_sessions"] == 200
    assert (
        call_envelope("get_risk_snapshot", {"symbol": "TCS"})["data"]["window_sessions"]
        == 252
    )


def test_the_saved_settings_supply_the_defaults(tmp_path, monkeypatch):
    write_prices(tmp_path, monkeypatch, "TCS", alternating(0.01, 259))
    call_envelope("update_indicator_settings", {"changes": {"year_sessions": 220}})

    assert (
        call_envelope("get_risk_snapshot", {"symbol": "TCS"})["data"]["window_sessions"]
        == 220
    )


def test_unknown_symbols_and_bad_settings_are_errors(tmp_path, monkeypatch):
    write_prices(tmp_path, monkeypatch, "TCS", alternating(0.01, 100))

    with pytest.raises(ToolError, match="not found"):
        call_envelope("get_risk_snapshot", {"symbol": "NOPE"})
    with pytest.raises(ToolError, match="between 200 and 260"):
        call_envelope("get_risk_snapshot", {"symbol": "TCS", "year_sessions": 10})
