"""Tests of the dataset quality report, using a deliberately broken dataset."""

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import data, server
from indian_stock_market_mcp.envelope import WarningCode
from indian_stock_market_mcp.quality import build_quality_report

SAMPLE_CSV = Path(__file__).parents[1] / "data" / "sample_equity_daily.csv"
DAYS = pd.bdate_range("2026-03-02", periods=14)  # 14 trading days


def rows(symbol, days, closes):
    return [
        {
            "date": DAYS[day].strftime("%Y-%m-%d"),
            "symbol": symbol,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
        }
        for day, close in zip(days, closes, strict=True)
    ]


def steady(symbol, days=None):
    days = range(14) if days is None else days
    return rows(symbol, days, [100.0 + 0.5 * day for day in days])


def broken_dataset() -> pd.DataFrame:
    """Each symbol has exactly one problem, so every check has one culprit."""
    gap_days = [d for d in range(14) if d not in (5, 6)]
    split = rows("SPLIT", range(14), [200.0] * 8 + [100.0] * 6)
    dup = steady("DUP") + rows("DUP", [3], [101.5])
    badpx = steady("BADPX")
    badpx[4].update(high=90.0, low=100.0)  # high below low
    frames = [
        steady("GOOD"),
        steady("GAP", gap_days),
        split,
        steady("STALE", range(3)),
        steady("SHORT", range(11, 14)),
        dup,
        badpx,
    ]
    return pd.DataFrame([row for frame in frames for row in frame])


@pytest.fixture
def report():
    return build_quality_report(broken_dataset())


def finding(report, code):
    matches = [f for f in report["findings"] if f["code"] == code]
    assert len(matches) == 1, code
    return matches[0]


def test_the_broken_dataset_fails_the_verdict_with_every_expected_reason(report):
    assert report["backtest_ready"] is False
    codes = {f["code"]: f["blocking"] for f in report["findings"]}
    assert codes == {
        "duplicate_rows": True,
        "impossible_prices": True,
        "large_price_move": True,
        "short_history": False,
        "missing_trading_days": False,
        "stale_data": False,
    }


def test_duplicate_rows_are_found(report):
    found = finding(report, "duplicate_rows")

    assert (found["count"], found["symbols"]) == (1, ["DUP"])
    assert report["checks"]["duplicate_rows"]["examples"] == [
        {"symbol": "DUP", "date": "2026-03-05"}
    ]


def test_impossible_prices_are_found(report):
    found = finding(report, "impossible_prices")

    assert (found["count"], found["symbols"]) == (1, ["BADPX"])
    example = report["checks"]["impossible_prices"]["examples"][0]
    assert (example["symbol"], example["date"]) == ("BADPX", "2026-03-06")
    assert (example["high"], example["low"]) == (90.0, 100.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"low": 0.0},
        {"close": -5.0},
        {"close": float("nan")},
        {"close": 150.0},  # above the high
        {"open": 50.0, "low": 60.0},  # below the low
    ],
)
def test_each_kind_of_impossible_price_is_caught(overrides):
    frame = pd.DataFrame(steady("AAA"))
    frame.loc[3, list(overrides)] = list(overrides.values())

    report = build_quality_report(frame)

    assert finding(report, "impossible_prices")["count"] == 1


def test_missing_trading_days_are_found_inside_each_symbols_own_span(report):
    check = report["checks"]["missing_trading_days"]

    assert check["symbols_affected"] == 1
    assert check["total_missing_days"] == 2
    assert check["examples"] == [
        {
            "symbol": "GAP",
            "missing_days": 2,
            "dates": ["2026-03-09", "2026-03-10"],
        }
    ]
    # A late start or an early stop is not a gap.
    assert finding(report, "missing_trading_days")["symbols"] == ["GAP"]


def test_a_large_one_day_move_is_found(report):
    found = finding(report, "large_price_move")

    assert (found["count"], found["symbols"]) == (1, ["SPLIT"])
    assert report["checks"]["large_moves"]["examples"] == [
        {
            "symbol": "SPLIT",
            "date": "2026-03-12",
            "previous_close": 200.0,
            "close": 100.0,
            "change_percent": -50.0,
        }
    ]


def test_the_jump_threshold_is_strict_and_adjustable():
    frame = pd.DataFrame(rows("AAA", range(14), [100.0] * 7 + [125.0] * 7))

    at_threshold = build_quality_report(frame, jump_threshold_percent=25)
    below = build_quality_report(frame, jump_threshold_percent=24.9)

    assert [f["code"] for f in at_threshold["findings"]] == []
    assert finding(below, "large_price_move")["count"] == 1


def test_a_bad_row_does_not_create_a_move_or_a_gap(report):
    assert "BADPX" not in finding(report, "large_price_move")["symbols"]
    assert "BADPX" not in finding(report, "missing_trading_days")["symbols"]


def test_stale_symbols_are_found(report):
    found = finding(report, "stale_data")

    assert found["symbols"] == ["STALE"]
    assert report["checks"]["stale_symbols"]["examples"] == [
        {"symbol": "STALE", "last_date": "2026-03-04", "trading_days_since": 11}
    ]


def test_the_stale_limit_is_adjustable():
    # LATE stops 5 trading days before the dataset ends.
    frame = pd.DataFrame(steady("GOOD") + steady("LATE", range(9)))

    default = build_quality_report(frame)
    strict = build_quality_report(frame, stale_after_sessions=4)

    assert all(f["code"] != "stale_data" for f in default["findings"])
    assert finding(strict, "stale_data")["symbols"] == ["LATE"]


