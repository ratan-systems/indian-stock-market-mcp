"""The ranking, screen, comparison, breadth and export tools, through MCP."""

import asyncio
import json
from datetime import UTC, datetime

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import data, server
from indian_stock_market_mcp.watchlists import create_watchlist

DAYS = pd.bdate_range("2026-01-01", periods=80)
LAST = DAYS[-1].strftime("%Y-%m-%d")


def call_envelope(name, arguments=None):
    result = asyncio.run(server.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def codes(envelope):
    return [warning["code"] for warning in envelope["warnings"]]


def rows_for(symbol, closes, volumes=None, highs=None, days=None):
    days = DAYS[-len(closes) :] if days is None else days
    volumes = [1_000_000.0] * len(closes) if volumes is None else volumes
    highs = closes if highs is None else highs
    return [
        {
            "date": day,
            "symbol": symbol,
            "open": close,
            "high": high,
            "low": close,
            "close": close,
            "volume": volume,
        }
        for day, close, high, volume in zip(days, closes, highs, volumes, strict=True)
    ]


def rising_with_dips(length=80):
    closes, price = [], 100.0
    for i in range(length):
        price *= 0.99 if i % 3 == 2 else 1.01
        closes.append(price)
    return closes


def use_dataset(rows, tmp_path, monkeypatch):
    path = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)
    return path


@pytest.fixture
def market(tmp_path, monkeypatch):
    """Stocks shaped to pass or fail each momentum rule."""
    rows = (
        rows_for("MOMO", rising_with_dips())
        + rows_for("HOT", [100 * 1.01**i for i in range(80)])
        + rows_for("DOWN", [200 * 0.99**i for i in range(80)])
        + rows_for("THIN", rising_with_dips(), volumes=[10.0] * 80)
        + rows_for("NEW", rising_with_dips(10))
        + rows_for("OLD", rising_with_dips(70), days=DAYS[:70])
    )
    use_dataset(rows, tmp_path, monkeypatch)
    create_watchlist("mine", ["MOMO", "HOT", "DOWN", "THIN", "NEW", "OLD", "GONE"])


@pytest.fixture
def breakouts(tmp_path, monkeypatch):
    """Flat stocks at 100 (highs 101) whose last session differs."""
    flat, highs, volume = [100.0] * 79, [101.0] * 79, [1_000_000.0] * 79
    rows = (
        rows_for("BOTH", [*flat, 105.0], [*volume, 3_000_000.0], [*highs, 106.0])
        + rows_for("LOUD", [*flat, 100.0], [*volume, 2_500_000.0], [*highs, 101.0])
        + rows_for("QUIET", [*flat, 104.0], [*volume, 1_000_000.0], [*highs, 104.0])
        + rows_for("CALM", [*flat, 100.0], [*volume, 1_000_000.0], [*highs, 101.0])
    )
    use_dataset(rows, tmp_path, monkeypatch)
    create_watchlist("mine", ["BOTH", "LOUD", "QUIET", "CALM"])


# --- rank_performers -----------------------------------------------------------------


def test_the_ranking_returns_the_best_and_the_worst_over_a_shortcut(market):
    response = call_envelope("rank_performers", {"universe": "mine", "top_n": 2})
    result = response["data"]

    hot = (1.01**21 - 1) * 100
    down = (0.99**21 - 1) * 100
    assert result["period"] == "1m"
    assert result["sessions"] == 21
    assert result["start_date"] == DAYS[-22].strftime("%Y-%m-%d")
    assert result["end_date"] == LAST
    assert [r["symbol"] for r in result["best"]] == ["HOT", "MOMO"]
    assert result["best"][0]["return_percent"] == pytest.approx(hot, abs=0.01)
    assert [r["symbol"] for r in result["worst"]] == ["DOWN", "THIN"]
    assert result["worst"][0]["return_percent"] == pytest.approx(down, abs=0.01)
    assert result["checked"] == 4
    assert [(s["symbol"], s["code"]) for s in result["skipped"]] == [
        ("NEW", "short_history"),
        ("OLD", "stale_data"),
        ("GONE", "symbol_not_found"),
    ]
    assert response["as_of"] == LAST
    assert response["parameters"] == {
        "universe": "MINE",
        "symbols": None,
        "period": "1m",
        "start_date": DAYS[-22].strftime("%Y-%m-%d"),
        "end_date": LAST,
        "top_n": 2,
        "output_path": None,
        "overwrite": False,
    }
    assert sorted(codes(response)) == [
        "short_history",
        "stale_data",
        "symbol_not_found",
    ]


