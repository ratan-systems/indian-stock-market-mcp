"""Rankings over a period, screen rules, presets and breadth (no files)."""

import numpy as np
import pandas as pd
import pytest

from indian_stock_market_mcp import screens
from indian_stock_market_mcp.indicators import (
    InsufficientHistoryError,
    highest_high,
    nearest_resistance,
    relative_volume,
)
from indian_stock_market_mcp.settings import DEFAULTS

DAYS = pd.bdate_range("2026-01-01", periods=80)


def history(closes, volumes=None, highs=None, days=None):
    """A stock's rows; high = low = close unless highs are given."""
    days = DAYS[-len(closes) :] if days is None else days
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "date": days,
            "open": closes,
            "high": closes if highs is None else [float(h) for h in highs],
            "low": closes,
            "close": closes,
            "volume": [1_000_000.0] * len(closes) if volumes is None else volumes,
        }
    )


def settings(**changes):
    return {**DEFAULTS, **changes}


# --- The trading calendar and the window ---------------------------------------------


def test_the_calendar_ignores_a_day_with_only_a_stray_row():
    rows = [
        {"date": day, "symbol": s} for day in DAYS[:5] for s in ("A", "B", "C", "D")
    ]
    rows.append({"date": pd.Timestamp("2026-01-03"), "symbol": "A"})  # a Saturday

    calendar = screens.trading_calendar(pd.DataFrame(rows))

    assert list(calendar) == list(DAYS[:5])


@pytest.mark.parametrize(
    ("period", "sessions"), [("1w", 5), ("1m", 21), ("3m", 63), ("1M", 21)]
)
def test_a_shortcut_counts_trading_sessions_back_from_the_latest_day(period, sessions):
    window = screens.resolve_window(DAYS, period)

    assert window.end == DAYS[-1]
    assert window.start == DAYS[-1 - sessions]
    assert window.sessions == sessions
    assert window.notes == ()


def test_the_default_period_is_one_month():
    assert screens.resolve_window(DAYS).label == "1m"


def test_a_shortcut_longer_than_the_data_is_refused():
    with pytest.raises(ValueError, match="needs 127 trading days; the dataset has 80"):
        screens.resolve_window(DAYS, "6m")


def test_an_unknown_shortcut_is_refused():
    with pytest.raises(ValueError, match="Unknown period '2m'"):
        screens.resolve_window(DAYS, "2m")


def test_year_to_date_starts_at_the_previous_years_last_close():
    calendar = pd.bdate_range("2025-12-22", "2026-01-15")

    window = screens.resolve_window(calendar, "ytd")

    assert window.start == pd.Timestamp("2025-12-31")
    assert window.end == pd.Timestamp("2026-01-15")
    assert window.sessions == 11  # 1 to 15 January 2026 has 11 weekdays
    assert window.notes == ()


def test_year_to_date_without_last_year_starts_at_the_first_session_and_says_so():
    window = screens.resolve_window(DAYS, "ytd")

    assert window.start == DAYS[0]
    assert "no session before 2026" in window.notes[0]


def test_dates_on_trading_days_are_used_as_given():
    window = screens.resolve_window(
        DAYS, start_date="2026-01-05", end_date="2026-01-16"
    )

    assert (window.start, window.end) == (
        pd.Timestamp("2026-01-05"),
        pd.Timestamp("2026-01-16"),
    )
    assert window.label == "custom"
    assert window.sessions == 9
    assert window.notes == ()


def test_a_weekend_start_moves_forward_and_a_weekend_end_moves_back():
    # 3 January 2026 is a Saturday and 18 January a Sunday.
    window = screens.resolve_window(
        DAYS, start_date="2026-01-03", end_date="2026-01-18"
    )

    assert window.start == pd.Timestamp("2026-01-05")  # the Monday after
    assert window.end == pd.Timestamp("2026-01-16")  # the Friday before
    assert len(window.notes) == 2
    assert "start_date 2026-01-03" in window.notes[0]
    assert "2026-01-05" in window.notes[0]
    assert "end_date 2026-01-18" in window.notes[1]
    assert "2026-01-16" in window.notes[1]


