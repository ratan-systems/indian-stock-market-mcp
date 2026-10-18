"""Turn the ways a stock symbol is written into one canonical symbol."""

# A prefix needs its colon, so a symbol that is literally BSE is left alone.
EXCHANGE_PREFIXES = ("NSE:", "BSE:")
EXCHANGE_SUFFIXES = (".NS", ".BO")


def normalize_symbol(symbol: str) -> str:
    """Return the canonical form of a symbol.

    Strips spaces, uppercases, and removes one exchange prefix (NSE:, BSE:)
    and one exchange suffix (.NS, .BO). Symbols such as M&M, BAJAJ-AUTO and BSE
    are kept as they are. Raises ValueError for anything that is not a
    non-empty string, or that is empty once the exchange marker is removed.
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
    return cleaned
