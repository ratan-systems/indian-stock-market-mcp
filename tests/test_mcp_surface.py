"""Tests of the MCP-facing surface, called the way an MCP client calls it."""

import asyncio
import json
import numbers
import re
import runpy
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import data, server

SAMPLE_CSV = Path(__file__).parents[1] / "data" / "sample_equity_daily.csv"
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

DATA_TOOL_CALLS = [
    ("get_price_history", {"symbol": "TCS"}),
    ("validate_ticker", {"symbol": "TCS"}),
    ("get_stock_weekly_return", {"symbol": "TCS"}),
    ("rank_weekly_performers", {}),
    ("get_available_universe", {}),
]


@pytest.fixture(autouse=True)
def sample_dataset(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(SAMPLE_CSV))


def call_tool(name, arguments=None):
    """Call a tool through the MCP server and return its structured result."""
    result = asyncio.run(server.mcp.call_tool(name, arguments or {}))
    if isinstance(result, tuple):
        return result[1]
    return json.loads(result[0].text)


def unset_data_path(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", "")
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)


def make_rows(symbol, closes, start=date(2026, 3, 2), volume=1000):
    """Daily rows for one symbol; open, high, low and close share a value."""
    return [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "symbol": symbol,
            "open": float(close),
            "high": float(close),
            "low": float(close),
            "close": float(close),
            "volume": volume,
        }
        for offset, close in enumerate(closes)
    ]


def write_dataset(tmp_path, monkeypatch, rows, name="prices.csv", columns=None):
    """Write rows as CSV or parquet (by extension) and point the server at it."""
    frame = pd.DataFrame(rows, columns=columns)
    path = tmp_path / name
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    return path


def sample_frame():
    frame = pd.read_csv(SAMPLE_CSV)
    return frame.sort_values(["symbol", "date"]).reset_index(drop=True)


# --- Complete success-response structures ------------------------------------


def test_get_price_history_response_structure():
    response = call_tool("get_price_history", {"symbol": "tcs"})

    assert set(response) == {
        "symbol",
        "session_requested",
        "session_returned",
        "prices",
    }
    assert response["symbol"] == "TCS"
    assert response["session_requested"] == 5
    assert response["session_returned"] == 5
    assert len(response["prices"]) == 5
    for row in response["prices"]:
        assert set(row) == {"date", "symbol", "open", "high", "low", "close", "volume"}
        assert row["symbol"] == "TCS"
        assert ISO_DATE.fullmatch(row["date"])
        for field in ("open", "high", "low", "close"):
            assert isinstance(row[field], float)
        assert isinstance(row["volume"], numbers.Number)
    dates = [row["date"] for row in response["prices"]]
    assert dates == sorted(dates)


def test_get_price_history_returns_the_most_recent_sessions_from_the_data():
    expected = sample_frame().query("symbol == 'TCS'").tail(2)

    response = call_tool("get_price_history", {"symbol": "TCS", "sessions": 2})

    assert response["session_requested"] == 2
    assert response["session_returned"] == 2
    assert [row["date"] for row in response["prices"]] == expected["date"].tolist()
    assert [row["close"] for row in response["prices"]] == expected["close"].tolist()


def test_validate_ticker_response_structure():
    found = call_tool("validate_ticker", {"symbol": " tcs "})
    missing = call_tool("validate_ticker", {"symbol": "NOPE"})

    assert found == {"valid": True, "ticker": "TCS", "message": "Ticker is available"}
    assert set(missing) == {"valid", "ticker", "message"}
    assert missing["valid"] is False
    assert missing["ticker"] == "NOPE"
    assert "was not found" in missing["message"]


def test_get_stock_weekly_return_response_structure_and_math():
    tcs = sample_frame().query("symbol == 'TCS'").tail(5)
    start_close, end_close = tcs["close"].iloc[0], tcs["close"].iloc[-1]

    response = call_tool("get_stock_weekly_return", {"symbol": "tcs"})

    assert set(response) == {
        "symbol",
        "start_date",
        "end_date",
        "start_close",
        "end_close",
        "return_percent",
        "session_count",
    }
    assert response["symbol"] == "TCS"
    assert response["start_date"] == tcs["date"].iloc[0]
    assert response["end_date"] == tcs["date"].iloc[-1]
    assert response["start_close"] == start_close
    assert response["end_close"] == end_close
    assert response["return_percent"] == pytest.approx(
        (end_close - start_close) / start_close * 100
    )
    assert response["session_count"] == 5


