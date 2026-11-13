"""Tests of split and bonus adjustment.

Yahoo prices below were recorded from a live check on 11 November 2026 and
stand in for the network, which tests never use.
"""

import asyncio
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import adjustments, bhavcopy, cli, data, server, watchlists
from indian_stock_market_mcp.adjustments import (
    ADJUSTED,
    NO_ADJUSTMENT,
    RAW_UNCHECKED,
    UNKNOWN,
    UNVERIFIED,
    YAHOO_CHECKED,
    AdjustmentEvent,
    AdjustmentsError,
    Candidate,
)

# Bajaj Finance: 4:1 bonus plus 1:2 split, ex-date 16 June 2025 (factor 10).
# HDFC Bank: 1:1 bonus, ex-date 26 August 2025 (factor 2).
# Yahoo's own split list misses Bajaj Finance's bonus, but its prices are right.
YAHOO = {
    "BAJFINANCE.NS": {
        date(2025, 6, 11): 942.50,
        date(2025, 6, 12): 936.85,
        date(2025, 6, 13): 933.10,
        date(2025, 6, 16): 938.00,
        date(2025, 6, 17): 923.00,
    },
    "HDFCBANK.NS": {
        date(2025, 8, 21): 995.60,
        date(2025, 8, 22): 982.30,
        date(2025, 8, 25): 982.05,
        date(2025, 8, 26): 973.40,
    },
}
# The exchange's unadjusted closes around the same two events.
RAW_ROWS = [
    ("BAJFINANCE", "2025-06-13", 9331.0, 1_000_000),
    ("BAJFINANCE", "2025-06-16", 938.0, 7_000_000),
    ("HDFCBANK", "2025-08-25", 1964.1, 8_000_000),
    ("HDFCBANK", "2025-08-26", 973.4, 16_000_000),
]


class FakeYahoo:
    """A fetcher that serves recorded prices and remembers what was asked."""

    def __init__(self, prices=None):
        self.prices = YAHOO if prices is None else prices
        self.calls = []

    def __call__(self, ticker, start, end):
        self.calls.append(ticker)
        return {
            day: close
            for day, close in self.prices.get(ticker, {}).items()
            if start <= day <= end
        }


