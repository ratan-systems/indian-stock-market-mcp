"""Turn the ways a stock symbol is written into one canonical symbol."""

import json
from functools import cache
from importlib.resources import files

# A prefix needs its colon, so a symbol that is literally BSE is left alone.
EXCHANGE_PREFIXES = ("NSE:", "BSE:")
EXCHANGE_SUFFIXES = (".NS", ".BO")

BSE_CODES_PATH = files("indian_stock_market_mcp").joinpath("resources/bse_codes.json")


@cache
def load_bse_codes() -> dict[str, str]:
    """Return the bundled map of BSE scrip codes to NSE symbols."""
    if not BSE_CODES_PATH.is_file():
        raise FileNotFoundError(
            "Bundled BSE scrip-code file was not found. Reinstall the package"
        )
    return json.loads(BSE_CODES_PATH.read_text())


def normalize_symbol(symbol: str) -> str:
    """Return the canonical form of a symbol.

    Strips spaces, uppercases, and removes one exchange prefix (NSE:, BSE:)
    and one exchange suffix (.NS, .BO). A BSE scrip code in the bundled mapping,
    such as 500325, becomes its NSE symbol; any other number is kept. Symbols
    such as M&M, BAJAJ-AUTO and BSE are kept as they are. Raises ValueError for
    anything that is not a non-empty string, or that is empty once the exchange
    marker is removed.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol must be a non-empty string")

    cleaned = symbol.strip().upper()
    for prefix in EXCHANGE_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned.removeprefix(prefix).strip()
            break
    for suffix in EXCHANGE_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned.removesuffix(suffix).strip()
            break

    if not cleaned:
        raise ValueError("Symbol must be a non-empty string")
    if cleaned.isdigit():
        return load_bse_codes().get(cleaned, cleaned)
    return cleaned
