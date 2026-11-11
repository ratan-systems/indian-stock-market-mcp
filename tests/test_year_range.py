"""Tests of the 52-week high and low calculation and its tools."""

import asyncio
import json

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import adjustments, analysis, bhavcopy, data, server
from indian_stock_market_mcp.adjustments import ADJUSTED
from indian_stock_market_mcp.indicators import InsufficientHistoryError, year_range
from indian_stock_market_mcp.watchlists import create_watchlist

DAYS = pd.bdate_range("2026-01-01", periods=30)


def history(highs, lows=None, closes=None, start="2026-01-01"):
    """A stock's history from its highs (lows default to highs, closes to lows)."""
    lows = highs if lows is None else lows
    closes = lows if closes is None else closes
    return pd.DataFrame(
        {
            "date": pd.bdate_range(start, periods=len(highs)),
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


# --- The calculation -----------------------------------------------------------------


def test_the_range_has_the_extremes_their_dates_and_the_distances_from_the_close():
    frame = history(
        highs=[10, 12, 20, 15, 14],
        lows=[9, 11, 18, 13, 12],
        closes=[9.5, 11.5, 19, 14, 15],
    )

    result = year_range(frame)

    assert (result.high, result.low) == (20.0, 9.0)
    assert result.high_date == pd.Timestamp("2026-01-05")
    assert result.low_date == pd.Timestamp("2026-01-01")
    assert result.close == 15.0
    assert result.percent_below_high == pytest.approx(25.0)  # (20 - 15) / 20
    assert result.percent_above_low == pytest.approx(66.6667, abs=1e-3)  # (15 - 9) / 9
    assert result.sessions == 5


def test_a_stock_closing_at_its_high_is_zero_percent_below_it():
    frame = history([10, 11, 12], closes=[9.5, 10.5, 12])

    assert year_range(frame).percent_below_high == 0.0


def test_only_the_last_sessions_of_the_window_count():
    frame = history([50, 10, 11, 12], lows=[40, 9, 10, 11])

    result = year_range(frame, sessions=3)

    assert (result.high, result.low) == (12.0, 9.0)  # the 50 and 40 are too old
    assert result.sessions == 3


def test_a_history_shorter_than_the_window_uses_all_of_it():
    assert year_range(history([10, 11, 12]), sessions=252).sessions == 3


def test_the_most_recent_session_is_reported_when_extremes_tie():
    frame = history(highs=[20, 10, 20, 10], lows=[5, 9, 5, 9])

    result = year_range(frame)

    assert result.high_date == pd.Timestamp("2026-01-05")
    assert result.low_date == pd.Timestamp("2026-01-05")


@pytest.mark.parametrize(
    ("highs", "lows", "new_high", "new_low"),
    [
        ([10, 11, 12], [9, 9.5, 11], True, False),  # latest high is the highest
        ([12, 11, 10], [9, 9.5, 8], False, True),  # latest low is the lowest
        ([10, 12, 12], [9, 9, 10], True, False),  # equalling the high counts
        ([10, 12, 11], [9, 8, 8.5], False, False),  # neither
    ],
)
def test_a_new_high_or_low_is_the_latest_session_setting_the_extreme(
    highs, lows, new_high, new_low
):
    result = year_range(history(highs, lows))

    assert (result.is_new_high, result.is_new_low) == (new_high, new_low)


def test_a_single_session_is_not_enough():
    with pytest.raises(InsufficientHistoryError):
        year_range(history([10]))


# --- Scanning many stocks --------------------------------------------------------------


def test_the_scan_skips_missing_stale_and_too_short_stocks():
    rows = {
        "NOW": history([10, 11, 12, 13]),
        "OLD": history([10, 11, 12], start="2026-01-01"),  # ends a session earlier
        "ONE": history([10]).assign(date=pd.Timestamp("2026-01-06")),
    }

    records, skipped, as_of = analysis.scan_year_ranges(
        ("NOW", "OLD", "ONE", "GONE"), rows, 252
    )

    assert [r["symbol"] for r in records] == ["NOW"]
    assert {entry["symbol"]: entry["code"] for entry in skipped} == {
        "OLD": "stale_data",
        "ONE": "short_history",
        "GONE": "symbol_not_found",
    }
    assert as_of == "2026-01-06"


def test_an_empty_scan_has_no_date():
    assert analysis.scan_year_ranges(("A",), {}, 252) == (
        [],
        [
            {
                "symbol": "A",
                "code": "symbol_not_found",
                "reason": "Symbol 'A' was not found",
            }
        ],
        None,
    )


# --- The tools --------------------------------------------------------------------------


def call_envelope(name, arguments=None):
    result = asyncio.run(server.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def rows_for(symbol, closes, days=DAYS):
    """Rows with high = low = close, so every figure is easy to state."""
    return [
        {
            "date": day,
            "symbol": symbol,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000.0,
        }
        for day, close in zip(days, closes, strict=True)
    ]


@pytest.fixture
def market(tmp_path, monkeypatch):
    """Five stocks over 30 sessions, shaped to land in different lists."""
    rising = [100 + i for i in range(30)]  # new high on the last day
    peaked = [100 + i * 30 / 19 for i in range(20)] + [
        126.1
    ] * 10  # 3% under a 130 high
    falling = [100 - i for i in range(30)]  # new low on the last day
    rows = (
        rows_for("AAA", rising)
        + rows_for("BBB", peaked)
        + rows_for("CCC", falling)
        + rows_for("DDD", [50.0], days=DAYS[-1:])  # one session only
        + rows_for("OLD", rising[:25], days=DAYS[:25])  # data stops early
    )
    path = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    create_watchlist("mine", ["AAA", "BBB", "CCC", "DDD", "OLD", "GONE"])


def codes(envelope):
    return [warning["code"] for warning in envelope["warnings"]]


def test_one_stocks_range_matches_a_hand_calculation(market):
    envelope = call_envelope("get_52_week_range", {"symbol": "bbb.ns"})
    result = envelope["data"]["range"]

    assert envelope["data"]["symbol"] == "BBB"
    assert envelope["as_of"] == "2026-02-11"
    assert result["high_52w"] == 130.0
    assert result["low_52w"] == 100.0
    assert result["close"] == 126.1
    assert result["percent_below_high"] == 3.0
    assert result["percent_above_low"] == 26.1
    assert (result["is_new_high"], result["is_new_low"]) == (False, False)
    assert result["sessions"] == 30
    assert envelope["parameters"] == {"symbol": "BBB", "year_sessions": 252}


def test_a_stock_with_under_a_year_of_history_is_flagged(market):
    envelope = call_envelope("get_52_week_range", {"symbol": "AAA"})

    assert codes(envelope) == ["short_history"]
    assert "fewer than 252 sessions" in envelope["warnings"][0]["message"]
    assert envelope["warnings"][0]["symbols"] == ["AAA"]


def test_a_full_year_of_history_has_no_warning(tmp_path, monkeypatch):
    days = pd.bdate_range("2025-01-01", periods=260)
    closes = [100 + i * 0.1 for i in range(260)]
    path = tmp_path / "year.csv"
    pd.DataFrame(rows_for("FULL", closes, days=days)).to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)

    envelope = call_envelope("get_52_week_range", {"symbol": "FULL"})

    assert envelope["warnings"] == []
    assert envelope["data"]["range"]["sessions"] == 252


def test_a_shorter_year_can_be_asked_for_once_and_is_not_saved(tmp_path, monkeypatch):
    days = pd.bdate_range("2025-01-01", periods=260)
    path = tmp_path / "year.csv"
    pd.DataFrame(rows_for("FULL", [100 + i for i in range(260)], days=days)).to_csv(
        path, index=False
    )
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)

    short = call_envelope("get_52_week_range", {"symbol": "FULL", "year_sessions": 200})
    after = call_envelope("get_52_week_range", {"symbol": "FULL"})

    assert short["data"]["range"]["sessions"] == 200
    assert short["data"]["range"]["low_52w"] == 160.0  # 100 + (260 - 200)
    assert after["data"]["range"]["sessions"] == 252


def test_a_single_session_stock_gets_a_warning_instead_of_a_range(market):
    envelope = call_envelope("get_52_week_range", {"symbol": "DDD"})

    assert envelope["data"]["range"] is None
    assert codes(envelope) == ["short_history"]


def test_an_unknown_symbol_and_a_bad_window_are_errors(market):
    with pytest.raises(ToolError, match="not found"):
        call_envelope("get_52_week_range", {"symbol": "NOPE"})
    with pytest.raises(ToolError, match="between 200 and 260"):
        call_envelope("get_52_week_range", {"symbol": "AAA", "year_sessions": 50})


def test_near_the_high_lists_stocks_within_the_distance_closest_first(market):
    envelope = call_envelope("find_near_52_week_high", {"universe": "mine"})
    result = envelope["data"]

    assert [m["symbol"] for m in result["matches"]] == ["AAA", "BBB"]
    assert [m["percent_below_high"] for m in result["matches"]] == [0.0, 3.0]
    assert result["within_percent"] == 5.0
    assert result["universe"] == "MINE"
    assert (result["checked"], result["total_matches"], result["truncated"]) == (
        3,
        2,
        False,
    )
    assert envelope["as_of"] == "2026-02-11"


def test_the_distance_can_be_changed_per_question_or_saved(market):
    tight = call_envelope(
        "find_near_52_week_high", {"universe": "mine", "within_percent": 1}
    )
    wide = call_envelope(
        "find_near_52_week_high", {"universe": "mine", "within_percent": 50}
    )
    assert [m["symbol"] for m in tight["data"]["matches"]] == ["AAA"]
    assert [m["symbol"] for m in wide["data"]["matches"]] == ["AAA", "BBB", "CCC"]

    call_envelope("update_indicator_settings", {"changes": {"near_high_percent": 1}})
    saved = call_envelope("find_near_52_week_high", {"universe": "mine"})

    assert saved["parameters"]["within_percent"] == 1.0
    assert [m["symbol"] for m in saved["data"]["matches"]] == ["AAA"]


def test_results_can_be_capped_and_say_so(market):
    envelope = call_envelope(
        "find_near_52_week_high",
        {"universe": "mine", "within_percent": 50, "max_results": 2},
    )

    assert [m["symbol"] for m in envelope["data"]["matches"]] == ["AAA", "BBB"]
    assert envelope["data"]["total_matches"] == 3
    assert envelope["data"]["truncated"] is True


def test_stale_short_and_missing_stocks_are_skipped_and_warned_about(market):
    envelope = call_envelope("find_near_52_week_high", {"universe": "mine"})

    assert {e["symbol"]: e["code"] for e in envelope["data"]["skipped"]} == {
        "OLD": "stale_data",
        "DDD": "short_history",
        "GONE": "symbol_not_found",
    }
    by_code = {
        w["code"]: w for w in envelope["warnings"] if w["code"] != "short_history"
    }
    assert by_code["stale_data"]["symbols"] == ["OLD"]
    assert by_code["symbol_not_found"]["symbols"] == ["GONE"]
    year_warning = [
        w for w in envelope["warnings"] if "52-week range covers" in w["message"]
    ]
    assert year_warning[0]["symbols"] == ["AAA", "BBB"]  # only the stocks shown


def test_invalid_arguments_are_errors(market):
    with pytest.raises(ToolError, match="max_results"):
        call_envelope("find_near_52_week_high", {"universe": "mine", "max_results": 0})
    with pytest.raises(ToolError, match="between 0.5 and 50"):
        call_envelope(
            "find_near_52_week_high", {"universe": "mine", "within_percent": 80}
        )
    with pytest.raises(ToolError, match="Unknown universe"):
        call_envelope("find_near_52_week_high", {"universe": "nowhere"})


def test_new_highs_and_new_lows_are_the_stocks_that_set_one_today(market):
    highs = call_envelope("find_new_52_week_extremes", {"universe": "mine"})
    lows = call_envelope(
        "find_new_52_week_extremes", {"universe": "mine", "kind": "low"}
    )

    assert [m["symbol"] for m in highs["data"]["matches"]] == ["AAA"]
    assert highs["data"]["count"] == 1
    assert [m["symbol"] for m in lows["data"]["matches"]] == ["CCC"]
    assert lows["data"]["kind"] == "low"
    assert highs["parameters"]["kind"] == "high"


def test_an_unknown_kind_is_an_error(market):
    with pytest.raises(ToolError, match='"high" or "low"'):
        call_envelope("find_new_52_week_extremes", {"universe": "mine", "kind": "up"})


def test_a_bundled_universe_works_too_and_skips_what_the_data_lacks(market):
    envelope = call_envelope("find_near_52_week_high", {"universe": "nifty bank"})

    assert envelope["data"]["universe"] == "NIFTY_BANK"
    assert envelope["data"]["matches"] == []
    assert {e["code"] for e in envelope["data"]["skipped"]} == {"symbol_not_found"}


# --- Split adjustment ----------------------------------------------------------------------


@pytest.fixture
def split_dataset(tmp_path, monkeypatch):
    """A stock that fell from 1000 to 250 on a 4:1 split, with the event saved."""
    path = tmp_path / "split.parquet"
    days = pd.bdate_range("2026-03-02", periods=8)
    frame = pd.DataFrame(rows_for("SPLIT", [1000.0] * 5 + [250.0] * 3, days=days))
    bhavcopy.write_output(frame, "NSE", path)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    create_watchlist("splits", ["SPLIT"])
    return path


def test_a_split_looks_like_a_crash_until_it_is_adjusted(split_dataset):
    raw = call_envelope("get_52_week_range", {"symbol": "SPLIT"})
    assert raw["data"]["range"]["percent_below_high"] == 75.0
    assert "unadjusted_prices" in codes(raw)

    adjustments.write_sidecar(
        split_dataset, "NSE", {"SPLIT|2026-03-09": {"status": ADJUSTED, "factor": 4.0}}
    )

    single = call_envelope("get_52_week_range", {"symbol": "SPLIT"})
    scan = call_envelope("find_near_52_week_high", {"universe": "splits"})

    assert single["data"]["range"]["percent_below_high"] == 0.0
    assert single["data"]["range"]["high_52w"] == 250.0
    assert "unadjusted_prices" not in codes(single)
    assert [m["symbol"] for m in scan["data"]["matches"]] == ["SPLIT"]