def make_frame(rows):
    frame = pd.DataFrame(rows, columns=["symbol", "date", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ("open", "high", "low"):
        frame[column] = frame["close"]
    return frame[["date", "symbol", "open", "high", "low", "close", "volume"]]


@pytest.fixture
def dataset(tmp_path):
    """An import-bhavcopy style Parquet file holding the two recorded events."""
    path = tmp_path / "nse.parquet"
    bhavcopy.write_output(make_frame(RAW_ROWS), "NSE", path)
    return path


# --- Clean ratios ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(10.02, 10.0), (9.95, 10.0), (5.04, 5.0), (1.5, 1.5), (2.49, 2.5), (1.25, 1.25)],
)
def test_a_factor_snaps_to_the_nearest_clean_ratio(value, expected):
    assert adjustments.snap_ratio(value) == expected


@pytest.mark.parametrize("value", [1.37, 1.375, 7.3, 2.2, 1.9])
def test_a_factor_that_is_not_a_clean_ratio_does_not_snap(value):
    assert adjustments.snap_ratio(value) is None


def test_bajaj_finance_gives_factor_10_and_hdfc_bank_factor_2():
    # raw / Yahoo is 10 before the event and 1 after: 9331/933.1 vs 938/938.
    assert adjustments.compute_factor(9331.0, 938.0, 933.10, 938.00) == (ADJUSTED, 10.0)
    assert adjustments.compute_factor(1964.1, 973.4, 982.05, 973.40) == (ADJUSTED, 2.0)


def test_a_real_crash_needs_no_adjustment():
    # Both our prices and Yahoo's fell by the same 30%.
    assert adjustments.compute_factor(100.0, 70.0, 100.0, 70.0) == (NO_ADJUSTMENT, 1.0)


def test_a_later_split_does_not_change_the_factor():
    # Yahoo's prices are also adjusted for a later 5:1 split, which moves both
    # of its closes by the same amount and cancels out.
    assert adjustments.compute_factor(9331.0, 938.0, 933.10 / 5, 938.00 / 5) == (
        ADJUSTED,
        10.0,
    )


def test_an_unclean_ratio_is_unverified():
    assert adjustments.compute_factor(1370.0, 1000.0, 100.0, 100.0) == (
        UNVERIFIED,
        None,
    )


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_unusable_prices_are_unverified(bad):
    assert adjustments.compute_factor(100.0, 50.0, bad, 50.0) == (UNVERIFIED, None)


# --- Candidate days ----------------------------------------------------------------


def test_candidates_are_closes_at_least_15_percent_below_the_previous_close():
    frame = make_frame(
        [
            ("A", "2026-03-02", 100.0, 1),
            ("A", "2026-03-03", 79.0, 1),  # -21%: candidate
            ("B", "2026-03-02", 100.0, 1),
            ("B", "2026-03-03", 86.0, 1),  # -14%: not enough
            ("C", "2026-03-02", 100.0, 1),
            ("C", "2026-03-03", 84.9, 1),  # -15.1%: candidate
            ("D", "2026-03-02", 100.0, 1),
            ("D", "2026-03-03", 150.0, 1),  # a rise is not a candidate
        ]
    )

    found = adjustments.find_candidates(frame)

    assert [(c.symbol, c.ex_date) for c in found] == [
        ("A", date(2026, 3, 3)),
        ("C", date(2026, 3, 3)),
    ]
    assert found[0] == Candidate("A", date(2026, 3, 2), date(2026, 3, 3), 100.0, 79.0)
    assert found[0].key == "A|2026-03-03"


def test_each_symbol_is_compared_with_its_own_previous_close():
    frame = make_frame(
        [
            ("A", "2026-03-02", 1000.0, 1),
            ("B", "2026-03-02", 10.0, 1),
            ("A", "2026-03-03", 1000.0, 1),
            ("B", "2026-03-03", 10.0, 1),
        ]
    )

    assert adjustments.find_candidates(frame) == []


def test_a_first_session_is_never_a_candidate():
    assert adjustments.find_candidates(make_frame([("A", "2026-03-02", 1.0, 1)])) == []


def test_yahoo_tickers_carry_the_exchange_suffix():
    assert adjustments.yahoo_symbol("RELIANCE", "NSE") == "RELIANCE.NS"
    assert adjustments.yahoo_symbol("RELIANCE", "BSE") == "RELIANCE.BO"


# --- Checking against Yahoo ----------------------------------------------------------


def candidates_for(rows):
    return adjustments.find_candidates(make_frame(rows))


def test_the_two_recorded_events_are_resolved_to_their_factors():
    fake = FakeYahoo()

    resolved = adjustments.resolve_candidates(candidates_for(RAW_ROWS), "NSE", fake)

    assert resolved["BAJFINANCE|2025-06-16"] == {"status": ADJUSTED, "factor": 10.0}
    assert resolved["HDFCBANK|2025-08-26"] == {"status": ADJUSTED, "factor": 2.0}
    assert sorted(fake.calls) == ["BAJFINANCE.NS", "HDFCBANK.NS"]


def test_yahoo_is_asked_once_per_symbol_however_many_days_it_has():
    rows = [
        ("BAJFINANCE", "2025-06-12", 9400.0, 1),
        ("BAJFINANCE", "2025-06-13", 9331.0, 1),
        ("BAJFINANCE", "2025-06-16", 938.0, 1),
        ("BAJFINANCE", "2025-06-17", 700.0, 1),  # a second fall of 25%
    ]
    # Here Yahoo's price fell the same way: a real crash, not a split.
    crash = {**YAHOO["BAJFINANCE.NS"], date(2025, 6, 17): 700.0}
    fake = FakeYahoo({"BAJFINANCE.NS": crash})

    resolved = adjustments.resolve_candidates(candidates_for(rows), "NSE", fake)

    assert fake.calls == ["BAJFINANCE.NS"]
    assert resolved["BAJFINANCE|2025-06-16"]["status"] == ADJUSTED
    assert resolved["BAJFINANCE|2025-06-17"]["status"] == NO_ADJUSTMENT


def test_a_failed_yahoo_request_marks_the_days_unverified_with_the_reason():
    def broken(ticker, start, end):
        raise ConnectionError("offline")

    resolved = adjustments.resolve_candidates(candidates_for(RAW_ROWS), "NSE", broken)

    assert {entry["status"] for entry in resolved.values()} == {UNVERIFIED}
    assert resolved["BAJFINANCE|2025-06-16"]["reason"] == (
        "Yahoo request failed (ConnectionError)"
    )


def test_a_day_yahoo_has_no_price_for_is_unverified():
    fake = FakeYahoo({"BAJFINANCE.NS": {date(2025, 6, 13): 933.10}})  # no ex-date

    resolved = adjustments.resolve_candidates(candidates_for(RAW_ROWS[:2]), "NSE", fake)

    assert resolved["BAJFINANCE|2025-06-16"]["status"] == UNVERIFIED
    assert "no price" in resolved["BAJFINANCE|2025-06-16"]["reason"]


def test_an_unknown_ticker_is_unverified_not_an_error():
    resolved = adjustments.resolve_candidates(
        candidates_for(RAW_ROWS[:2]), "NSE", FakeYahoo({})
    )

    assert resolved["BAJFINANCE|2025-06-16"]["status"] == UNVERIFIED


# --- Applying adjustments ------------------------------------------------------------


def three_sessions(symbol="X", volume=100.0):
    return make_frame(
        [
            (symbol, "2026-03-02", 1000.0, volume),
            (symbol, "2026-03-03", 1000.0, volume),
            (symbol, "2026-03-04", 250.0, volume),
        ]
    )


def test_prices_before_the_event_are_divided_and_volume_multiplied():
    frame = three_sessions()

    adjusted = adjustments.apply_adjustments(
        frame, [AdjustmentEvent("X", date(2026, 3, 4), 4.0)]
    )

    assert adjusted["close"].tolist() == [250.0, 250.0, 250.0]
    assert adjusted[["open", "high", "low"]].iloc[0].tolist() == [250.0] * 3
    assert adjusted["volume"].tolist() == [400.0, 400.0, 100.0]


def test_the_original_frame_and_other_symbols_are_left_alone():
    frame = pd.concat([three_sessions("X"), three_sessions("Y")], ignore_index=True)
    before = frame.copy()

    adjusted = adjustments.apply_adjustments(
        frame, [AdjustmentEvent("X", date(2026, 3, 4), 4.0)]
    )

    pd.testing.assert_frame_equal(frame, before)
    assert adjusted[adjusted["symbol"] == "Y"]["close"].tolist() == [
        1000.0,
        1000.0,
        250.0,
    ]


def test_two_events_for_one_stock_compound():
    frame = make_frame(
        [
            ("X", "2026-03-02", 1000.0, 1.0),
            ("X", "2026-03-03", 500.0, 1.0),  # first event: factor 2
            ("X", "2026-03-04", 100.0, 1.0),  # second event: factor 5
        ]
    )

    adjusted = adjustments.apply_adjustments(
        frame,
        [
            AdjustmentEvent("X", date(2026, 3, 3), 2.0),
            AdjustmentEvent("X", date(2026, 3, 4), 5.0),
        ],
    )

    assert adjusted["close"].tolist() == [100.0, 100.0, 100.0]


def test_whole_number_volume_survives_a_fractional_factor():
    # Imported files store volume as integers; a 1:5 bonus has factor 1.2 and a
    # 3:2 bonus 2.5, which give volumes with decimals.
    frame = three_sessions(volume=0).assign(
        volume=pd.Series([83145, 99447, 100712], dtype="int64")
    )

    adjusted = adjustments.apply_adjustments(
        frame, [AdjustmentEvent("X", date(2026, 3, 4), 2.5)]
    )

    assert adjusted["volume"].tolist() == [83145 * 2.5, 99447 * 2.5, 100712.0]
    assert adjusted["close"].tolist() == [400.0, 400.0, 250.0]


def test_nullable_integer_volume_with_gaps_is_handled():
    frame = three_sessions().assign(volume=pd.array([100, None, 300], dtype="Int64"))

    adjusted = adjustments.apply_adjustments(
        frame, [AdjustmentEvent("X", date(2026, 3, 4), 1.2)]
    )

    assert adjusted["volume"].iloc[0] == pytest.approx(120.0)
    assert pd.isna(adjusted["volume"].iloc[1])
    assert adjusted["volume"].iloc[2] == 300.0


def test_missing_volume_stays_missing():
    frame = three_sessions(volume=np.nan)

    adjusted = adjustments.apply_adjustments(
        frame, [AdjustmentEvent("X", date(2026, 3, 4), 4.0)]
    )

    assert adjusted["volume"].isna().all()
    assert adjusted["close"].tolist() == [250.0, 250.0, 250.0]


def test_no_events_returns_the_frame_unchanged():
    frame = three_sessions()

    assert adjustments.apply_adjustments(frame, []) is frame


# --- Refreshing a dataset ----------------------------------------------------------


def test_refresh_checks_every_candidate_and_saves_the_answers(dataset):
    fake = FakeYahoo()

    summary = adjustments.refresh_adjustments(dataset, fake)

    assert summary.candidates == 2
    assert summary.already_checked == 0
    assert {(e.symbol, e.factor) for e in summary.adjusted} == {
        ("BAJFINANCE", 10.0),
        ("HDFCBANK", 2.0),
    }
    saved = json.loads(adjustments.sidecar_path(dataset).read_text())
    assert saved["exchange"] == "NSE"
    assert saved["checked"]["BAJFINANCE|2025-06-16"] == {
        "status": ADJUSTED,
        "factor": 10.0,
    }


def test_the_answers_file_sits_beside_the_dataset(dataset):
    assert adjustments.sidecar_path(dataset).name == "nse.parquet.adjustments.json"
    assert adjustments.sidecar_path(dataset).parent == dataset.parent


def test_settled_days_are_not_asked_again(dataset):
    adjustments.refresh_adjustments(dataset, FakeYahoo())
    second = FakeYahoo()

    summary = adjustments.refresh_adjustments(dataset, second)

    assert second.calls == []
    assert summary.already_checked == 2
    assert len(summary.adjusted) == 2


def test_unverified_days_are_retried_and_then_settle(dataset):
    offline = FakeYahoo({})
    first = adjustments.refresh_adjustments(dataset, offline)
    assert len(first.unverified) == 2
    assert first.adjusted == ()

    second = adjustments.refresh_adjustments(dataset, FakeYahoo())

    assert second.unverified == ()
    assert len(second.adjusted) == 2


def test_a_dataset_with_no_large_falls_still_records_that_it_was_checked(tmp_path):
    path = tmp_path / "calm.parquet"
    bhavcopy.write_output(
        make_frame([("A", "2026-03-02", 100.0, 1), ("A", "2026-03-03", 99.0, 1)]),
        "NSE",
        path,
    )

    summary = adjustments.refresh_adjustments(path, FakeYahoo())

    assert summary.candidates == 0
    assert adjustments.load_adjustment_info(path).status == YAHOO_CHECKED


def test_only_imported_parquet_files_can_be_refreshed(tmp_path):
    csv = tmp_path / "mine.csv"
    make_frame(RAW_ROWS).to_csv(csv, index=False)
    foreign = tmp_path / "foreign.parquet"
    make_frame(RAW_ROWS).to_parquet(foreign)

    for path in (csv, foreign):
        with pytest.raises(AdjustmentsError, match="not created by import-bhavcopy"):
            adjustments.refresh_adjustments(path, FakeYahoo())
        assert not adjustments.sidecar_path(path).exists()


def test_a_damaged_answers_file_stops_a_refresh_and_is_left_alone(dataset):
    sidecar = adjustments.sidecar_path(dataset)
    sidecar.write_text("{broken")

    with pytest.raises(AdjustmentsError, match="could not be read"):
        adjustments.refresh_adjustments(dataset, FakeYahoo())

    assert sidecar.read_text() == "{broken"


# --- Reading a dataset's status ----------------------------------------------------


def test_a_csv_has_unknown_adjustment_status(tmp_path):
    csv = tmp_path / "mine.csv"
    make_frame(RAW_ROWS).to_csv(csv, index=False)

    assert adjustments.load_adjustment_info(csv).status == UNKNOWN


def test_an_imported_file_without_answers_is_known_to_be_unadjusted(dataset):
    assert adjustments.load_adjustment_info(dataset).status == RAW_UNCHECKED


def test_the_status_lists_adjusted_events_and_unverified_days(dataset):
    adjustments.write_sidecar(
        dataset,
        "NSE",
        {
            "BAJFINANCE|2025-06-16": {"status": ADJUSTED, "factor": 10.0},
            "HDFCBANK|2025-08-26": {"status": UNVERIFIED, "factor": None},
            "XYZ|2025-01-02": {"status": NO_ADJUSTMENT, "factor": 1.0},
        },
    )

    info = adjustments.load_adjustment_info(dataset)

    assert info.status == YAHOO_CHECKED
    assert info.events == (AdjustmentEvent("BAJFINANCE", date(2025, 6, 16), 10.0),)
    assert info.unverified == (("HDFCBANK", date(2025, 8, 26)),)


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "{}",
        json.dumps({"checked": {"A|2026-01-01": {"status": "weird", "factor": 2}}}),
        json.dumps({"checked": {"A|not-a-date": {"status": ADJUSTED, "factor": 2}}}),
        json.dumps({"checked": {"A|2026-01-01": {"status": ADJUSTED, "factor": 0.5}}}),
    ],
)
def test_a_damaged_answers_file_is_reported_as_a_problem_not_trusted(dataset, content):
    adjustments.sidecar_path(dataset).write_text(content)

    info = adjustments.load_adjustment_info(dataset)

    assert info.status == UNKNOWN
    assert info.events == ()
    assert "could not be read" in info.problem


