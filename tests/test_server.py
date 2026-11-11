import asyncio
import json
from pathlib import Path

import pytest

from indian_stock_market_mcp import server


@pytest.fixture(autouse=True)
def sample_dataset(monkeypatch):
    csv_path = Path(__file__).parents[1] / "data" / "sample_equity_daily.csv"
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(csv_path))


def _sample_symbol() -> str:
    return server.get_available_universe_data()[0]


def test_get_price_history_is_json_safe():
    response = server.get_price_history(_sample_symbol())
    json.dumps(response, allow_nan=False)


def test_rank_weekly_performers_is_json_safe():
    response = server.rank_weekly_performers()
    json.dumps(response, allow_nan=False)


def test_validate_ticker_valid_is_json_safe():
    response = server.validate_ticker(_sample_symbol())
    json.dumps(response, allow_nan=False)


def test_validate_ticker_invalid_is_json_safe():
    response = server.validate_ticker("NOTREAL")
    json.dumps(response, allow_nan=False)


def test_get_stock_weekly_return_is_json_safe():
    response = server.get_stock_weekly_return(_sample_symbol())
    json.dumps(response, allow_nan=False)


def test_get_available_universe_is_json_safe():
    response = server.get_available_universe()
    json.dumps(response, allow_nan=False)


def test_get_nifty50_universe_is_json_safe():
    response = server.get_nifty50_universe()
    json.dumps(response, allow_nan=False)


def test_get_data_capabilities_is_json_safe():
    response = server.get_data_capabilities()
    json.dumps(response, allow_nan=False)


def test_get_data_capabilities_describes_sample_dataset():
    response = server.get_data_capabilities()

    assert response["data"]["status"] in {"healthy", "incomplete"}
    assert response["source"]["file_name"] == "sample_equity_daily.csv"
    assert response["data"]["errors"] == []
    assert "price_adjustment_unknown" in [w["code"] for w in response["warnings"]]


def test_get_data_capabilities_is_registered_as_mcp_tool():
    tools = asyncio.run(server.mcp.list_tools())

    assert "get_data_capabilities" in [tool.name for tool in tools]


# --- MCP surface: tool inventory and input schemas ---------------------------

EXPECTED_TOOLS = {
    "get_price_history": {
        "properties": {
            "symbol": {"type": "string"},
            "sessions": {"type": "integer", "default": 5},
        },
        "required": ["symbol"],
    },
    "rank_weekly_performers": {
        "properties": {
            "top_n": {"type": "integer", "default": 5},
            "universe": {"type": "string", "default": "NIFTY_50"},
        },
        "required": [],
    },
    "validate_ticker": {
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    },
    "get_stock_weekly_return": {
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    },
    "get_available_universe": {"properties": {}, "required": []},
    "get_nifty50_universe": {"properties": {}, "required": []},
    "list_universes": {"properties": {}, "required": []},
    "create_watchlist": {
        "properties": {"name": {"type": "string"}, "symbols": {"type": "array"}},
        "required": ["name", "symbols"],
    },
    "get_watchlists": {
        "properties": {"name": {"type": ["string", "null"]}},
        "required": [],
    },
    "update_watchlist": {
        "properties": {
            "name": {"type": "string"},
            "add_symbols": {"type": ["array", "null"]},
            "remove_symbols": {"type": ["array", "null"]},
            "new_name": {"type": ["string", "null"]},
        },
        "required": ["name"],
    },
    "delete_watchlist": {
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
    "get_stock_indicators": {
        "properties": {
            "symbol": {"type": "string"},
            "ema_periods": {"type": ["array", "null"]},
            "rsi_period": {"type": ["integer", "null"]},
            "atr_period": {"type": ["integer", "null"]},
            "traded_value_sessions": {"type": ["integer", "null"]},
        },
        "required": ["symbol"],
    },
    "get_52_week_range": {
        "properties": {
            "symbol": {"type": "string"},
            "year_sessions": {"type": ["integer", "null"]},
        },
        "required": ["symbol"],
    },
    "find_near_52_week_high": {
        "properties": {
            "universe": {"type": "string", "default": "NIFTY_50"},
            "within_percent": {"type": ["number", "null"]},
            "max_results": {"type": "integer", "default": 50},
            "year_sessions": {"type": ["integer", "null"]},
        },
        "required": [],
    },
    "find_new_52_week_extremes": {
        "properties": {
            "universe": {"type": "string", "default": "NIFTY_50"},
            "kind": {"type": "string", "default": "high"},
            "year_sessions": {"type": ["integer", "null"]},
        },
        "required": [],
    },
    "get_indicator_settings": {"properties": {}, "required": []},
    "update_indicator_settings": {
        "properties": {"changes": {"type": "object"}},
        "required": ["changes"],
    },
    "reset_indicator_settings": {
        "properties": {"names": {"type": ["array", "null"]}},
        "required": [],
    },
    "get_data_capabilities": {"properties": {}, "required": []},
    "get_data_quality_report": {
        "properties": {
            "jump_threshold_percent": {"type": "number", "default": 25.0},
            "min_history_sessions": {"type": "integer", "default": 5},
            "stale_after_sessions": {"type": "integer", "default": 10},
        },
        "required": [],
    },
}


@pytest.fixture
def listed_tools():
    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


def test_tool_inventory_is_exactly_the_expected_set(listed_tools):
    assert set(listed_tools) == set(EXPECTED_TOOLS)


def test_removed_tool_names_are_not_offered(listed_tools):
    assert "get_weekly_performance_summary" not in listed_tools


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_tool_has_a_meaningful_description(listed_tools, tool_name):
    description = listed_tools[tool_name].description

    assert description
    assert len(description.split()) >= 5


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_tool_input_schema_matches_the_contract(listed_tools, tool_name):
    expected = EXPECTED_TOOLS[tool_name]
    schema = listed_tools[tool_name].inputSchema

    assert schema["type"] == "object"
    assert sorted(schema.get("required", [])) == sorted(expected["required"])
    assert set(schema["properties"]) == set(expected["properties"])
    for name, rules in expected["properties"].items():
        actual = schema["properties"][name]
        expected_types = (
            {rules["type"]} if isinstance(rules["type"], str) else set(rules["type"])
        )
        actual_types = (
            {actual["type"]}
            if "type" in actual
            else {option["type"] for option in actual["anyOf"]}
        )
        assert actual_types == expected_types
        assert actual.get("default") == rules.get("default")


def test_tool_names_are_unique_and_snake_case(listed_tools):
    names = [tool.name for tool in asyncio.run(server.mcp.list_tools())]

    assert len(names) == len(set(names))
    assert all(name == name.lower() and " " not in name for name in names)