def test_a_symbol_list_is_ranked_instead_of_a_universe(market):
    response = call_envelope(
        "rank_performers", {"symbols": ["down", "MOMO", "momo"], "period": "1w"}
    )

    assert response["data"]["universe"] == "CUSTOM"
    assert [r["symbol"] for r in response["data"]["best"]] == ["MOMO", "DOWN"]
    assert response["parameters"]["symbols"] == ["DOWN", "MOMO"]
    assert response["parameters"]["universe"] is None


def test_dates_that_are_not_trading_days_are_moved_and_reported(market):
    response = call_envelope(
        "rank_performers",
        {"universe": "mine", "start_date": "2026-03-07", "end_date": "2026-03-22"},
    )

    assert response["data"]["start_date"] == "2026-03-09"  # Saturday to Monday
    assert response["data"]["end_date"] == "2026-03-20"  # Sunday to Friday
    assert response["data"]["period"] == "custom"
    assert codes(response).count("date_adjusted") == 2
    hot = response["data"]["best"][0]
    assert hot["symbol"] == "HOT"
    assert hot["return_percent"] == pytest.approx((1.01**9 - 1) * 100, abs=0.01)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"period": "2m"}, "Unknown period"),
        ({"period": "1y"}, "needs 253 trading days"),
        ({"top_n": 0}, "top_n must be an integer between 1 and 100"),
        ({"symbols": []}, "1 to 500 symbols"),
        ({"universe": "nope"}, "Unknown universe or watchlist"),
        ({"period": "1m", "start_date": "2026-02-02"}, "not both"),
    ],
)
def test_the_ranking_refuses_bad_arguments(market, arguments, message):
    with pytest.raises(ToolError, match=message):
        call_envelope("rank_performers", {"universe": "mine", **arguments})


def test_the_weekly_ranking_still_answers_as_before(market):
    response = call_envelope("rank_weekly_performers", {"universe": "mine"})

    assert [r["symbol"] for r in response["data"]["rankings"]][:2] == ["HOT", "MOMO"]
    assert response["data"]["rankings"][0]["session_count"] == 5


# --- screen_momentum -----------------------------------------------------------------


def test_the_momentum_screen_explains_every_stock_left_out(market):
    response = call_envelope("screen_momentum", {"universe": "mine"})
    result = response["data"]

    assert [row["symbol"] for row in result["matches"]] == ["MOMO"]
    assert 50 <= result["matches"][0]["rsi"] <= 70
    assert {e["reason"]: e["symbols"] for e in result["excluded"]} == {
        "illiquid": ["THIN"],
        "below_ema": ["DOWN"],
        "rsi_too_low": ["DOWN"],
        "rsi_too_high": ["HOT"],
        "weak_return": ["DOWN"],
        "too_little_history": ["NEW"],
    }
    assert result["excluded_count"] == 4
    assert result["checked"] == 5
    assert result["rules_in_words"] == [
        "Average traded value over 20 sessions is at least Rs 1 crore a day",
        "Close is above the 50-day EMA",
        "RSI(14) is between 50 and 70",
        "Return over 63 sessions is above 0%",
    ]
    assert response["parameters"]["rsi_max"] == 70.0
    assert sorted(codes(response)) == ["stale_data", "symbol_not_found"]


def test_a_threshold_given_once_changes_the_answer_but_not_the_settings(market):
    wide = call_envelope(
        "screen_momentum",
        {"universe": "mine", "rsi_max": 100, "min_traded_value_crore": 0},
    )
    after = call_envelope("screen_momentum", {"universe": "mine"})

    assert [row["symbol"] for row in wide["data"]["matches"]] == ["HOT", "MOMO", "THIN"]
    assert wide["parameters"]["min_traded_value_crore"] == 0
    assert "min_traded_value_crore" not in wide["data"]["rules"]
    assert [row["symbol"] for row in after["data"]["matches"]] == ["MOMO"]