def test_rank_weekly_performers_response_structure():
    response = call_tool("rank_weekly_performers")

    assert set(response) == {"top_n", "rankings", "skipped"}
    assert response["top_n"] == 5
    assert [item["symbol"] for item in response["rankings"]] != []
    for item in response["rankings"]:
        assert set(item) == {
            "symbol",
            "start_date",
            "end_date",
            "start_close",
            "end_close",
            "return_percent",
            "session_count",
        }
    returns = [item["return_percent"] for item in response["rankings"]]
    assert returns == sorted(returns, reverse=True)
    for item in response["skipped"]:
        assert set(item) == {"symbol", "reason"}


def test_rank_weekly_performers_accounts_for_every_nifty_symbol():
    response = call_tool("rank_weekly_performers", {"top_n": 50})

    ranked = [item["symbol"] for item in response["rankings"]]
    skipped = [item["symbol"] for item in response["skipped"]]
    assert sorted(ranked) == ["INFY", "RELIANCE", "TCS"]
    assert sorted(ranked + skipped) == sorted(data.get_nifty50_universe())
    assert not set(ranked) & set(skipped)


def test_get_available_universe_response_structure():
    response = call_tool("get_available_universe")

    assert response == {
        "universe": "configured_dataset",
        "count": 3,
        "symbols": ["INFY", "RELIANCE", "TCS"],
    }


def test_get_nifty50_universe_response_structure():
    response = call_tool("get_nifty50_universe")

    assert set(response) == {"universe", "count", "symbols"}
    assert response["universe"] == "NIFTY_50"
    assert response["count"] == 50
    assert response["symbols"] == data.get_nifty50_universe()
    assert len(set(response["symbols"])) == 50
    assert all(symbol == symbol.strip().upper() for symbol in response["symbols"])


def test_get_data_capabilities_response_structure():
    response = call_tool("get_data_capabilities")

    assert set(response) == {
        "status",
        "source",
        "schema",
        "summary",
        "capabilities",
        "data_quality",
        "errors",
        "warnings",
    }
    assert set(response["source"]) == {
        "configured",
        "available",
        "readable",
        "file_name",
        "format",
    }
    assert set(response["capabilities"]) == {
        "price_history",
        "weekly_return",
        "volume_analysis",
    }


# --- Defaults and boundaries -------------------------------------------------


@pytest.mark.parametrize("sessions", [1, 5, 100])
def test_price_history_accepts_sessions_inside_the_boundary(sessions):
    response = call_tool("get_price_history", {"symbol": "TCS", "sessions": sessions})

    assert response["session_requested"] == sessions
    assert response["session_returned"] == min(sessions, 5)


@pytest.mark.parametrize("sessions", [-1, 0, 101])
def test_price_history_rejects_sessions_outside_the_boundary(sessions):
    with pytest.raises(ToolError, match="between 1 and 100"):
        call_tool("get_price_history", {"symbol": "TCS", "sessions": sessions})


@pytest.mark.parametrize("top_n", [1, 5, 50])
def test_rank_weekly_performers_accepts_top_n_inside_the_boundary(top_n):
    response = call_tool("rank_weekly_performers", {"top_n": top_n})

    assert response["top_n"] == top_n
    assert len(response["rankings"]) == min(top_n, 3)


@pytest.mark.parametrize("top_n", [-1, 0, 51])
def test_rank_weekly_performers_rejects_top_n_outside_the_boundary(top_n):
    with pytest.raises(ToolError, match="between 1 and 50"):
        call_tool("rank_weekly_performers", {"top_n": top_n})


