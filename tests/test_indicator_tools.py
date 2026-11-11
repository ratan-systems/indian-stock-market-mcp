"""Tests of the indicator and settings tools, called the way an MCP client calls them."""

import asyncio
import json
import math

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import data, server

SESSIONS = 60


def call_envelope(name, arguments=None):
    result = asyncio.run(server.mcp.call_tool(name, arguments or {}))
    if isinstance(result, tuple):
        return result[1]
    return json.loads(result[0].text)


def make_rows(symbol, sessions, volume, price=1000.0):
    """Deterministic, valid daily rows: a gentle trend with a wobble."""
    dates = pd.bdate_range("2026-01-01", periods=sessions)
    rows = []
    for position, day in enumerate(dates):
        close = price + position * 0.5 + 5 * math.sin(position)
        rows.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "symbol": symbol,
                "open": close - 0.3,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": volume,
            }
        )
    return rows


@pytest.fixture
def dataset(monkeypatch, tmp_path):
    """Write a CSV from rows and point the server at it."""

    def write(rows, drop_volume=False):
        path = tmp_path / "prices.csv"
        frame = pd.DataFrame(rows)
        if drop_volume:
            frame = frame.drop(columns="volume")
        frame.to_csv(path, index=False)
        monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
        monkeypatch.setattr(data, "_DOTENV_LOADED", True)

    return write


def codes(envelope):
    return [warning["code"] for warning in envelope["warnings"]]


# --- get_stock_indicators ------------------------------------------------------


def test_a_liquid_stock_with_long_history_has_every_indicator_and_no_warnings(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))

    envelope = call_envelope(
        "get_stock_indicators", {"symbol": "TCS", "ema_periods": [20, 50]}
    )
    result = envelope["data"]

    assert envelope["warnings"] == []
    assert result["symbol"] == "TCS"
    assert result["sessions_available"] == SESSIONS
    assert result["as_of"] == envelope["as_of"]
    assert [item["period"] for item in result["ema"]] == [20, 50]
    assert all(item["value"] is not None for item in result["ema"])
    assert result["rsi"]["value"] is not None
    assert result["atr"]["value"] is not None
    assert result["average_traded_value"]["rupees"] is not None


def test_each_indicator_without_enough_history_is_null_and_named_in_one_warning(
    dataset,
):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))

    envelope = call_envelope("get_stock_indicators", {"symbol": "TCS"})
    result = envelope["data"]

    by_period = {item["period"]: item for item in result["ema"]}
    assert by_period[20]["value"] is not None
    assert by_period[50]["value"] is not None
    assert by_period[200]["value"] is None
    assert by_period[200]["close_vs_ema_percent"] is None
    short = [w for w in envelope["warnings"] if w["code"] == "short_history"]
    assert len(short) == 1
    assert "EMA 200" in short[0]["message"]
    assert "RSI" not in short[0]["message"]
    assert short[0]["symbols"] == ["TCS"]


def test_values_are_consistent_with_each_other(dataset):
    rows = make_rows("TCS", SESSIONS, volume=100_000)
    dataset(rows)

    result = call_envelope("get_stock_indicators", {"symbol": "TCS"})["data"]

    assert 0 <= result["rsi"]["value"] <= 100
    assert result["atr"]["value"] > 0
    assert result["atr"]["percent_of_close"] == pytest.approx(
        result["atr"]["value"] / result["close"] * 100, abs=0.01
    )
    ema_20 = result["ema"][0]
    assert ema_20["close_vs_ema_percent"] == pytest.approx(
        (result["close"] / ema_20["value"] - 1) * 100, abs=0.01
    )
    traded = result["average_traded_value"]
    assert traded["crore"] == pytest.approx(traded["rupees"] / 10_000_000, abs=0.01)


def test_the_response_reports_the_settings_it_used(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))

    parameters = call_envelope("get_stock_indicators", {"symbol": "tcs.ns"})[
        "parameters"
    ]

    assert parameters == {
        "symbol": "TCS",
        "ema_periods": [20, 50, 200],
        "rsi_period": 14,
        "atr_period": 14,
        "traded_value_sessions": 20,
        "illiquid_below_crore": 1.0,
    }


def test_arguments_override_the_saved_settings_for_one_question(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))
    call_envelope("update_indicator_settings", {"changes": {"rsi_period": 21}})

    saved = call_envelope("get_stock_indicators", {"symbol": "TCS"})
    overridden = call_envelope(
        "get_stock_indicators", {"symbol": "TCS", "rsi_period": 7}
    )
    after = call_envelope("get_stock_indicators", {"symbol": "TCS"})

    assert saved["data"]["rsi"]["period"] == 21
    assert overridden["data"]["rsi"]["period"] == 7
    assert overridden["data"]["rsi"]["value"] != saved["data"]["rsi"]["value"]
    assert after["data"]["rsi"]["period"] == 21  # the override was not saved