def test_saved_settings_become_the_screens_defaults(market):
    call_envelope("update_indicator_settings", {"changes": {"momentum_rsi_max": 100.0}})

    response = call_envelope("screen_momentum", {"universe": "mine"})

    assert [row["symbol"] for row in response["data"]["matches"]] == ["HOT", "MOMO"]
    assert response["parameters"]["rsi_max"] == 100.0


def test_an_rsi_band_upside_down_is_refused(market):
    with pytest.raises(ToolError, match="must not be above"):
        call_envelope("screen_momentum", {"universe": "mine", "rsi_min": 80})


def test_max_results_shortens_the_list_and_says_so(market):
    response = call_envelope(
        "screen_momentum",
        {
            "universe": "mine",
            "rsi_max": 100,
            "min_traded_value_crore": 0,
            "max_results": 1,
        },
    )

    assert [row["symbol"] for row in response["data"]["matches"]] == ["HOT"]
    assert response["data"]["total_matches"] == 3
    assert response["data"]["truncated"] is True


# --- find_volume_breakouts -------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("both", ["BOTH"]),
        ("spike", ["BOTH", "LOUD"]),  # 3.0x before 2.5x
        ("breakout", ["BOTH", "QUIET"]),  # 3.96% above the level before 2.97%
    ],
)
def test_each_mode_finds_the_right_stocks(breakouts, mode, expected):
    response = call_envelope(
        "find_volume_breakouts", {"universe": "mine", "mode": mode}
    )

    assert [row["symbol"] for row in response["data"]["matches"]] == expected
    assert response["data"]["mode"] == mode


def test_a_match_carries_the_numbers_needed_to_check_it(breakouts):
    response = call_envelope("find_volume_breakouts", {"universe": "mine"})

    match = response["data"]["matches"][0]
    assert match["symbol"] == "BOTH"
    assert match["close"] == 105.0
    assert match["volume"] == 3_000_000
    assert match["volume_ratio"] == 3.0
    assert match["breakout_level"] == 101.0
    assert match["breakout_level_date"] == DAYS[-2].strftime("%Y-%m-%d")
    assert match["close_vs_level_percent"] == pytest.approx(3.96)
    assert {e["reason"]: e["symbols"] for e in response["data"]["excluded"]} == {
        "low_volume": ["QUIET", "CALM"],
        "no_breakout": ["LOUD", "CALM"],
    }


def test_the_volume_multiple_and_the_lookback_can_be_changed(breakouts):
    higher = call_envelope(
        "find_volume_breakouts",
        {"universe": "mine", "mode": "spike", "volume_multiple": 2.8},
    )

    assert [row["symbol"] for row in higher["data"]["matches"]] == ["BOTH"]
    assert higher["parameters"]["volume_multiple"] == 2.8
    assert higher["parameters"]["breakout_sessions"] == 20


def test_a_resistance_breakout_is_measured_from_a_swing_high(tmp_path, monkeypatch):
    closes = [100.0] * 80
    for offset in range(-4, 5):  # a swing high of 110, 30 sessions ago
        closes[49 + offset] = 110.0 - 2.0 * abs(offset)
    use_dataset(
        rows_for("OVER", [*closes[:-1], 111.0])
        + rows_for("UNDER", [*closes[:-1], 108.0]),
        tmp_path,
        monkeypatch,
    )

    response = call_envelope(
        "find_volume_breakouts",
        {
            "symbols": ["OVER", "UNDER"],
            "mode": "breakout",
            "breakout_method": "resistance",
        },
    )

    match = response["data"]["matches"][0]
    assert [row["symbol"] for row in response["data"]["matches"]] == ["OVER"]
    assert match["breakout_level"] == 110.0
    assert match["breakout_level_date"] == DAYS[49].strftime("%Y-%m-%d")
    assert "nearest resistance" in response["data"]["rules_in_words"][-1]
    # The 20-day high would not have caught it: it is only 100.
    plain = call_envelope(
        "find_volume_breakouts", {"symbols": ["OVER", "UNDER"], "mode": "breakout"}
    )
    assert [row["symbol"] for row in plain["data"]["matches"]] == ["OVER", "UNDER"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"mode": "loud"}, "mode must be one of"),
        ({"breakout_method": "magic"}, "breakout_method must be one of"),
        ({"volume_multiple": 0.5}, "between 1 and 100"),
        ({"max_results": 501}, "between 1 and 500"),
    ],
)
def test_the_breakout_tool_refuses_bad_arguments(breakouts, arguments, message):
    with pytest.raises(ToolError, match=message):
        call_envelope("find_volume_breakouts", {"universe": "mine", **arguments})