# --- Invalid inputs and MCP-facing failures ----------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("get_price_history", {"symbol": "NOPE"}, "Symbol 'NOPE' was not found"),
        ("get_price_history", {"symbol": "  "}, "non-empty string"),
        ("get_price_history", {}, "Field required"),
        ("get_price_history", {"symbol": "TCS", "sessions": "abc"}, "valid integer"),
        ("get_price_history", {"symbol": "TCS", "sessions": 2.5}, "valid integer"),
        ("get_price_history", {"symbol": 123}, "valid string"),
        ("validate_ticker", {"symbol": ""}, "non-empty string"),
        ("validate_ticker", {}, "Field required"),
        ("get_stock_weekly_return", {"symbol": "NOPE"}, "was not found"),
        ("get_stock_weekly_return", {"symbol": " "}, "non-empty string"),
        ("get_stock_weekly_return", {}, "Field required"),
        ("rank_weekly_performers", {"top_n": "x"}, "valid integer"),
    ],
)
def test_invalid_inputs_fail_as_tool_errors(tool, arguments, message):
    with pytest.raises(ToolError, match=message):
        call_tool(tool, arguments)


@pytest.mark.parametrize(
    "tool", ["get_weekly_performance_summary", "not_a_tool", "GET_PRICE_HISTORY"]
)
def test_unknown_tool_names_fail_as_tool_errors(tool):
    with pytest.raises(ToolError, match="Unknown tool"):
        call_tool(tool, {})


def test_symbol_with_too_few_sessions_fails_with_a_clear_message(tmp_path, monkeypatch):
    write_dataset(tmp_path, monkeypatch, make_rows("TCS", [10, 11, 12]))

    with pytest.raises(ToolError, match="need 5 sessions"):
        call_tool("get_stock_weekly_return", {"symbol": "TCS"})


@pytest.mark.parametrize(("tool", "arguments"), DATA_TOOL_CALLS)
def test_data_tools_fail_clearly_when_no_dataset_is_configured(
    monkeypatch, tool, arguments
):
    unset_data_path(monkeypatch)

    with pytest.raises(ToolError, match="INDIAN_STOCK_DATA_PATH is not configured"):
        call_tool(tool, arguments)


@pytest.mark.parametrize(("tool", "arguments"), DATA_TOOL_CALLS)
def test_data_tools_fail_clearly_when_the_file_is_missing(
    tmp_path, monkeypatch, tool, arguments
):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(tmp_path / "gone.csv"))

    with pytest.raises(ToolError, match="was not found") as error:
        call_tool(tool, arguments)
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(("tool", "arguments"), DATA_TOOL_CALLS)
def test_data_tools_fail_clearly_on_malformed_data(
    tmp_path, monkeypatch, tool, arguments
):
    rows = make_rows("TCS", [10, 11, 12, 13, 14])
    write_dataset(tmp_path, monkeypatch, rows + [rows[0]])

    with pytest.raises(ToolError, match="duplicate symbol-date"):
        call_tool(tool, arguments)


def test_nifty50_universe_works_without_any_dataset(monkeypatch):
    unset_data_path(monkeypatch)

    assert call_tool("get_nifty50_universe")["count"] == 50


# --- Ranking behavior --------------------------------------------------------


@pytest.fixture
def ranking_dataset(tmp_path, monkeypatch):
    rows = [
        *make_rows("RELIANCE", [100, 101, 102, 103, 110]),  # +10.0%
        *make_rows("TCS", [200, 200, 200, 200, 210]),  # +5.0%
        *make_rows("INFY", [50, 50, 50, 50, 55]),  # +10.0%, ties RELIANCE
        *make_rows("HDFCBANK", [100, 100, 100, 100, 100]),  # 0.0%
        *make_rows("ITC", [10, 11, 12]),  # too few sessions
        *make_rows("NOTNIFTY", [1, 2, 3, 4, 50]),  # not a Nifty 50 symbol
    ]
    return write_dataset(tmp_path, monkeypatch, rows)


