"""Tests of symbol cleanup and its use by every symbol-taking tool."""

import asyncio
import json
from pathlib import Path

import pytest

from indian_stock_market_mcp import server
from indian_stock_market_mcp.symbols import normalize_symbol

SAMPLE_CSV = Path(__file__).parents[1] / "data" / "sample_equity_daily.csv"


@pytest.fixture(autouse=True)
def sample_dataset(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(SAMPLE_CSV))


@pytest.mark.parametrize(
    "raw",
    [
        "TCS",
        "tcs",
        "  tcs  ",
        "TCS.NS",
        "tcs.ns",
        "TCS.BO",
        "NSE:TCS",
        "nse:tcs",
        "BSE:TCS",
        "NSE: TCS",
        " NSE:TCS.NS ",
    ],
)
def test_every_input_form_gives_the_same_canonical_symbol(raw):
    assert normalize_symbol(raw) == "TCS"


@pytest.mark.parametrize(
    "symbol",
    ["M&M", "BAJAJ-AUTO", "BSE", "NSE", "TCS.BSE", "ETERNAL"],
)
def test_symbols_with_special_characters_or_exchange_names_are_kept(symbol):
    assert normalize_symbol(symbol) == symbol
    assert normalize_symbol(f"{symbol}.NS") == symbol
    assert normalize_symbol(f"nse:{symbol.lower()}") == symbol


def test_bse_prefix_is_removed_but_a_symbol_named_bse_is_not():
    assert normalize_symbol("BSE:BSE") == "BSE"
    assert normalize_symbol("BSE") == "BSE"


def test_numeric_input_is_not_translated():
    # BSE scrip codes are not looked up; a number is just an unknown symbol.
    assert normalize_symbol("500325") == "500325"
    assert normalize_symbol("500325.BO") == "500325"


@pytest.mark.parametrize("raw", ["", "   ", "NSE:", ".NS", "BSE: ", None, 5, ["TCS"]])
def test_empty_or_non_string_input_is_rejected(raw):
    with pytest.raises(ValueError, match="non-empty string"):
        normalize_symbol(raw)


def call_envelope(name, arguments):
    result = asyncio.run(server.mcp.call_tool(name, arguments))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


@pytest.mark.parametrize("raw", [" tcs ", "TCS.NS", "BSE:TCS", "nse:tcs.ns"])
@pytest.mark.parametrize(
    "tool", ["get_price_history", "validate_ticker", "get_stock_weekly_return"]
)
def test_every_symbol_tool_accepts_every_input_form(tool, raw):
    envelope = call_envelope(tool, {"symbol": raw})

    assert envelope["parameters"]["symbol"] == "TCS"
    assert (
        envelope["data"]["symbol" if tool != "validate_ticker" else "ticker"] == "TCS"
    )


def test_a_cleaned_unknown_symbol_is_still_not_found():
    envelope = call_envelope("validate_ticker", {"symbol": "NSE:NOPE.NS"})

    assert envelope["data"]["valid"] is False
    assert envelope["data"]["ticker"] == "NOPE"
