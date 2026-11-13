"""The response envelope shared by every MCP tool."""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, NotRequired, TypedDict

ENVELOPE_VERSION = 1


class WarningCode(StrEnum):
    """Every warning code a tool is allowed to emit."""

    STALE_DATA = "stale_data"
    SHORT_HISTORY = "short_history"
    INSUFFICIENT_SESSIONS = "insufficient_sessions"
    UNADJUSTED_PRICES = "unadjusted_prices"
    PRICES_ADJUSTED = "prices_adjusted"
    PRICE_ADJUSTMENT_UNKNOWN = "price_adjustment_unknown"
    ILLIQUID = "illiquid"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    VOLUME_COLUMN_MISSING = "volume_column_missing"
    VOLUME_ALL_MISSING = "volume_all_missing"
    DUPLICATE_ROWS = "duplicate_rows"
    IMPOSSIBLE_PRICES = "impossible_prices"
    INVALID_ROWS = "invalid_rows"
    LARGE_PRICE_MOVE = "large_price_move"
    MISSING_TRADING_DAYS = "missing_trading_days"
    NO_USABLE_DATA = "no_usable_data"
    UNIVERSE_OUTDATED = "universe_outdated"
    UNIVERSE_SYMBOLS_MISSING = "universe_symbols_missing"
    SYMBOLS_UNCHECKED = "symbols_unchecked"
    BENCHMARK_APPROXIMATE = "benchmark_approximate"
    BENCHMARK_UNAVAILABLE = "benchmark_unavailable"
    DATE_ADJUSTED = "date_adjusted"
    DIFFERENT_HISTORIES = "different_histories"


class ResponseWarning(TypedDict):
    code: str
    message: str
    symbols: NotRequired[list[str]]


class ResponseSource(TypedDict):
    kind: str
    file_name: str | None
    format: str | None
    file_modified: NotRequired[str | None]  # date the dataset file last changed


class Envelope(TypedDict):
    envelope_version: int
    data: dict[str, Any]
    source: ResponseSource
    as_of: str | None
    parameters: dict[str, Any]
    warnings: list[ResponseWarning]


def make_warning(
    code: WarningCode, message: str, symbols: Iterable[str] | None = None
) -> ResponseWarning:
    """Build one warning; the code must be a registered WarningCode."""
    warning: ResponseWarning = {"code": WarningCode(code).value, "message": message}
    if symbols is not None:
        warning["symbols"] = list(symbols)
    return warning


def build_response(
    data: Mapping[str, Any],
    *,
    source: ResponseSource,
    as_of: str | None,
    parameters: Mapping[str, Any],
    warnings: Iterable[ResponseWarning] = (),
) -> Envelope:
    """Wrap a tool result in the standard envelope.

    Every field except warnings is a required keyword, so a tool cannot leave
    one out. Warning codes are checked against the WarningCode registry.
    """
    checked = []
    for warning in warnings:
        WarningCode(warning["code"])
        checked.append(warning)
    return {
        "envelope_version": ENVELOPE_VERSION,
        "data": dict(data),
        "source": source,
        "as_of": as_of,
        "parameters": dict(parameters),
        "warnings": checked,
    }