# --- Presets -------------------------------------------------------------------------


def test_the_presets_are_listed_with_their_exact_rules(market):
    response = call_envelope("list_screen_presets")

    listed = {p["id"]: p for p in response["data"]["presets"]}
    assert list(listed) == [
        "strong_momentum",
        "near_52_week_high",
        "oversold",
        "volume_breakout",
    ]
    assert listed["oversold"]["name"] == "Oversold"
    assert listed["oversold"]["rules"] == {
        "min_traded_value_crore": 1.0,
        "rsi_max": 30.0,
    }
    assert listed["oversold"]["rules_in_words"][-1] == "RSI(14) is at most 30"
    assert "rsi_max" in response["data"]["available_rules"]
    assert response["source"]["kind"] == "local_file"


def test_the_momentum_preset_matches_the_momentum_screen(market):
    preset = call_envelope(
        "run_screen_preset", {"preset": "Strong momentum, liquid", "universe": "mine"}
    )
    screen = call_envelope("screen_momentum", {"universe": "mine"})

    assert preset["data"]["preset"] == "strong_momentum"
    assert preset["data"]["matches"] == screen["data"]["matches"]
    assert preset["data"]["excluded"] == screen["data"]["excluded"]


def test_the_oversold_preset_finds_the_falling_stock_and_a_rule_can_be_overridden(
    market,
):
    found = call_envelope(
        "run_screen_preset", {"preset": "oversold", "universe": "mine"}
    )
    stricter = call_envelope(
        "run_screen_preset",
        {"preset": "oversold", "universe": "mine", "rules": {"rsi_max": 0}},
    )

    assert [row["symbol"] for row in found["data"]["matches"]] == ["DOWN"]
    assert found["data"]["matches"][0]["rsi"] == 0.0
    assert [row["symbol"] for row in stricter["data"]["matches"]] == ["DOWN"]
    assert stricter["data"]["rules"]["rsi_max"] == 0.0
    assert stricter["parameters"]["rules"] == {"rsi_max": 0}


def test_a_preset_rule_can_be_switched_off_or_added(market):
    response = call_envelope(
        "run_screen_preset",
        {
            "preset": "near_52_week_high",
            "universe": "mine",
            "rules": {"min_traded_value_crore": None, "rsi_max": 80},
        },
    )

    # Without the liquidity rule THIN is back; HOT is out on the added RSI rule,
    # and NEW's ten sessions are too few for an RSI.
    assert [row["symbol"] for row in response["data"]["matches"]] == ["MOMO", "THIN"]
    assert response["data"]["rules"] == {"within_percent_of_high": 5.0, "rsi_max": 80.0}
    assert {e["reason"]: e["symbols"] for e in response["data"]["excluded"]} == {
        "rsi_too_high": ["HOT"],
        "far_from_high": ["DOWN"],
        "too_little_history": ["NEW"],
    }


def test_the_volume_breakout_preset_runs(breakouts):
    response = call_envelope(
        "run_screen_preset", {"preset": "volume_breakout", "universe": "mine"}
    )

    assert [row["symbol"] for row in response["data"]["matches"]] == ["BOTH"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"preset": "value"}, "Unknown preset"),
        ({"preset": "oversold", "rules": {"pe_max": 10}}, "Unknown rule"),
        ({"preset": "oversold", "rules": {"rsi_max": 300}}, "between 0 and 100"),
        (
            {
                "preset": "oversold",
                "rules": {"rsi_max": None, "min_traded_value_crore": None},
            },
            "needs at least one",
        ),
    ],
)
def test_a_preset_run_refuses_bad_arguments(market, arguments, message):
    with pytest.raises(ToolError, match=message):
        call_envelope("run_screen_preset", {"universe": "mine", **arguments})