def test_the_end_date_defaults_to_the_latest_day():
    window = screens.resolve_window(DAYS, start_date="2026-01-05")

    assert window.end == DAYS[-1]
    assert window.notes == ()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"start_date": "2026-02-10", "end_date": "2026-02-10"}, "must be before"),
        ({"start_date": "2026-02-10", "end_date": "2026-02-02"}, "must be before"),
        ({"period": "1m", "start_date": "2026-02-10"}, "not both"),
        ({"end_date": "2026-02-10"}, "needs a start_date"),
        ({"start_date": "10/02/2026"}, "date like 2026-03-31"),
        ({"start_date": "2030-01-01", "end_date": "2030-02-01"}, "does not include"),
        # A Saturday to the Sunday after it holds no trading day at all.
        (
            {"start_date": "2026-01-03", "end_date": "2026-01-04"},
            "not two trading days",
        ),
    ],
)
def test_dates_that_cannot_make_a_window_are_refused(arguments, message):
    with pytest.raises(ValueError, match=message):
        screens.resolve_window(DAYS, **arguments)


# --- Ranking ---------------------------------------------------------------------------


def window_of(start, end):
    return screens.Window(DAYS[start], DAYS[end], "custom", end - start)


def test_stocks_are_ranked_by_their_return_between_the_two_days():
    rows = {
        "UP": history([100.0] * 70 + [110.0] * 10),
        "DOWN": history([100.0] * 70 + [80.0] * 10),
        "FLAT": history([50.0] * 80),
    }

    ranked, skipped = screens.rank_period(
        ("DOWN", "FLAT", "UP"), rows, window_of(60, 79)
    )

    assert [(r["symbol"], r["return_percent"]) for r in ranked] == [
        ("UP", 10.0),
        ("FLAT", 0.0),
        ("DOWN", -20.0),
    ]
    assert ranked[0] | {} == {
        "symbol": "UP",
        "start_date": "2026-03-26",
        "end_date": "2026-04-22",
        "start_close": 100.0,
        "end_close": 110.0,
        "return_percent": 10.0,
    }
    assert skipped == []


def test_equal_returns_are_ordered_by_symbol():
    rows = {"B": history([10.0] * 80), "A": history([20.0] * 80)}

    ranked, _ = screens.rank_period(("B", "A"), rows, window_of(0, 79))

    assert [r["symbol"] for r in ranked] == ["A", "B"]


def test_a_stock_that_did_not_trade_on_the_start_day_uses_its_close_before_it():
    gap = history([100.0] * 80).drop(index=60)  # no row on the start day
    gap.loc[59, "close"] = 90.0

    ranked, skipped = screens.rank_period(("GAP",), {"GAP": gap}, window_of(60, 79))

    assert ranked[0]["start_date"] == DAYS[59].strftime("%Y-%m-%d")
    assert ranked[0]["start_close"] == 90.0
    assert ranked[0]["return_percent"] == pytest.approx(11.11)
    assert skipped == []


def test_stocks_that_cannot_be_ranked_are_skipped_with_a_reason():
    rows = {
        "OK": history([100.0] * 80),
        "NEW": history([100.0] * 10),  # listed after the start day
        "OLD": history([100.0] * 70, days=DAYS[:70]),  # stopped trading
    }

    ranked, skipped = screens.rank_period(
        ("GONE", "OLD", "NEW", "OK"), rows, window_of(40, 79)
    )

    assert [r["symbol"] for r in ranked] == ["OK"]
    assert [(s["symbol"], s["code"]) for s in skipped] == [
        ("GONE", "symbol_not_found"),
        ("OLD", "stale_data"),
        ("NEW", "short_history"),
    ]
    assert "after the start date 2026-02-26" in skipped[2]["reason"]


def test_a_window_ending_before_the_latest_day_ignores_later_prices():
    rows = {"X": history([100.0] * 60 + [200.0] * 20)}

    ranked, _ = screens.rank_period(("X",), rows, window_of(10, 59))

    assert ranked[0]["end_close"] == 100.0
    assert ranked[0]["return_percent"] == 0.0


# --- Volume and breakout levels ----------------------------------------------------------


def test_relative_volume_leaves_the_latest_session_out_of_its_average():
    volume = pd.Series([100.0] * 20 + [250.0])

    # 250 / 100. With today in the average it would be 250 / 107.5 = 2.33.
    assert relative_volume(volume, 20) == 2.5


