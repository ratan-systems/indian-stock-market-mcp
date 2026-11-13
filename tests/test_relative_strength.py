"""Tests of relative strength against benchmarks the user chooses."""

import asyncio
import json

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import adjustments, bhavcopy, data, index_closes
from indian_stock_market_mcp import server as server_module
from indian_stock_market_mcp.indicators import (
    SeriesCoverageError,
    level_return,
    period_return,
)
from indian_stock_market_mcp.universes import get_universe
from indian_stock_market_mcp.watchlists import create_watchlist

SESSIONS = 130
DAYS = pd.bdate_range("2025-09-01", periods=SESSIONS)
# Daily growth rates: HDFC Bank fastest, Kotak flat.
RATES = {"HDFCBANK": 0.0010, "ICICIBANK": 0.0005, "AXISBANK": 0.0002, "KOTAKBANK": 0.0}
INDEX_RATE = 0.0003  # the official Nifty Private Bank grows 0.03% a day
PERIODS = {"1m": 21, "3m": 63, "6m": 126}


def growth(rate, count=SESSIONS, start=100.0):
    return [start * (1 + rate) ** i for i in range(count)]


def rows_for(symbol, closes, days=DAYS):
    return [
        {
            "date": day,
            "symbol": symbol,
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1000.0,
        }
        for day, c in zip(days, closes, strict=True)
    ]


def expected_return(rate, sessions):
    """Percent growth over `sessions` sessions at a constant daily rate."""
    return round(((1 + rate) ** sessions - 1) * 100, 2)