# --- compare_stocks --------------------------------------------------------------------


def test_stocks_are_compared_on_the_dates_they_share(market):
    response = call_envelope(
        "compare_stocks",
        {"symbols": ["MOMO", "DOWN"], "benchmarks": ["mine"], "periods": {"1m": 21}},
    )
    result = response["data"]

    assert result["symbols"] == ["MOMO", "DOWN"]
    assert (result["start_date"], result["end_date"]) == (
        DAYS[0].strftime("%Y-%m-%d"),
        LAST,
    )
    momo, down = result["stocks"]
    closes = rising_with_dips()
    assert momo["close"] == pytest.approx(closes[-1], abs=0.001)
    assert momo["returns"][0]["return_percent"] == pytest.approx(
        (closes[-1] / closes[-22] - 1) * 100, abs=0.01
    )
    assert down["returns"][0]["return_percent"] == pytest.approx(
        (0.99**21 - 1) * 100, abs=0.01
    )
    assert momo["returns"][0]["benchmarks"][0]["id"] == "MINE"
    assert momo["rsi"] > 50 > down["rsi"]
    assert down["drawdown"]["max_percent"] == pytest.approx(
        (1 - 0.99**79) * 100, abs=0.01
    )
    assert momo["range_52w"]["is_new_high"] is True
    assert down["range_52w"]["is_new_low"] is True
    assert [b["kind"] for b in result["benchmarks"]] == ["approximate"]
    assert "different_histories" not in codes(response)
    assert "benchmark_approximate" in codes(response)


def test_a_shorter_history_limits_every_stock_and_is_warned_about(market):
    response = call_envelope(
        "compare_stocks",
        {"symbols": ["MOMO", "NEW"], "benchmarks": ["mine"], "periods": {"1w": 5}},
    )
    result = response["data"]

    assert result["start_date"] == DAYS[-10].strftime("%Y-%m-%d")
    assert [stock["sessions"] for stock in result["stocks"]] == [10, 10]
    warning = next(
        w for w in response["warnings"] if w["code"] == "different_histories"
    )
    assert warning["symbols"] == ["MOMO"]
    assert "10 sessions for NEW" in warning["message"]
    # MOMO's worst fall is measured over those ten sessions only.
    momo = result["stocks"][0]
    assert momo["drawdown"]["max_percent"] == pytest.approx(1.0, abs=0.01)


def test_a_symbol_not_in_the_data_is_left_out_with_a_warning(market):
    response = call_envelope(
        "compare_stocks",
        {"symbols": ["MOMO", "GONE", "HOT"], "benchmarks": ["mine"]},
    )

    assert response["data"]["symbols"] == ["MOMO", "HOT"]
    assert "symbol_not_found" in codes(response)


def test_a_comparison_without_a_usable_benchmark_still_has_its_other_numbers(market):
    response = call_envelope(
        "compare_stocks", {"symbols": ["MOMO", "HOT"], "periods": {"1m": 21}}
    )

    momo = response["data"]["stocks"][0]
    assert momo["returns"][0]["benchmarks"] == []
    assert momo["returns"][0]["return_percent"] is not None
    assert "benchmark_unavailable" in codes(response)


@pytest.mark.parametrize(
    ("symbols", "message"),
    [
        (["MOMO"], "2 to 10 different symbols"),
        (["MOMO", "momo"], "2 to 10 different symbols"),
        ([f"S{i}" for i in range(11)], "2 to 10 different symbols"),
        (["MOMO", "GONE"], "at least 2 symbols that are in the dataset"),
        (["MOMO", "OLD", "NEW"], "no trading days in common"),
    ],
)
def test_a_comparison_refuses_what_it_cannot_compare(market, symbols, message):
    with pytest.raises(ToolError, match=message):
        call_envelope("compare_stocks", {"symbols": symbols, "benchmarks": ["mine"]})