def test_relative_volume_uses_only_the_sessions_asked_for():
    volume = pd.Series([1000.0] * 30 + [100.0] * 5 + [300.0])

    assert relative_volume(volume, 5) == 3.0


def test_relative_volume_is_unknown_without_a_latest_volume_or_an_average():
    assert relative_volume(pd.Series([100.0] * 20 + [np.nan]), 20) is None
    assert relative_volume(pd.Series([0.0] * 20 + [50.0]), 20) is None


def test_relative_volume_needs_one_session_more_than_the_average():
    with pytest.raises(InsufficientHistoryError):
        relative_volume(pd.Series([100.0] * 20), 20)


def test_the_highest_high_leaves_out_the_latest_session():
    rows = history([100.0] * 30, highs=[101.0] * 29 + [150.0])

    level = highest_high(rows, 20)

    assert level.level == 101.0


def test_the_highest_high_only_looks_at_the_sessions_asked_for():
    highs = [100.0] * 30
    highs[5] = 140.0  # 24 sessions before the latest one: outside a 20-session look
    highs[15] = 120.0  # 14 sessions before: inside
    rows = history([100.0] * 30, highs=highs)

    level = highest_high(rows, 20)

    assert level.level == 120.0
    assert level.date == rows["date"].iloc[15]


def test_the_most_recent_of_two_equal_highs_is_reported():
    highs = [100.0] * 30
    highs[12] = highs[20] = 120.0
    rows = history([100.0] * 30, highs=highs)

    assert highest_high(rows, 20).date == rows["date"].iloc[20]


def swing(peaks, length=41, last=100.0):
    """Flat at 100 with triangular peaks: {index: height}. `last` is the final close."""
    closes = [100.0] * length
    for at, height in peaks.items():
        for offset in range(-4, 5):
            closes[at + offset] = max(
                closes[at + offset], height - 2.0 * abs(offset) * (height - 100) / 10
            )
    closes[-1] = last
    return history(closes)


def test_the_nearest_resistance_is_the_lowest_swing_high_above_the_previous_close():
    rows = swing({10: 110.0, 25: 105.0})

    level = nearest_resistance(rows, pivot=3, lookback=120)

    assert level.level == 105.0
    assert level.date == rows["date"].iloc[25]


def test_a_swing_high_already_closed_above_is_no_longer_resistance():
    rows = swing({10: 105.0, 25: 110.0})  # the later peak closed above the first

    level = nearest_resistance(rows, pivot=3, lookback=120)

    assert level.level == 110.0


def test_there_is_no_resistance_when_no_swing_high_stands_above_the_price():
    rows = history([100.0 + i for i in range(40)])  # a straight climb has no swing high

    assert nearest_resistance(rows, pivot=3, lookback=120) is None


def test_resistance_only_looks_back_as_far_as_asked():
    rows = swing({10: 110.0}, length=61)

    assert nearest_resistance(rows, pivot=3, lookback=120).level == 110.0
    assert nearest_resistance(rows, pivot=3, lookback=30) is None


def test_the_latest_session_is_never_its_own_resistance():
    rows = swing({10: 110.0}, last=130.0)

    assert nearest_resistance(rows, pivot=3, lookback=120).level == 110.0


def test_resistance_needs_enough_sessions_to_confirm_a_swing():
    with pytest.raises(InsufficientHistoryError):
        nearest_resistance(history([100.0] * 7), pivot=3, lookback=120)


# --- Rules ---------------------------------------------------------------------------


def test_rules_are_checked_and_a_rule_set_to_none_is_switched_off():
    assert screens.check_rules({"rsi_max": 30, "above_ema_period": None}) == {
        "rsi_max": 30.0
    }


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"rsi_maximum": 30}, "Unknown rule"),
        ({"rsi_max": 130}, "between 0 and 100"),
        ({"rsi_max": "30"}, "must be a number"),
        ({"above_ema_period": 1}, "between 2 and 400"),
        ({"above_ema_period": 50.5}, "whole number"),
        ({"rsi_min": 70, "rsi_max": 50}, "must not be above"),
        ({"breakout_method": "magic"}, "must be one of"),
        ({"min_volume_ratio": True}, "must be a number"),
    ],
)
def test_bad_rules_are_refused(rules, message):
    with pytest.raises(ValueError, match=message):
        screens.check_rules(rules)