# --- Warnings -----------------------------------------------------------------------


def info_with(unverified=()):
    return adjustments.AdjustmentInfo(YAHOO_CHECKED, unverified=tuple(unverified))


def codes(warnings):
    return [warning["code"] for warning in warnings]


def test_unchecked_exchange_prices_warn_that_they_are_unadjusted():
    warnings = adjustments.adjustment_warnings(
        adjustments.AdjustmentInfo(RAW_UNCHECKED)
    )

    assert codes(warnings) == ["unadjusted_prices"]
    assert "refresh-adjustments" in warnings[0]["message"]


def test_checked_prices_with_nothing_unverified_have_no_warning():
    assert adjustments.adjustment_warnings(info_with()) == []


def test_unverified_falls_warn_only_for_the_symbols_asked_about():
    info = info_with([("AAA", date(2026, 3, 3)), ("BBB", date(2026, 3, 4))])

    only_aaa = adjustments.adjustment_warnings(info, ["AAA"])
    other = adjustments.adjustment_warnings(info, ["CCC"])

    assert codes(only_aaa) == ["price_adjustment_unknown"]
    assert only_aaa[0]["symbols"] == ["AAA"]
    assert "AAA 2026-03-03" in only_aaa[0]["message"]
    assert "BBB" not in only_aaa[0]["message"]
    assert other == []
    assert len(adjustments.adjustment_warnings(info)[0]["symbols"]) == 2