# --- get_market_breadth ------------------------------------------------------------------


def test_breadth_reports_moves_ema_shares_and_year_extremes(market):
    response = call_envelope("get_market_breadth", {"universe": "mine"})
    result = response["data"]

    # Last session: MOMO, HOT, THIN and NEW rose; DOWN fell.
    assert result["advance_decline"] == {
        "advancers": 4,
        "decliners": 1,
        "unchanged": 0,
        "advancers_percent": 80.0,
        "decliners_percent": 20.0,
        "ratio": 4.0,
    }
    assert result["above_ema"] == [
        {"period": 50, "above": 3, "measured": 4, "percent": 75.0},
        {"period": 200, "above": 0, "measured": 0, "percent": None},
    ]
    # NEW rose on the day but its high was two sessions earlier.
    assert result["year_extremes"]["new_high_symbols"] == ["MOMO", "HOT", "THIN"]
    assert result["year_extremes"]["new_low_symbols"] == ["DOWN"]
    assert result["checked"] == 5
    assert response["parameters"]["ema_periods"] == [50, 200]
    assert sorted(set(codes(response))) == [
        "short_history",
        "stale_data",
        "symbol_not_found",
    ]


def test_breadth_takes_other_ema_periods_and_a_symbol_list(market):
    response = call_envelope(
        "get_market_breadth", {"symbols": ["MOMO", "DOWN"], "ema_periods": [10]}
    )

    assert response["data"]["above_ema"] == [
        {"period": 10, "above": 1, "measured": 2, "percent": 50.0}
    ]
    assert response["data"]["universe"] == "CUSTOM"


# --- CSV export ----------------------------------------------------------------------


def read_export(path):
    lines = path.read_text().splitlines()
    comments = [line for line in lines if line.startswith("#")]
    table = pd.read_csv(path, comment="#")
    return comments, table


def test_a_ranking_can_be_saved_as_a_csv_with_its_provenance(market, tmp_path):
    target = tmp_path / "ranking.csv"

    response = call_envelope(
        "rank_performers",
        {"universe": "mine", "top_n": 1, "output_path": str(target)},
    )

    comments, table = read_export(target)
    assert response["data"]["export"] == {
        "file_name": "ranking.csv",
        "rows": 4,
        "columns": [
            "rank",
            "symbol",
            "start_date",
            "end_date",
            "start_close",
            "end_close",
            "return_percent",
        ],
    }
    assert comments[0] == "# tool: rank_performers"
    assert comments[1] == "# source: prices.csv (csv)"
    assert comments[2] == f"# as_of: {LAST}"
    recorded = json.loads(comments[3].removeprefix("# parameters: "))
    assert recorded == {
        "universe": "MINE",
        "symbols": None,
        "period": "1m",
        "start_date": DAYS[-22].strftime("%Y-%m-%d"),
        "end_date": LAST,
        "top_n": 1,
    }
    # The file does not carry the folder it was saved in.
    assert str(tmp_path) not in target.read_text()
    # Every ranked stock is saved, not only the top_n shown.
    assert list(table["symbol"]) == ["HOT", "MOMO", "THIN", "DOWN"]
    assert list(table["rank"]) == [1, 2, 3, 4]


def test_an_existing_file_is_kept_unless_overwrite_is_asked_for(market, tmp_path):
    target = tmp_path / "ranking.csv"
    target.write_text("mine\n")

    with pytest.raises(ToolError, match="already exists"):
        call_envelope(
            "rank_performers", {"universe": "mine", "output_path": str(target)}
        )
    assert target.read_text() == "mine\n"

    call_envelope(
        "rank_performers",
        {"universe": "mine", "output_path": str(target), "overwrite": True},
    )
    assert target.read_text().startswith("# tool: rank_performers")


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("ranking.txt", "must end in .csv"),
        ("missing_folder/ranking.csv", "does not exist"),
        ("", "must be a file path"),
    ],
)
def test_a_bad_export_path_is_refused_before_any_work(market, tmp_path, name, message):
    target = str(tmp_path / name) if name else ""

    with pytest.raises(ToolError, match=message):
        call_envelope("rank_performers", {"universe": "mine", "output_path": target})