def rising_with_dips(length=80):
    """Up 1% twice, down 1% once: an uptrend whose RSI sits near 67."""
    closes, price = [], 100.0
    for i in range(length):
        price *= 0.99 if i % 3 == 2 else 1.01
        closes.append(price)
    return closes


MOMENTUM = screens.momentum_rules(DEFAULTS)


def evaluate(rows, rules=MOMENTUM, **changes):
    return screens.evaluate_stock("X", rows, rules, settings(**changes))


def test_a_stock_meeting_every_momentum_rule_passes_with_its_numbers():
    row, failed = evaluate(history(rising_with_dips()))

    assert failed == []
    assert 50 <= row["rsi"] <= 70
    assert row["close_vs_ema_percent"] > 0
    closes = rising_with_dips()
    assert row["return_percent"] == pytest.approx(
        (closes[-1] / closes[-64] - 1) * 100, abs=0.01
    )
    # 1,000,000 shares at about Rs 130 is about Rs 13 crore a day.
    assert row["traded_value_crore"] == pytest.approx(
        np.mean(closes[-20:]) * 1_000_000 / 1e7, abs=0.01
    )


def test_a_stock_rising_every_day_is_overbought():
    _, failed = evaluate(history([100 * 1.01**i for i in range(80)]))

    assert failed == ["rsi_too_high"]


def test_a_falling_stock_fails_every_trend_rule_and_is_listed_under_each():
    _, failed = evaluate(history([200 * 0.99**i for i in range(80)]))

    assert failed == ["below_ema", "rsi_too_low", "weak_return"]


def test_a_thinly_traded_stock_is_illiquid():
    row, failed = evaluate(history(rising_with_dips(), volumes=[10.0] * 80))

    assert failed == ["illiquid"]
    assert row["traded_value_crore"] < 1


def test_a_liquidity_minimum_of_zero_switches_the_filter_off():
    rules = screens.momentum_rules(settings(illiquid_below_crore=0))

    _, failed = evaluate(history(rising_with_dips(), volumes=[10.0] * 80), rules)

    assert "min_traded_value_crore" not in rules
    assert failed == []


def test_a_stock_without_enough_history_says_so_once():
    row, failed = evaluate(history(rising_with_dips(10)))

    assert failed == ["too_little_history"]
    assert row["ema"] is None
    assert row["return_percent"] is None


def test_a_stock_without_volume_cannot_pass_a_liquidity_rule():
    _, failed = evaluate(history(rising_with_dips(), volumes=[np.nan] * 80))

    assert failed == ["no_volume_data"]


def test_a_return_exactly_at_the_minimum_does_not_pass():
    flat = history([100.0] * 80)

    _, failed = screens.evaluate_stock(
        "X", flat, {"min_return_percent": 0.0}, settings()
    )

    assert failed == ["weak_return"]


def test_a_close_exactly_at_the_breakout_level_is_not_a_breakout():
    rows = history([100.0] * 30, highs=[105.0] * 29 + [105.0])
    rows.loc[rows.index[-1], "close"] = 105.0

    row, failed = screens.evaluate_stock(
        "X", rows, {"breakout_sessions": 20}, settings()
    )

    assert failed == ["no_breakout"]
    assert row["breakout_level"] == 105.0
    assert row["close_vs_level_percent"] == 0.0


def test_a_close_above_the_previous_highs_is_a_breakout():
    rows = history([100.0] * 29 + [106.0], highs=[105.0] * 29 + [107.0])

    row, failed = screens.evaluate_stock(
        "X", rows, {"breakout_sessions": 20}, settings()
    )

    assert failed == []
    assert row["close_vs_level_percent"] == pytest.approx(0.95)


def test_the_volume_rule_passes_at_the_multiple_and_fails_just_below():
    at = history([100.0] * 30, volumes=[100.0] * 29 + [200.0])
    below = history([100.0] * 30, volumes=[100.0] * 29 + [199.0])
    rules = {"min_volume_ratio": 2.0}

    assert screens.evaluate_stock("X", at, rules, settings())[1] == []
    assert screens.evaluate_stock("X", below, rules, settings())[1] == ["low_volume"]