def test_an_unknown_status_is_silent_unless_diagnostics_ask_for_it():
    unknown = adjustments.AdjustmentInfo(UNKNOWN)

    assert adjustments.adjustment_warnings(unknown) == []
    assert codes(adjustments.adjustment_warnings(unknown, explain_unknown=True)) == [
        "price_adjustment_unknown"
    ]


def test_a_damaged_file_warns_in_every_context():
    broken = adjustments.AdjustmentInfo(UNKNOWN, problem="x.json could not be read")

    assert codes(adjustments.adjustment_warnings(broken)) == [
        "price_adjustment_unknown"
    ]


# --- Through the tools ---------------------------------------------------------------


def call_envelope(name, arguments=None):
    result = asyncio.run(server.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


@pytest.fixture
def split_dataset(tmp_path, monkeypatch):
    """One stock that fell from 1000 to 250 on a 4:1 split, with the event saved."""
    path = tmp_path / "split.parquet"
    days = pd.bdate_range("2026-03-02", periods=8)
    closes = [1000.0] * 5 + [250.0] * 3
    rows = [
        ("SPLIT", day.strftime("%Y-%m-%d"), close, 1000.0)
        for day, close in zip(days, closes, strict=True)
    ]
    # A calm stock alongside, so rankings have something to compare.
    rows += [("CALM", day.strftime("%Y-%m-%d"), 100.0, 1000.0) for day in days]
    bhavcopy.write_output(make_frame(rows), "NSE", path)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    return path


def save_split(path, status=ADJUSTED, factor=4.0):
    adjustments.write_sidecar(
        path, "NSE", {"SPLIT|2026-03-09": {"status": status, "factor": factor}}
    )


def test_the_weekly_return_across_a_split_is_zero_once_adjusted(split_dataset):
    raw = call_envelope("get_stock_weekly_return", {"symbol": "SPLIT"})
    assert raw["data"]["return_percent"] == pytest.approx(-75.0)
    assert [w["code"] for w in raw["warnings"]] == ["unadjusted_prices"]

    save_split(split_dataset)
    adjusted = call_envelope("get_stock_weekly_return", {"symbol": "SPLIT"})

    assert adjusted["data"]["return_percent"] == pytest.approx(0.0)
    assert adjusted["warnings"] == []


def test_rankings_use_adjusted_prices(split_dataset):
    watchlists.create_watchlist("mine", ["SPLIT", "CALM"])

    def returns():
        ranked = data.rank_weekly_performers(top_n=10, universe="mine")["rankings"]
        return {row["symbol"]: round(row["return_percent"], 6) for row in ranked}

    assert returns() == {"CALM": 0.0, "SPLIT": -75.0}  # the split looks like a crash

    save_split(split_dataset)

    assert returns() == {"CALM": 0.0, "SPLIT": 0.0}


def test_indicators_are_calculated_on_adjusted_prices(split_dataset):
    save_split(split_dataset)

    result = call_envelope(
        "get_stock_indicators",
        {"symbol": "SPLIT", "ema_periods": [3], "rsi_period": 2, "atr_period": 2},
    )["data"]

    # A flat adjusted history: no move, so RSI is neutral and the 3-day EMA is the close.
    assert result["close"] == 250.0
    assert result["ema"][0]["value"] == pytest.approx(250.0)
    assert result["rsi"]["value"] == 50.0


def test_unverified_falls_warn_on_the_tools_that_use_the_stock(split_dataset):
    save_split(split_dataset, status=UNVERIFIED, factor=None)

    weekly = call_envelope("get_stock_weekly_return", {"symbol": "SPLIT"})
    calm = call_envelope("get_stock_weekly_return", {"symbol": "CALM"})

    assert [w["code"] for w in weekly["warnings"]] == ["price_adjustment_unknown"]
    assert weekly["warnings"][0]["symbols"] == ["SPLIT"]
    assert calm["warnings"] == []


def test_price_history_shows_prices_as_published(split_dataset):
    save_split(split_dataset)

    prices = call_envelope("get_price_history", {"symbol": "SPLIT", "sessions": 8})[
        "data"
    ]["prices"]

    assert [row["close"] for row in prices] == [1000.0] * 5 + [250.0] * 3


def test_capabilities_report_the_adjustment_status(split_dataset):
    assert call_envelope("get_data_capabilities")["data"]["price_adjustment"] == {
        "status": RAW_UNCHECKED,
        "adjusted_events": 0,
        "unverified_events": 0,
    }

    save_split(split_dataset)
    report = call_envelope("get_data_capabilities")

    assert report["data"]["price_adjustment"] == {
        "status": YAHOO_CHECKED,
        "adjusted_events": 1,
        "unverified_events": 0,
    }
    assert "price_adjustment_unknown" not in [w["code"] for w in report["warnings"]]


def test_a_damaged_answers_file_does_not_break_the_tools(split_dataset):
    adjustments.sidecar_path(split_dataset).write_text("{broken")

    envelope = call_envelope("get_stock_weekly_return", {"symbol": "SPLIT"})

    assert envelope["data"]["return_percent"] == pytest.approx(-75.0)  # unadjusted
    assert [w["code"] for w in envelope["warnings"]] == ["price_adjustment_unknown"]


def test_unknown_symbols_are_still_errors(split_dataset):
    with pytest.raises(ToolError, match="not found"):
        call_envelope("get_stock_weekly_return", {"symbol": "NOPE"})


# --- The commands ---------------------------------------------------------------------


def test_refresh_adjustments_prints_what_it_found(dataset, monkeypatch, capsys):
    monkeypatch.setattr(adjustments, "fetch_yahoo_closes", FakeYahoo())

    cli.main(["refresh-adjustments", str(dataset)])

    printed = capsys.readouterr().out
    assert "Large falls (15%+) found:  2" in printed
    assert "Adjusted:                  2" in printed
    assert "BAJFINANCE  2025-06-16  factor 10" in printed
    assert "HDFCBANK  2025-08-26  factor 2" in printed
    assert "Could not verify:          0" in printed


def test_refresh_adjustments_reports_unverified_days_and_the_retry(
    dataset, monkeypatch, capsys
):
    # The autouse fixture makes every Yahoo request fail.
    cli.main(["refresh-adjustments", str(dataset)])

    printed = capsys.readouterr().out
    assert "Could not verify:          2 (retried on the next refresh)" in printed
    assert "Yahoo request failed (RuntimeError)" in printed


def test_refresh_adjustments_exits_with_a_message_for_a_foreign_file(tmp_path):
    csv = tmp_path / "mine.csv"
    make_frame(RAW_ROWS).to_csv(csv, index=False)

    with pytest.raises(SystemExit) as error:
        cli.main(["refresh-adjustments", str(csv)])

    assert "not created by import-bhavcopy" in str(error.value)