def test_history_length_is_reported_for_each_symbol_shortest_first(report):
    history = report["history"]

    assert (history["min_sessions"], history["max_sessions"]) == (3, 14)
    assert [(s["symbol"], s["sessions"]) for s in history["symbols"]][:3] == [
        ("SHORT", 3),
        ("STALE", 3),
        ("GAP", 12),
    ]
    short = history["symbols"][0]
    assert (short["first_date"], short["last_date"]) == ("2026-03-17", "2026-03-19")


def test_short_history_is_a_note_unless_no_symbol_has_enough(report):
    assert finding(report, "short_history") == {
        "code": "short_history",
        "blocking": False,
        "count": 2,
        "message": "2 symbol(s) have fewer than 5 sessions of history.",
        "symbols": ["SHORT", "STALE"],
    }

    none_long_enough = build_quality_report(
        pd.DataFrame(steady("AAA", range(3))), min_history_sessions=5
    )
    assert finding(none_long_enough, "short_history")["blocking"] is True
    assert none_long_enough["backtest_ready"] is False


def test_a_clean_dataset_is_backtest_ready_with_no_findings():
    frame = pd.DataFrame([row for s in ("AAA", "BBB", "CCC") for row in steady(s)])

    report = build_quality_report(frame)

    assert report["backtest_ready"] is True
    assert report["findings"] == []
    assert report["summary"]["trading_days"] == 14
    assert report["summary"]["date_range"] == {
        "start": "2026-03-02",
        "end": "2026-03-19",
    }


def test_notes_alone_do_not_fail_the_verdict():
    frame = pd.DataFrame(
        [row for s in ("AAA", "BBB") for row in steady(s)] + steady("OLD", range(3))
    )

    report = build_quality_report(frame)

    assert {f["code"] for f in report["findings"]} == {"stale_data", "short_history"}
    assert report["backtest_ready"] is True


def test_rows_with_a_missing_symbol_or_date_are_reported_and_left_out():
    frame = pd.DataFrame(steady("AAA"))
    frame.loc[len(frame)] = ["not a date", "BBB", 1, 1, 1, 1, 10]
    frame.loc[len(frame)] = ["2026-03-02", "", 1, 1, 1, 1, 10]

    report = build_quality_report(frame)

    assert finding(report, "invalid_rows")["count"] == 2
    assert report["backtest_ready"] is False
    assert report["summary"]["symbol_count"] == 1


def test_an_empty_dataset_is_not_ready_and_does_not_raise():
    report = build_quality_report(pd.DataFrame(columns=steady("A")[0].keys()))

    assert report["backtest_ready"] is False
    assert [f["code"] for f in report["findings"]] == ["no_usable_data"]


def test_a_dataset_of_only_impossible_prices_is_not_ready_and_does_not_raise():
    frame = pd.DataFrame(rows("AAA", range(3), [-1.0, -1.0, -1.0]))

    report = build_quality_report(frame)

    assert report["backtest_ready"] is False
    assert {f["code"] for f in report["findings"]} >= {
        "impossible_prices",
        "no_usable_data",
    }


@pytest.mark.parametrize(
    "settings",
    [
        {"jump_threshold_percent": 0},
        {"jump_threshold_percent": -5},
        {"jump_threshold_percent": 1001},
        {"jump_threshold_percent": True},
        {"jump_threshold_percent": "25"},
        {"min_history_sessions": 0},
        {"min_history_sessions": 2.5},
        {"stale_after_sessions": 0},
        {"stale_after_sessions": True},
    ],
)
def test_invalid_settings_are_rejected(settings):
    with pytest.raises(ValueError, match="must be"):
        build_quality_report(pd.DataFrame(steady("AAA")), **settings)


def test_the_report_is_json_safe(report):
    json.dumps(report, allow_nan=False)


# --- Through the MCP tool ----------------------------------------------------


def call_tool(arguments=None):
    result = asyncio.run(
        server.mcp.call_tool("get_data_quality_report", arguments or {})
    )
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def test_the_tool_reports_the_broken_dataset_in_the_standard_envelope(
    tmp_path, monkeypatch
):
    path = tmp_path / "broken.csv"
    broken_dataset().to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))

    envelope = call_tool({"jump_threshold_percent": 30})

    assert envelope["data"]["backtest_ready"] is False
    assert envelope["source"]["file_name"] == "broken.csv"
    assert envelope["as_of"] == "2026-03-19"
    assert envelope["parameters"] == {
        "jump_threshold_percent": 30,
        "min_history_sessions": 5,
        "stale_after_sessions": 10,
    }
    codes = {w["code"] for w in envelope["warnings"]}
    assert codes == {f["code"] for f in envelope["data"]["findings"]}
    assert all(WarningCode(code) for code in codes)
    large = next(w for w in envelope["warnings"] if w["code"] == "large_price_move")
    assert large["symbols"] == ["SPLIT"]


def test_the_tool_loads_a_dataset_that_the_other_tools_reject(tmp_path, monkeypatch):
    path = tmp_path / "broken.csv"
    broken_dataset().to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))

    with pytest.raises(ValueError, match="duplicate"):
        data.get_available_universe()
    assert call_tool()["data"]["findings"]


def test_the_tool_runs_on_the_bundled_sample(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(SAMPLE_CSV))

    envelope = call_tool()

    assert envelope["data"]["backtest_ready"] is True
    assert envelope["data"]["summary"]["symbol_count"] == 3


def test_the_tool_rejects_bad_settings_and_a_missing_dataset(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(SAMPLE_CSV))
    with pytest.raises(ToolError, match="min_history_sessions"):
        call_tool({"min_history_sessions": 0})

    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", "")
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    with pytest.raises(ToolError, match="not configured"):
        call_tool()
