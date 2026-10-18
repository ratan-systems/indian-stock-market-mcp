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
    PRICE_ADJUSTMENT_UNKNOWN = "price_adjustment_unknown"
    ILLIQUID = "illiquid"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    VOLUME_COLUMN_MISSING = "volume_column_missing"
    VOLUME_ALL_MISSING = "volume_all_missing"


class ResponseWarning(TypedDict):
    code: str
    message: str
    symbols: NotRequired[list[str]]


class ResponseSource(TypedDict):
    kind: str
    file_name: str | None
    format: str | None


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