def test_extra_measurements_are_shown_without_being_rules():
    rows = history([100.0] * 30, volumes=[100.0] * 29 + [150.0])

    row, failed = screens.evaluate_stock(
        "X", rows, {"rsi_max": 100}, settings(), show=("volume", "breakout")
    )

    assert failed == []
    assert row["volume_ratio"] == 1.5
    assert row["breakout_level"] == 100.0


def test_resistance_breakouts_use_the_swing_high_not_the_recent_high():
    rows = swing({10: 110.0}, last=111.0)
    rules = {"breakout_method": "resistance"}

    row, failed = screens.evaluate_stock(
        "X", rows, rules, settings(resistance_pivot_sessions=3)
    )

    assert failed == []
    assert row["breakout_level"] == 110.0
    assert row["breakout_method"] == "resistance"

    below = swing({10: 110.0}, last=109.0)
    assert screens.evaluate_stock(
        "X", below, rules, settings(resistance_pivot_sessions=3)
    )[1] == ["no_breakout"]


# --- Running a screen ------------------------------------------------------------------


def momentum_market():
    return {
        "MOMO": history(rising_with_dips()),
        "HOT": history([100 * 1.01**i for i in range(80)]),
        "DOWN": history([200 * 0.99**i for i in range(80)]),
        "THIN": history(rising_with_dips(), volumes=[10.0] * 80),
        "NEW": history(rising_with_dips(10)),
        "OLD": history(rising_with_dips(70), days=DAYS[:70]),
    }


def test_a_screen_lists_who_passes_and_why_everyone_else_does_not():
    names = ("MOMO", "HOT", "DOWN", "THIN", "NEW", "OLD", "GONE")

    result = screens.run_screen(
        names, momentum_market(), MOMENTUM, settings(), sort_by="return_percent"
    )

    assert [row["symbol"] for row in result["matches"]] == ["MOMO"]
    assert {e["reason"]: e["symbols"] for e in result["excluded"]} == {
        "illiquid": ["THIN"],
        "below_ema": ["DOWN"],
        "rsi_too_low": ["DOWN"],
        "rsi_too_high": ["HOT"],
        "weak_return": ["DOWN"],
        "too_little_history": ["NEW"],
    }
    assert [e["count"] for e in result["excluded"]] == [1, 1, 1, 1, 1, 1]
    assert result["excluded_count"] == 4  # DOWN is counted once
    assert result["checked"] == 5
    assert [(s["symbol"], s["code"]) for s in result["skipped"]] == [
        ("OLD", "stale_data"),
        ("GONE", "symbol_not_found"),
    ]
    assert result["as_of"] == DAYS[-1].strftime("%Y-%m-%d")


def test_matches_are_ordered_by_the_chosen_number_then_symbol():
    market = {
        "B": history([100.0] * 79 + [130.0]),
        "A": history([100.0] * 79 + [130.0]),
        "C": history([100.0] * 79 + [150.0]),
    }
    rules = {"min_return_percent": 0.0, "return_sessions": 5}

    best_first = screens.run_screen(
        ("B", "A", "C"), market, rules, settings(), sort_by="return_percent"
    )
    worst_first = screens.run_screen(
        ("B", "A", "C"),
        market,
        rules,
        settings(),
        sort_by="return_percent",
        descending=False,
    )

    assert [r["symbol"] for r in best_first["matches"]] == ["C", "A", "B"]
    assert [r["symbol"] for r in worst_first["matches"]] == ["A", "B", "C"]


# --- Presets ---------------------------------------------------------------------------