def test_every_table_tool_can_export(market, tmp_path):
    calls = {
        "screen_momentum": {"universe": "mine"},
        "find_volume_breakouts": {"universe": "mine", "mode": "breakout"},
        "run_screen_preset": {"preset": "oversold", "universe": "mine"},
        "compare_stocks": {"symbols": ["MOMO", "DOWN"], "benchmarks": ["mine"]},
        "get_market_breadth": {"universe": "mine"},
    }
    for name, arguments in calls.items():
        target = tmp_path / f"{name}.csv"

        response = call_envelope(name, {**arguments, "output_path": str(target)})

        comments, table = read_export(target)
        assert comments[0] == f"# tool: {name}"
        assert response["data"]["export"]["rows"] == len(table) > 0
        assert response["data"]["export"]["file_name"] == target.name


def test_the_comparison_csv_has_one_flat_row_per_stock(market, tmp_path):
    target = tmp_path / "compare.csv"

    call_envelope(
        "compare_stocks",
        {
            "symbols": ["MOMO", "DOWN"],
            "benchmarks": ["mine"],
            "periods": {"1m": 21},
            "output_path": str(target),
        },
    )

    _, table = read_export(target)
    assert list(table["symbol"]) == ["MOMO", "DOWN"]
    for column in (
        "return_1m_percent",
        "excess_1m_vs_MINE_percent",
        "close_vs_ema_20_percent",
        "rsi",
        "percent_below_high",
        "volatility_annualized_percent",
        "max_drawdown_percent",
    ):
        assert column in table.columns


def test_the_breadth_csv_lists_each_measure(market, tmp_path):
    target = tmp_path / "breadth.csv"

    call_envelope(
        "get_market_breadth", {"universe": "mine", "output_path": str(target)}
    )

    _, table = read_export(target)
    values = dict(zip(table["measure"], table["value"], strict=True))
    assert values["advancers"] == 4
    assert values["percent_above_ema_50"] == 75.0
    assert values["new_52_week_lows"] == 1
    lows = table.loc[table["measure"] == "new_52_week_lows", "symbols"].iloc[0]
    assert lows == "DOWN"


def test_no_file_is_written_unless_asked(market, tmp_path):
    response = call_envelope("rank_performers", {"universe": "mine"})

    assert response["data"]["export"] is None
    assert list(tmp_path.glob("*.csv")) == [tmp_path / "prices.csv"]


def test_a_screens_csv_holds_every_match_not_only_the_rows_shown(market, tmp_path):
    target = tmp_path / "screen.csv"

    response = call_envelope(
        "screen_momentum",
        {
            "universe": "mine",
            "rsi_max": 100,
            "min_traded_value_crore": 0,
            "max_results": 1,
            "output_path": str(target),
        },
    )

    _, table = read_export(target)
    assert [row["symbol"] for row in response["data"]["matches"]] == ["HOT"]
    assert list(table["symbol"]) == ["HOT", "MOMO", "THIN"]
    assert response["data"]["export"]["rows"] == 3


def test_the_relative_strength_ranking_can_be_saved(market, tmp_path):
    target = tmp_path / "strength.csv"

    response = call_envelope(
        "rank_relative_strength",
        {
            "universe": "mine",
            "benchmarks": ["mine"],
            "period": "1m",
            "top_n": 1,
            "output_path": str(target),
        },
    )

    comments, table = read_export(target)
    assert comments[0] == "# tool: rank_relative_strength"
    assert list(table.columns) == [
        "rank",
        "symbol",
        "stock_return_percent",
        "excess_vs_MINE_percent",
    ]
    assert list(table["symbol"]) == ["HOT", "MOMO", "THIN", "DOWN"]
    assert len(response["data"]["rankings"]) == 1
    assert response["data"]["export"]["rows"] == 4


def test_the_source_says_when_the_dataset_file_last_changed(market):
    source = call_envelope("rank_performers", {"universe": "mine"})["source"]
    today = datetime.now(UTC).astimezone().date()

    assert source["file_name"] == "prices.csv"
    assert source["file_modified"] == today.isoformat()