def test_saved_ema_periods_are_used(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))
    call_envelope("update_indicator_settings", {"changes": {"ema_periods": [5, 10]}})

    result = call_envelope("get_stock_indicators", {"symbol": "TCS"})["data"]

    assert [item["period"] for item in result["ema"]] == [5, 10]


def test_a_thinly_traded_stock_is_flagged_illiquid(dataset):
    # 1,000 rupees x 500 shares = Rs 5 lakh = 0.05 crore a day
    dataset(make_rows("THIN", SESSIONS, volume=500))

    envelope = call_envelope(
        "get_stock_indicators", {"symbol": "THIN", "ema_periods": [20]}
    )

    assert codes(envelope) == ["illiquid"]
    assert envelope["warnings"][0]["symbols"] == ["THIN"]
    assert envelope["data"]["average_traded_value"]["crore"] < 1


def test_the_illiquid_limit_comes_from_the_settings(dataset):
    dataset(make_rows("THIN", SESSIONS, volume=500))
    call_envelope(
        "update_indicator_settings", {"changes": {"illiquid_below_crore": 0.01}}
    )

    envelope = call_envelope(
        "get_stock_indicators", {"symbol": "THIN", "ema_periods": [20]}
    )

    assert "illiquid" not in codes(envelope)


def test_a_dataset_without_volume_still_gives_price_indicators(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=1), drop_volume=True)

    envelope = call_envelope(
        "get_stock_indicators", {"symbol": "TCS", "ema_periods": [20]}
    )

    assert "volume_all_missing" in codes(envelope)
    assert "illiquid" not in codes(envelope)
    assert envelope["data"]["average_traded_value"]["rupees"] is None
    assert envelope["data"]["rsi"]["value"] is not None


def test_too_little_volume_history_is_a_short_history_warning(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))

    envelope = call_envelope(
        "get_stock_indicators",
        {"symbol": "TCS", "ema_periods": [20], "traded_value_sessions": 100},
    )

    # A valid setting (up to 250) that this stock's history cannot satisfy.
    assert envelope["data"]["average_traded_value"]["rupees"] is None
    assert "average traded value 100" in envelope["warnings"][0]["message"]


def test_an_unknown_symbol_is_an_error(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))

    with pytest.raises(ToolError, match="not found"):
        call_envelope("get_stock_indicators", {"symbol": "NOPE"})


def test_an_invalid_override_is_an_error(dataset):
    dataset(make_rows("TCS", SESSIONS, volume=100_000))

    with pytest.raises(ToolError, match="between 2 and 100"):
        call_envelope("get_stock_indicators", {"symbol": "TCS", "rsi_period": 1})


# --- the settings tools --------------------------------------------------------


def test_get_indicator_settings_shows_defaults_when_nothing_is_saved():
    envelope = call_envelope("get_indicator_settings")

    assert envelope["data"]["customised"] == []
    assert envelope["data"]["settings"] == envelope["data"]["defaults"]
    assert envelope["source"]["kind"] == "local_file"
    assert envelope["source"]["file_name"] == "indicator_settings.json"


def test_update_then_get_then_reset_round_trip():
    updated = call_envelope(
        "update_indicator_settings",
        {"changes": {"rsi_period": 21, "near_high_percent": 3}},
    )
    assert updated["data"]["changed"] == ["near_high_percent", "rsi_period"]
    assert updated["data"]["customised"] == ["near_high_percent", "rsi_period"]
    assert updated["data"]["settings"]["near_high_percent"] == 3.0

    shown = call_envelope("get_indicator_settings")["data"]
    assert shown["settings"]["rsi_period"] == 21
    assert shown["customised"] == ["near_high_percent", "rsi_period"]

    partly = call_envelope("reset_indicator_settings", {"names": ["rsi_period"]})
    assert partly["data"]["reset"] == ["rsi_period"]
    assert partly["data"]["customised"] == ["near_high_percent"]

    everything = call_envelope("reset_indicator_settings")
    assert everything["data"]["reset"] == ["near_high_percent"]
    assert everything["data"]["customised"] == []


def test_an_invalid_update_is_an_error_and_saves_nothing():
    with pytest.raises(ToolError, match="between 2 and 100"):
        call_envelope("update_indicator_settings", {"changes": {"rsi_period": 1}})

    assert call_envelope("get_indicator_settings")["data"]["customised"] == []


def test_resetting_an_unknown_setting_is_an_error():
    with pytest.raises(ToolError, match="Unknown setting"):
        call_envelope("reset_indicator_settings", {"names": ["nonsense"]})