def test_the_four_presets_take_their_rules_from_the_settings():
    found = screens.presets(DEFAULTS)

    assert list(found) == [
        "strong_momentum",
        "near_52_week_high",
        "oversold",
        "volume_breakout",
    ]
    assert found["strong_momentum"]["rules"] == {
        "min_traded_value_crore": 1.0,
        "above_ema_period": 50,
        "rsi_min": 50.0,
        "rsi_max": 70.0,
        "return_sessions": 63,
        "min_return_percent": 0.0,
    }
    assert found["near_52_week_high"]["rules"] == {
        "min_traded_value_crore": 1.0,
        "within_percent_of_high": 5.0,
    }
    assert found["oversold"]["rules"] == {
        "min_traded_value_crore": 1.0,
        "rsi_max": 30.0,
    }
    assert found["volume_breakout"]["rules"] == {
        "min_traded_value_crore": 1.0,
        "min_volume_ratio": 2.0,
        "volume_sessions": 20,
        "breakout_sessions": 20,
    }


def test_a_changed_setting_changes_the_preset():
    found = screens.presets(settings(oversold_rsi=25.0, near_high_percent=2.0))

    assert found["oversold"]["rules"]["rsi_max"] == 25.0
    assert found["near_52_week_high"]["rules"]["within_percent_of_high"] == 2.0


@pytest.mark.parametrize(
    "name",
    [
        "oversold",
        "Oversold",
        " OVERSOLD ",
        "Strong momentum, liquid",
        "near-52-week-high",
    ],
)
def test_a_preset_is_found_by_its_id_or_its_name(name):
    key, preset = screens.get_preset(name, DEFAULTS)

    assert key in screens.presets(DEFAULTS)
    assert preset["rules"]


def test_an_unknown_preset_lists_the_ones_there_are():
    with pytest.raises(ValueError, match="Unknown preset 'value'. Available: strong"):
        screens.get_preset("value", DEFAULTS)


def test_every_preset_rule_reads_as_a_sentence():
    for preset in screens.presets(DEFAULTS).values():
        lines = screens.describe_rules(preset["rules"], DEFAULTS)
        assert len(lines) >= 2
        assert all(line[0].isupper() for line in lines)

    assert screens.describe_rules(MOMENTUM, DEFAULTS) == [
        "Average traded value over 20 sessions is at least Rs 1 crore a day",
        "Close is above the 50-day EMA",
        "RSI(14) is between 50 and 70",
        "Return over 63 sessions is above 0%",
    ]


# --- Breadth ---------------------------------------------------------------------------


def test_breadth_counts_moves_ema_shares_and_year_extremes():
    market = {
        "UP1": history([100.0 + i for i in range(80)]),  # rose, new high, above EMAs
        "UP2": history([100.0] * 79 + [101.0]),  # rose, new high
        "DOWN": history([200.0 - i for i in range(80)]),  # fell, new low, below EMAs
        "SAME": history([50.0] * 80),  # unchanged
        "NEW": history([10.0, 11.0]),  # rose; too short for any EMA
        "ONE": history([10.0]),  # a single session: no move to count
        "OLD": history([10.0] * 70, days=DAYS[:70]),
    }

    result = screens.market_breadth(
        ("UP1", "UP2", "DOWN", "SAME", "NEW", "ONE", "OLD", "GONE"),
        market,
        [50, 200],
        252,
    )

    assert result["checked"] == 6
    assert result["advance_decline"] == {
        "advancers": 3,
        "decliners": 1,
        "unchanged": 1,
        "advancers_percent": 60.0,
        "decliners_percent": 20.0,
        "ratio": 3.0,
    }
    # EMA 50 can be measured for the four 80-session stocks; EMA 200 for none.
    assert result["above_ema"] == [
        {"period": 50, "above": 2, "measured": 4, "percent": 50.0},
        {"period": 200, "above": 0, "measured": 0, "percent": None},
    ]
    assert result["year_extremes"]["new_high_symbols"] == ["UP1", "UP2", "SAME", "NEW"]
    assert result["year_extremes"]["new_low_symbols"] == ["DOWN", "SAME"]
    assert [(s["symbol"], s["code"]) for s in result["skipped"]] == [
        ("OLD", "stale_data"),
        ("GONE", "symbol_not_found"),
    ]


def test_breadth_with_no_decliners_has_no_ratio():
    result = screens.market_breadth(("A",), {"A": history([1.0, 2.0, 3.0])}, [2], 252)

    assert result["advance_decline"]["ratio"] is None
    assert result["above_ema"][0] == {
        "period": 2,
        "above": 1,
        "measured": 1,
        "percent": 100.0,
    }