def test_ranking_orders_by_return_and_breaks_ties_alphabetically(ranking_dataset):
    response = call_tool("rank_weekly_performers", {"top_n": 50})

    assert [item["symbol"] for item in response["rankings"]] == [
        "INFY",
        "RELIANCE",
        "TCS",
        "HDFCBANK",
    ]
    returns = [item["return_percent"] for item in response["rankings"]]
    assert returns == pytest.approx([10.0, 10.0, 5.0, 0.0])


def test_ranking_respects_top_n(ranking_dataset):
    response = call_tool("rank_weekly_performers", {"top_n": 2})

    assert [item["symbol"] for item in response["rankings"]] == ["INFY", "RELIANCE"]


def test_ranking_skips_thin_and_absent_symbols_with_reasons(ranking_dataset):
    response = call_tool("rank_weekly_performers", {"top_n": 50})

    skipped = {item["symbol"]: item["reason"] for item in response["skipped"]}
    assert "need 5 sessions" in skipped["ITC"]
    assert "was not found" in skipped["WIPRO"]
    assert len(skipped) == 50 - 4
    assert "NOTNIFTY" not in skipped
    assert "NOTNIFTY" not in [item["symbol"] for item in response["rankings"]]


# --- get_data_capabilities through the server --------------------------------


def codes(entries):
    return [entry["code"] for entry in entries]


def test_capabilities_for_the_bundled_sample_is_healthy():
    response = call_tool("get_data_capabilities")

    assert response["status"] == "healthy"
    assert response["source"]["file_name"] == "sample_equity_daily.csv"
    assert response["summary"] == {
        "row_count": 15,
        "symbol_count": 3,
        "date_range": {"start": "2026-07-27", "end": "2026-07-31"},
    }
    assert response["capabilities"] == {
        "price_history": True,
        "weekly_return": True,
        "volume_analysis": True,
    }
    assert response["errors"] == []
    assert codes(response["warnings"]) == ["price_adjustment_unknown"]


def test_capabilities_for_incomplete_data(tmp_path, monkeypatch):
    rows = [
        {k: v for k, v in row.items() if k != "volume"}
        for row in make_rows("TCS", [1, 2, 3, 4, 5]) + make_rows("ITC", [1, 2])
    ]
    write_dataset(tmp_path, monkeypatch, rows)

    response = call_tool("get_data_capabilities")

    assert response["status"] == "incomplete"
    assert response["errors"] == []
    assert response["capabilities"] == {
        "price_history": True,
        "weekly_return": True,
        "volume_analysis": False,
    }
    assert {"volume_column_missing", "insufficient_sessions"} <= set(
        codes(response["warnings"])
    )
    assert response["data_quality"]["symbols_with_insufficient_sessions"] == {
        "count": 1,
        "symbols": ["ITC"],
    }


def test_capabilities_for_malformed_data(tmp_path, monkeypatch):
    rows = make_rows("TCS", [10, 11, 12, 13, 14])
    bad_price = {**rows[0], "symbol": "BAD", "high": 1.0, "low": 9.0}
    write_dataset(tmp_path, monkeypatch, rows + [rows[0], bad_price])

    response = call_tool("get_data_capabilities")

    assert response["status"] == "invalid"
    assert {"duplicate_symbol_date", "inconsistent_ohlc"} <= set(
        codes(response["errors"])
    )
    assert not any(response["capabilities"].values())


def test_capabilities_when_no_dataset_is_configured(monkeypatch):
    unset_data_path(monkeypatch)

    response = call_tool("get_data_capabilities")

    assert response["status"] == "invalid"
    assert codes(response["errors"]) == ["dataset_not_configured"]
    assert response["source"]["configured"] is False


def test_capabilities_when_the_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(tmp_path / "gone.csv"))

    response = call_tool("get_data_capabilities")

    assert response["status"] == "invalid"
    assert codes(response["errors"]) == ["file_not_found"]
    assert str(tmp_path) not in json.dumps(response)


def test_capabilities_when_the_file_is_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "broken.parquet"
    path.write_bytes(b"not parquet")
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))

    response = call_tool("get_data_capabilities")

    assert response["status"] == "invalid"
    assert codes(response["errors"]) == ["unreadable_file"]
    assert str(tmp_path) not in json.dumps(response)


