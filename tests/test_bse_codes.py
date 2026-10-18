"""Tests of the bundled BSE scrip-code -> NSE symbol mapping."""

import json
import re
from importlib.resources import files

BSE_CODES_PATH = files("indian_stock_market_mcp").joinpath("resources/bse_codes.json")


def load_bse_codes():
    return json.loads(BSE_CODES_PATH.read_text())


def test_bse_codes_resource_is_bundled_and_not_empty():
    assert BSE_CODES_PATH.is_file()
    assert load_bse_codes()


def test_codes_are_six_digit_strings_and_symbols_are_canonical():
    for code, symbol in load_bse_codes().items():
        assert re.fullmatch(r"\d{6}", code), code
        assert symbol == symbol.strip().upper() and symbol, symbol


def test_each_nse_symbol_appears_once():
    symbols = list(load_bse_codes().values())

    assert len(symbols) == len(set(symbols))


def test_known_large_caps_map_to_their_nse_symbols():
    codes = load_bse_codes()

    assert codes["500325"] == "RELIANCE"
    assert codes["532540"] == "TCS"
    assert codes["500520"] == "M&M"
    assert codes["532977"] == "BAJAJ-AUTO"


def test_bse_ltd_has_no_scrip_code_because_it_is_not_listed_on_bse():
    # Codes quoted online for BSE Ltd. belong to other securities in BSE's data.
    codes = load_bse_codes()

    assert "BSE" not in codes.values()
    assert codes["500180"] == "HDFCBANK"
    for wrong_code in ("532615", "532809", "538397"):
        assert wrong_code not in codes