def call_envelope(name, arguments=None):
    result = asyncio.run(server_module.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def codes(envelope):
    return [warning["code"] for warning in envelope["warnings"]]


def write_index(tmp_path, monkeypatch, sessions=SESSIONS, name="Nifty Private Bank"):
    frame = pd.DataFrame(
        {
            "date": DAYS[:sessions],
            "index": name,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": growth(INDEX_RATE, sessions, start=1000.0),
        }
    )
    path = tmp_path / "index_closes.parquet"
    index_closes.write_output(frame, path)
    monkeypatch.setenv(index_closes.INDEX_PATH_ENV, str(path))


@pytest.fixture
def market(tmp_path, monkeypatch):
    """Four private banks over 130 sessions and an official Private Bank index."""
    assert set(RATES) <= set(get_universe("NIFTY_PRIVATE_BANK").symbols)
    rows = []
    for symbol, rate in RATES.items():
        rows += rows_for(symbol, growth(rate))
    path = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    write_index(tmp_path, monkeypatch)
    return tmp_path


def add_stock(market, symbol, closes, days):
    """Add a stock to the market's price file."""
    path = market / "prices.csv"
    frame = pd.concat(
        [
            pd.read_csv(path, parse_dates=["date"]),
            pd.DataFrame(rows_for(symbol, closes, days)),
        ]
    )
    frame.to_csv(path, index=False)


# --- The return calculations --------------------------------------------------------


def test_a_period_return_is_the_change_over_that_many_sessions():
    close = pd.Series([100.0, 110.0, 99.0, 121.0])

    assert period_return(close, 1) == pytest.approx((121 / 99 - 1) * 100)
    assert period_return(close, 3) == pytest.approx(21.0)


def test_a_period_needs_one_more_session_than_its_length():
    from indian_stock_market_mcp.indicators import InsufficientHistoryError

    with pytest.raises(InsufficientHistoryError):
        period_return(pd.Series([1.0, 2.0, 3.0]), 3)


def test_an_index_return_uses_the_values_on_the_two_dates():
    levels = pd.Series([100.0, 110.0, 121.0], index=DAYS[:3])

    assert level_return(levels, DAYS[0], DAYS[2]) == pytest.approx(21.0)
    assert level_return(levels, DAYS[1], DAYS[2]) == pytest.approx(10.0)


def test_an_index_that_stops_early_or_starts_late_is_refused_not_stretched():
    levels = pd.Series([100.0, 110.0, 121.0], index=DAYS[:3])

    with pytest.raises(SeriesCoverageError, match="end on"):
        level_return(levels, DAYS[0], DAYS[5])
    with pytest.raises(SeriesCoverageError, match="start on"):
        level_return(levels, DAYS[0] - pd.Timedelta(days=7), DAYS[2])
    with pytest.raises(SeriesCoverageError, match="no values"):
        level_return(levels.iloc[:0], DAYS[0], DAYS[2])


# --- One stock ---------------------------------------------------------------------------


def test_the_defaults_measure_a_stock_against_nifty_50_its_size_and_its_sector(market):
    envelope = call_envelope("get_relative_strength", {"symbol": "HDFCBANK"})
    result = envelope["data"]

    assert result["symbol"] == "HDFCBANK"
    assert result["as_of"] == DAYS[-1].strftime("%Y-%m-%d")
    assert [(b["id"], b["kind"]) for b in result["benchmarks"]] == [
        ("NIFTY_50", "approximate"),
        ("NIFTY_100", "approximate"),
        ("NIFTY_PRIVATE_BANK", "official"),
    ]
    assert [b["segment"] for b in result["benchmarks"]] == [
        None,
        "large",
        "Nifty Private Bank",
    ]
    assert [r["period"] for r in result["results"]] == ["1m", "3m", "6m"]
    assert envelope["parameters"]["benchmarks"] == [
        "NIFTY_50",
        "auto_size",
        "auto_sector",
    ]


@pytest.mark.parametrize("period", ["1m", "3m", "6m"])
def test_returns_and_excess_returns_match_the_formula(market, period):
    sessions = PERIODS[period]
    result = call_envelope("get_relative_strength", {"symbol": "HDFCBANK"})["data"]

    row = next(r for r in result["results"] if r["period"] == period)
    official = next(b for b in row["benchmarks"] if b["id"] == "NIFTY_PRIVATE_BANK")
    stock = expected_return(RATES["HDFCBANK"], sessions)
    index = expected_return(INDEX_RATE, sessions)
    assert row["sessions"] == sessions
    assert row["start_date"] == DAYS[-1 - sessions].strftime("%Y-%m-%d")
    assert row["end_date"] == DAYS[-1].strftime("%Y-%m-%d")
    assert row["stock_return_percent"] == pytest.approx(stock, abs=0.011)
    assert official["return_percent"] == pytest.approx(index, abs=0.011)
    assert official["excess_percent"] == pytest.approx(stock - index, abs=0.02)


def test_every_benchmark_that_is_not_official_says_so_with_the_reason(market):
    envelope = call_envelope("get_relative_strength", {"symbol": "HDFCBANK"})

    approximate = [
        w for w in envelope["warnings"] if w["code"] == "benchmark_approximate"
    ]
    assert len(approximate) == 2  # Nifty 50 and Nifty 100; Private Bank is official
    assert "Nifty 50" in approximate[0]["message"]
    assert "The index file has no values for Nifty 50" in approximate[0]["message"]
    assert "equal-weight average" in approximate[0]["message"]
    assert all("Private Bank" not in w["message"] for w in approximate)


def test_benchmarks_can_be_chosen_per_question_and_several_at_once(market):
    envelope = call_envelope(
        "get_relative_strength",
        {"symbol": "ICICIBANK", "benchmarks": ["nifty private bank", "nifty 50"]},
    )

    assert [b["id"] for b in envelope["data"]["benchmarks"]] == [
        "NIFTY_PRIVATE_BANK",
        "NIFTY_50",
    ]
    assert envelope["parameters"]["benchmarks"] == ["NIFTY_PRIVATE_BANK", "NIFTY_50"]
    assert codes(envelope) == ["benchmark_approximate"]  # only Nifty 50 is an average


def test_the_periods_can_be_chosen_per_question(market):
    envelope = call_envelope(
        "get_relative_strength",
        {
            "symbol": "HDFCBANK",
            "benchmarks": ["NIFTY_PRIVATE_BANK"],
            "periods": {"2w": 10},
        },
    )

    assert [(r["period"], r["sessions"]) for r in envelope["data"]["results"]] == [
        ("2w", 10)
    ]


def test_saved_settings_supply_the_defaults(market):
    call_envelope(
        "update_indicator_settings",
        {
            "changes": {
                "default_benchmarks": ["nifty private bank"],
                "relative_strength_periods": {"3m": 63},
            }
        },
    )

    envelope = call_envelope("get_relative_strength", {"symbol": "HDFCBANK"})

    assert [b["id"] for b in envelope["data"]["benchmarks"]] == ["NIFTY_PRIVATE_BANK"]
    assert [r["period"] for r in envelope["data"]["results"]] == ["3m"]
    assert envelope["warnings"] == []


def test_a_watchlist_can_be_a_benchmark(market):
    create_watchlist("mine", ["ICICIBANK", "AXISBANK"])

    envelope = call_envelope(
        "get_relative_strength", {"symbol": "HDFCBANK", "benchmarks": ["mine"]}
    )

    assert envelope["data"]["benchmarks"][0]["id"] == "MINE"
    assert "A watchlist has no official index" in envelope["warnings"][0]["message"]


def test_the_same_index_named_twice_is_measured_once(market):
    envelope = call_envelope(
        "get_relative_strength",
        {"symbol": "HDFCBANK", "benchmarks": ["nifty private bank", "auto_sector"]},
    )

    assert [b["key"] for b in envelope["data"]["benchmarks"]] == ["NIFTY_PRIVATE_BANK"]


def test_a_stock_with_little_history_gets_nulls_and_a_warning_for_long_periods(market):
    add_stock(market, "NEWCO", growth(0.001, 30), DAYS[-30:])

    envelope = call_envelope(
        "get_relative_strength",
        {"symbol": "NEWCO", "benchmarks": ["NIFTY_PRIVATE_BANK"]},
    )

    by_period = {r["period"]: r for r in envelope["data"]["results"]}
    assert by_period["1m"]["stock_return_percent"] is not None
    assert by_period["3m"]["stock_return_percent"] is None
    assert by_period["3m"]["benchmarks"] == []
    short = [w for w in envelope["warnings"] if w["code"] == "short_history"]
    assert "3m" in short[0]["message"] and "6m" in short[0]["message"]
    assert "1m" not in short[0]["message"]


def test_a_benchmark_with_no_match_for_the_stock_is_reported_not_fatal(market):
    add_stock(market, "NEWCO", growth(0.001, 60), DAYS[-60:])

    envelope = call_envelope("get_relative_strength", {"symbol": "NEWCO"})

    assert [b["id"] for b in envelope["data"]["benchmarks"]] == ["NIFTY_50"]
    unavailable = [
        w["message"]
        for w in envelope["warnings"]
        if w["code"] == "benchmark_unavailable"
    ]
    assert any("auto_size" in m and "NEWCO has no size index" in m for m in unavailable)
    assert any(
        "auto_sector" in m and "NEWCO has no sector index" in m for m in unavailable
    )


def test_when_no_benchmark_can_be_used_it_is_an_error(market):
    add_stock(market, "NEWCO", growth(0.001, 60), DAYS[-60:])

    with pytest.raises(ToolError, match="None of the benchmarks could be used"):
        call_envelope(
            "get_relative_strength",
            {"symbol": "NEWCO", "benchmarks": ["auto_size", "auto_sector"]},
        )


def test_an_unknown_benchmark_name_is_a_warning_when_others_work(market):
    envelope = call_envelope(
        "get_relative_strength",
        {"symbol": "HDFCBANK", "benchmarks": ["nifty private bank", "no such index"]},
    )

    assert [b["id"] for b in envelope["data"]["benchmarks"]] == ["NIFTY_PRIVATE_BANK"]
    assert "Unknown universe or watchlist" in envelope["warnings"][0]["message"]
    assert envelope["warnings"][0]["code"] == "benchmark_unavailable"


def test_an_index_file_that_ends_early_cannot_measure_the_latest_period(
    tmp_path, market, monkeypatch
):
    write_index(tmp_path, monkeypatch, sessions=SESSIONS - 5)

    envelope = call_envelope(
        "get_relative_strength",
        {
            "symbol": "HDFCBANK",
            "benchmarks": ["NIFTY_PRIVATE_BANK"],
            "periods": {"1m": 21},
        },
    )

    entry = envelope["data"]["results"][0]["benchmarks"][0]
    assert entry["return_percent"] is None and entry["excess_percent"] is None
    assert "values end on" in entry["reason"]
    assert codes(envelope) == ["benchmark_unavailable"]
    assert envelope["data"]["results"][0]["stock_return_percent"] is not None


def test_a_missing_index_file_falls_back_for_everything_with_the_reason(
    market, monkeypatch
):
    monkeypatch.delenv(index_closes.INDEX_PATH_ENV)

    envelope = call_envelope("get_relative_strength", {"symbol": "HDFCBANK"})

    assert {b["kind"] for b in envelope["data"]["benchmarks"]} == {"approximate"}
    assert all(
        "No index file is configured" in w["message"]
        for w in envelope["warnings"]
        if w["code"] == "benchmark_approximate"
    )


def test_invalid_inputs_are_errors(market):
    with pytest.raises(ToolError, match="not found"):
        call_envelope("get_relative_strength", {"symbol": "NOPE"})
    with pytest.raises(ToolError, match="1 to 5 items"):
        call_envelope(
            "get_relative_strength",
            {"symbol": "HDFCBANK", "benchmarks": list("ABCDEF")},
        )
    with pytest.raises(ToolError, match="between 5 and 504"):
        call_envelope(
            "get_relative_strength", {"symbol": "HDFCBANK", "periods": {"1m": 1}}
        )


# --- Ranking a universe -------------------------------------------------------------------


@pytest.fixture
def banks(market):
    create_watchlist("banks", list(RATES))
    return market


def test_stocks_are_ranked_by_their_excess_return_over_the_first_benchmark(banks):
    envelope = call_envelope(
        "rank_relative_strength",
        {"universe": "banks", "benchmarks": ["nifty private bank"], "period": "1m"},
    )
    result = envelope["data"]

    assert [r["symbol"] for r in result["rankings"]] == [
        "HDFCBANK",
        "ICICIBANK",
        "AXISBANK",
        "KOTAKBANK",
    ]
    assert (result["universe"], result["period"], result["sessions"]) == (
        "BANKS",
        "1m",
        21,
    )
    assert result["rank_by"] == "NIFTY_PRIVATE_BANK"
    top = result["rankings"][0]
    index = expected_return(INDEX_RATE, 21)
    assert top["stock_return_percent"] == pytest.approx(
        expected_return(RATES["HDFCBANK"], 21), abs=0.011
    )
    assert top["excess_percent"] == pytest.approx(
        top["stock_return_percent"] - index, abs=0.02
    )
    assert (
        result["rankings"][-1]["excess_percent"] < 0
    )  # Kotak is flat, the index grows
    assert envelope["warnings"] == []


def test_several_benchmarks_are_shown_side_by_side_and_one_orders_the_list(banks):
    # Against the average of the four banks the ordering is the same, but the
    # numbers differ from the official index, so the choice shows in the figures.
    by_index = call_envelope(
        "rank_relative_strength",
        {
            "universe": "banks",
            "benchmarks": ["nifty private bank", "banks"],
            "period": "1m",
        },
    )["data"]
    by_average = call_envelope(
        "rank_relative_strength",
        {
            "universe": "banks",
            "benchmarks": ["nifty private bank", "banks"],
            "period": "1m",
            "rank_by": "banks",
        },
    )["data"]

    first = by_index["rankings"][0]
    assert [b["id"] for b in first["benchmarks"]] == ["NIFTY_PRIVATE_BANK", "BANKS"]
    assert by_average["rank_by"] == "BANKS"
    assert (
        by_average["rankings"][0]["excess_percent"]
        == first["benchmarks"][1]["excess_percent"]
    )
    assert by_average["rankings"][0]["excess_percent"] != first["excess_percent"]
    assert {b["kind"] for b in by_index["benchmarks"]} == {"official", "approximate"}


def test_top_n_limits_the_list_but_not_what_is_checked(banks):
    result = call_envelope(
        "rank_relative_strength",
        {
            "universe": "banks",
            "benchmarks": ["nifty private bank"],
            "top_n": 2,
            "period": "1m",
        },
    )["data"]

    assert [r["symbol"] for r in result["rankings"]] == ["HDFCBANK", "ICICIBANK"]
    assert result["checked"] == 4


def test_the_default_period_is_three_months(banks):
    result = call_envelope(
        "rank_relative_strength",
        {"universe": "banks", "benchmarks": ["nifty private bank"]},
    )["data"]

    assert (result["period"], result["sessions"]) == ("3m", 63)


def test_auto_benchmarks_pick_each_stocks_own_index(banks):
    envelope = call_envelope(
        "rank_relative_strength",
        {
            "universe": "banks",
            "benchmarks": ["auto_sector"],
            "period": "1m",
            "rank_by": "AUTO_SECTOR",
        },
    )

    assert envelope["data"]["rank_by"] == "auto_sector"
    assert {e["id"] for r in envelope["data"]["rankings"] for e in r["benchmarks"]} == {
        "NIFTY_PRIVATE_BANK"
    }


def test_skipped_stocks_are_listed_with_a_code_and_warned_about(banks):
    add_stock(banks, "NEWCO", growth(0.001, 30), DAYS[-30:])  # too short for 3m
    add_stock(banks, "OLDCO", growth(0.001, 100), DAYS[:100])  # data stops early
    create_watchlist("mixed", [*RATES, "NEWCO", "OLDCO", "GONE"])

    envelope = call_envelope(
        "rank_relative_strength",
        {"universe": "mixed", "benchmarks": ["nifty private bank"]},
    )

    assert {s["symbol"]: s["code"] for s in envelope["data"]["skipped"]} == {
        "NEWCO": "short_history",
        "OLDCO": "stale_data",
        "GONE": "symbol_not_found",
    }
    assert {"short_history", "stale_data", "symbol_not_found"} <= set(codes(envelope))
    assert envelope["data"]["checked"] == 4


def test_a_stock_with_no_rank_by_benchmark_is_skipped_for_that_reason(banks):
    add_stock(banks, "NEWCO", growth(0.001, 60), DAYS[-60:])
    create_watchlist("mixed", ["HDFCBANK", "NEWCO"])

    envelope = call_envelope(
        "rank_relative_strength",
        {"universe": "mixed", "benchmarks": ["auto_sector"], "period": "1m"},
    )

    assert [r["symbol"] for r in envelope["data"]["rankings"]] == ["HDFCBANK"]
    assert envelope["data"]["skipped"][0]["symbol"] == "NEWCO"
    assert envelope["data"]["skipped"][0]["code"] == "benchmark_unavailable"
    assert "benchmark_unavailable" in codes(envelope)


def test_every_stock_without_a_matching_index_is_named_in_one_warning(banks):
    add_stock(banks, "NEWCO", growth(0.001, 60), DAYS[-60:])
    add_stock(banks, "NEWER", growth(0.001, 60), DAYS[-60:])
    create_watchlist("mixed", ["HDFCBANK", "NEWCO", "NEWER"])

    envelope = call_envelope(
        "rank_relative_strength",
        {
            "universe": "mixed",
            "benchmarks": ["nifty private bank", "auto_sector"],
            "period": "1m",
        },
    )

    unavailable = [
        w for w in envelope["warnings"] if w["code"] == "benchmark_unavailable"
    ]
    assert len(unavailable) == 1
    assert unavailable[0]["message"] == "auto_sector: 2 stock(s) have no sector index."
    assert unavailable[0]["symbols"] == ["NEWCO", "NEWER"]
    # They are still ranked against the benchmark they do have.
    assert {r["symbol"] for r in envelope["data"]["rankings"]} == {
        "HDFCBANK",
        "NEWCO",
        "NEWER",
    }


def test_ranking_inputs_are_validated(banks):
    with pytest.raises(ToolError, match="Unknown period '9y'"):
        call_envelope("rank_relative_strength", {"universe": "banks", "period": "9y"})
    with pytest.raises(ToolError, match="must be one of the benchmarks"):
        call_envelope(
            "rank_relative_strength",
            {"universe": "banks", "benchmarks": ["nifty 50"], "rank_by": "nifty bank"},
        )
    with pytest.raises(ToolError, match="top_n"):
        call_envelope("rank_relative_strength", {"universe": "banks", "top_n": 0})
    with pytest.raises(ToolError, match="Unknown universe"):
        call_envelope("rank_relative_strength", {"universe": "nowhere"})


# --- Adjusted prices -------------------------------------------------------------------------


def test_a_split_does_not_distort_relative_strength_once_adjusted(
    tmp_path, monkeypatch
):
    path = tmp_path / "split.parquet"
    grown = [400.0 * (1.001**i) for i in range(60)]
    plain = [100.0 * (1.001**i) for i in range(60)]
    # Raw: the stock fell 4:1 on session 40 (a split), then kept growing.
    raw = [c if i < 40 else c / 4 for i, c in enumerate(grown)]
    frame = pd.DataFrame(
        rows_for("SPLITCO", raw, DAYS[:60]) + rows_for("PLAINCO", plain, DAYS[:60])
    )
    bhavcopy.write_output(frame, "NSE", path)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    monkeypatch.delenv(index_closes.INDEX_PATH_ENV, raising=False)
    create_watchlist("pair", ["SPLITCO", "PLAINCO"])

    def ask():
        return call_envelope(
            "get_relative_strength",
            {"symbol": "SPLITCO", "benchmarks": ["pair"], "periods": {"1m": 21}},
        )

    # The 21-session window runs from session 38 to 59 and contains the split.
    before = ask()
    assert before["data"]["results"][0]["stock_return_percent"] == pytest.approx(
        (raw[59] / raw[38] - 1) * 100, abs=0.01
    )
    assert before["data"]["results"][0]["stock_return_percent"] < -70
    assert "unadjusted_prices" in codes(before)

    ex_date = DAYS[40].strftime("%Y-%m-%d")
    adjustments.write_sidecar(
        path, "NSE", {f"SPLITCO|{ex_date}": {"status": "adjusted", "factor": 4.0}}
    )
    after = ask()

    assert after["data"]["results"][0]["stock_return_percent"] == pytest.approx(
        (1.001**21 - 1) * 100, abs=0.01
    )
    assert "unadjusted_prices" not in codes(after)