# --- CSV, parquet and optional volume ----------------------------------------

CLOSES = {"AAA": [10, 11, 12, 13, 14, 15], "BBB": [20, 20, 20, 20, 20, 22]}


def format_rows(with_volume):
    """Messy-but-valid rows: padded lowercase symbols, shuffled columns, extras."""
    rows = []
    for symbol, closes in CLOSES.items():
        for row in make_rows(f" {symbol.lower()} ", closes):
            row["adj_close"] = row["close"]
            if not with_volume:
                del row["volume"]
            rows.append(row)
    return rows


def format_columns(with_volume):
    columns = ["close", "adj_close", "symbol", "date", "low", "high", "open"]
    return [*columns, "volume"] if with_volume else columns


@pytest.mark.parametrize("name", ["prices.csv", "prices.parquet", "PRICES.CSV"])
@pytest.mark.parametrize("with_volume", [True, False])
def test_tools_work_across_formats_with_and_without_volume(
    tmp_path, monkeypatch, name, with_volume
):
    write_dataset(
        tmp_path,
        monkeypatch,
        format_rows(with_volume),
        name=name,
        columns=format_columns(with_volume),
    )

    assert call_tool("get_available_universe")["symbols"] == ["AAA", "BBB"]
    assert call_tool("validate_ticker", {"symbol": "aaa"})["valid"] is True

    history = call_tool("get_price_history", {"symbol": "AAA", "sessions": 3})
    assert [row["close"] for row in history["prices"]] == [13.0, 14.0, 15.0]
    for row in history["prices"]:
        assert "adj_close" not in row
        if with_volume:
            assert row["volume"] == 1000
        else:
            assert row["volume"] is None

    weekly = call_tool("get_stock_weekly_return", {"symbol": "AAA"})
    assert weekly["return_percent"] == pytest.approx((15 - 11) / 11 * 100)

    report = call_tool("get_data_capabilities")
    assert report["source"]["format"] == Path(name).suffix.lstrip(".").lower()
    assert report["capabilities"]["volume_analysis"] is with_volume
    assert report["status"] == ("healthy" if with_volume else "incomplete")
    assert ("volume_column_missing" in codes(report["warnings"])) is not with_volume


def test_csv_and_parquet_give_identical_results(tmp_path, monkeypatch):
    results = {}
    for name in ("prices.csv", "prices.parquet"):
        write_dataset(tmp_path, monkeypatch, format_rows(True), name=name)
        results[name] = [
            call_tool("get_price_history", {"symbol": "BBB", "sessions": 6}),
            call_tool("get_stock_weekly_return", {"symbol": "BBB"}),
            call_tool("rank_weekly_performers"),
        ]

    assert results["prices.csv"] == results["prices.parquet"]


@pytest.mark.parametrize("name", ["prices.csv", "prices.parquet"])
def test_partially_missing_volume_is_null_and_json_safe(tmp_path, monkeypatch, name):
    rows = make_rows("AAA", [1, 2, 3, 4, 5])
    rows[-1]["volume"] = None
    write_dataset(tmp_path, monkeypatch, rows, name=name)

    history = call_tool("get_price_history", {"symbol": "AAA", "sessions": 5})

    json.dumps(history, allow_nan=False)
    assert history["prices"][-1]["volume"] is None
    assert history["prices"][0]["volume"] == 1000
    assert call_tool("get_data_capabilities")["capabilities"]["volume_analysis"]


# --- Server entry point ------------------------------------------------------


def test_main_runs_the_server_over_stdio(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server.mcp, "run", lambda transport="stdio": calls.append(transport)
    )

    server.main()

    assert calls == ["stdio"]


def test_running_the_module_as_a_script_starts_the_server(monkeypatch):
    calls = []
    monkeypatch.setattr(
        FastMCP, "run", lambda self, transport="stdio": calls.append(transport)
    )

    # Re-executing a module that is already imported warns unless it is dropped.
    monkeypatch.delitem(sys.modules, "indian_stock_market_mcp.server")

    runpy.run_module("indian_stock_market_mcp.server", run_name="__main__")

    assert calls == ["stdio"]
