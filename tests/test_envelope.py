"""Tests of the shared response envelope and warning registry."""

import asyncio
import json
from pathlib import Path

import pytest

from indian_stock_market_mcp import server
from indian_stock_market_mcp.envelope import (
    ENVELOPE_VERSION,
    WarningCode,
    build_response,
    make_warning,
)

SAMPLE_CSV = Path(__file__).parents[1] / "data" / "sample_equity_daily.csv"
ENVELOPE_KEYS = {
    "envelope_version",
    "data",
    "source",
    "as_of",
    "parameters",
    "warnings",
}
SOURCE = {"kind": "dataset", "file_name": "x.csv", "format": "csv"}

# Arguments for tools that need them; every other tool is called with none.
TOOL_ARGUMENTS = {
    "get_price_history": {"symbol": "TCS"},
    "validate_ticker": {"symbol": "TCS"},
    "get_stock_weekly_return": {"symbol": "TCS"},
}


@pytest.fixture(autouse=True)
def sample_dataset(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(SAMPLE_CSV))


def call_envelope(name, arguments=None):
    result = asyncio.run(server.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def test_every_registered_tool_returns_the_envelope():
    tools = asyncio.run(server.mcp.list_tools())
    assert tools

    for tool in tools:
        envelope = call_envelope(tool.name, TOOL_ARGUMENTS.get(tool.name, {}))

        assert set(envelope) == ENVELOPE_KEYS, tool.name
        assert envelope["envelope_version"] == ENVELOPE_VERSION
        assert isinstance(envelope["data"], dict)
        assert set(envelope["source"]) == {"kind", "file_name", "format"}
        assert envelope["as_of"] is None or isinstance(envelope["as_of"], str)
        assert isinstance(envelope["parameters"], dict)
        for warning in envelope["warnings"]:
            WarningCode(warning["code"])


def test_every_tool_advertises_the_envelope_output_schema():
    for tool in asyncio.run(server.mcp.list_tools()):
        assert set(tool.outputSchema["properties"]) == ENVELOPE_KEYS, tool.name


def test_build_response_wraps_the_data():
    envelope = build_response(
        {"a": 1}, source=SOURCE, as_of="2026-01-02", parameters={"p": 1}
    )

    assert envelope == {
        "envelope_version": ENVELOPE_VERSION,
        "data": {"a": 1},
        "source": SOURCE,
        "as_of": "2026-01-02",
        "parameters": {"p": 1},
        "warnings": [],
    }


@pytest.mark.parametrize("missing", ["source", "as_of", "parameters"])
def test_build_response_requires_every_field(missing):
    fields = {"source": SOURCE, "as_of": None, "parameters": {}}
    del fields[missing]

    with pytest.raises(TypeError):
        build_response({}, **fields)


def test_build_response_rejects_unregistered_warning_codes():
    with pytest.raises(ValueError):
        build_response(
            {},
            source=SOURCE,
            as_of=None,
            parameters={},
            warnings=[{"code": "stale_dat", "message": "typo"}],
        )


def test_make_warning_includes_symbols_only_when_given():
    assert make_warning(WarningCode.ILLIQUID, "thin") == {
        "code": "illiquid",
        "message": "thin",
    }
    assert make_warning(WarningCode.ILLIQUID, "thin", ["AAA"])["symbols"] == ["AAA"]


def test_unknown_symbol_is_a_warning_on_validate_ticker():
    envelope = call_envelope("validate_ticker", {"symbol": "NOPE"})

    assert envelope["data"]["valid"] is False
    assert [w["code"] for w in envelope["warnings"]] == ["symbol_not_found"]
    assert envelope["warnings"][0]["symbols"] == ["NOPE"]
