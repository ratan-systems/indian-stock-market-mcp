"""Looking across many stocks: period rankings, screens, presets and breadth.

Everything here works on rows already loaded (one frame per symbol, adjusted
for splits and bonuses) and reads no files, so the tools stay thin and the
rules can be tested without a dataset.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from .indicators import (
    InsufficientHistoryError,
    average_traded_value,
    ema,
    highest_high,
    nearest_resistance,
    period_return,
    relative_volume,
    rsi,
    year_range,
)

CRORE = 10_000_000
PRICE_DECIMALS = 4
PERCENT_DECIMALS = 2

# A date counts as a trading day when at least this share of a typical day's
# rows is present, so a stray row on a holiday does not set the calendar.
CALENDAR_SHARE = 0.5

# What a shortcut means in trading sessions, the convention charting tools use
# (a week is 5 bars, a month 21, a year 252).
PERIOD_SESSIONS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}
YEAR_TO_DATE = "ytd"
PERIODS = (*PERIOD_SESSIONS, YEAR_TO_DATE)


def rounded(value: float, decimals: int = PRICE_DECIMALS) -> float:
    return round(float(value), decimals)


def day_text(value: pd.Timestamp) -> str:
    return value.strftime("%Y-%m-%d")


# --- The stocks to look at ---------------------------------------------------------


def current_histories(
    symbols: tuple[str, ...],
    rows_by_symbol: dict[str, pd.DataFrame],
    as_of: pd.Timestamp | None = None,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, str]], pd.Timestamp | None]:
    """Each symbol's history up to `as_of` (default: the latest date among the
    symbols), oldest first.

    A symbol that is not in the data, or whose last session is before that
    date, is skipped with a code and reason, so a list never mixes dates.
    """
    available = {s: rows_by_symbol[s] for s in symbols if s in rows_by_symbol}
    latest = as_of
    if latest is None:
        latest = max((rows["date"].max() for rows in available.values()), default=None)
    histories: dict[str, pd.DataFrame] = {}
    skipped: list[dict[str, str]] = []
    for symbol in symbols:
        rows = available.get(symbol)
        if rows is None:
            skipped.append(
                {
                    "symbol": symbol,
                    "code": "symbol_not_found",
                    "reason": f"Symbol '{symbol}' was not found",
                }
            )
            continue
        history = rows[rows["date"] <= latest].sort_values("date")
        if history.empty or history["date"].iloc[-1] < latest:
            last = (
                "no sessions" if history.empty else day_text(history["date"].iloc[-1])
            )
            skipped.append(
                {
                    "symbol": symbol,
                    "code": "stale_data",
                    "reason": f"Last session ({last}) is before {day_text(latest)}",
                }
            )
            continue
        histories[symbol] = history
    return histories, skipped, latest


# --- Ranking over any period ---------------------------------------------------------


def trading_calendar(frame: pd.DataFrame) -> pd.DatetimeIndex:
    """The dates the market traded, oldest first."""
    counts = frame.groupby("date").size().sort_index()
    if counts.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(counts.index[counts >= CALENDAR_SHARE * counts.median()])


@dataclass(frozen=True)
class Window:
    """The two trading days a return is measured between."""

    start: pd.Timestamp
    end: pd.Timestamp
    label: str  # the shortcut used, or "custom" for dates
    sessions: int  # trading sessions from start to end
    notes: tuple[str, ...] = field(default_factory=tuple)  # dates that were moved


def parse_day(name: str, value: Any) -> pd.Timestamp:
    if isinstance(value, date):
        return pd.Timestamp(value)
    try:
        return pd.Timestamp(date.fromisoformat(str(value).strip()))
    except ValueError as error:
        raise ValueError(
            f"{name} must be a date like 2026-03-31, got {value!r}"
        ) from error


def resolve_window(
    calendar: pd.DatetimeIndex,
    period: str | None = None,
    start_date: Any = None,
    end_date: Any = None,
) -> Window:
    """Turn a shortcut or a pair of dates into two trading days.

    A shortcut counts trading sessions back from the latest date; "ytd" starts
    at the last close of the previous year. A start date that is not a trading
    day moves forward to the next one and an end date moves back to the one
    before, so the window never reaches outside the dates asked for. Each move
    is recorded in the window's notes.
    """
    if len(calendar) < 2:
        raise ValueError("The dataset needs at least two trading days to rank a period")
    if start_date is None and end_date is not None:
        raise ValueError("end_date needs a start_date; use period for a shortcut")
    if start_date is not None:
        if period is not None:
            raise ValueError("Give either a period or dates, not both")
        return _dates_window(calendar, start_date, end_date)

    label = (period or "1m").strip().lower()
    end = calendar[-1]
    if label == YEAR_TO_DATE:
        earlier = calendar[calendar.year < end.year]
        if len(earlier):
            start, notes = earlier[-1], ()
        else:
            start = calendar[0]
            notes = (
                (
                    f"The data has no session before {end.year}, so the year to "
                    f"date is measured from {day_text(start)}, its first session."
                ),
            )
        if start == end:
            raise ValueError("The year to date needs at least two trading days")
        return Window(start, end, label, len(calendar[calendar > start]), notes)
    if label not in PERIOD_SESSIONS:
        raise ValueError(
            f"Unknown period '{period}'. Use one of {', '.join(PERIODS)}, "
            "or give start_date and end_date"
        )
    sessions = PERIOD_SESSIONS[label]
    if len(calendar) <= sessions:
        raise ValueError(
            f"Period {label} needs {sessions + 1} trading days; the dataset has "
            f"{len(calendar)}"
        )
    return Window(calendar[-1 - sessions], end, label, sessions)


def _dates_window(calendar: pd.DatetimeIndex, start_date: Any, end_date: Any) -> Window:
    asked_start = parse_day("start_date", start_date)
    asked_end = calendar[-1] if end_date is None else parse_day("end_date", end_date)
    if asked_start >= asked_end:
        raise ValueError("start_date must be before end_date")
    on_or_after = calendar[calendar >= asked_start]
    on_or_before = calendar[calendar <= asked_end]
    if not len(on_or_after) or not len(on_or_before):
        raise ValueError(
            f"The dataset covers {day_text(calendar[0])} to {day_text(calendar[-1])}, "
            "which does not include those dates"
        )
    start, end = on_or_after[0], on_or_before[-1]
    if start >= end:
        raise ValueError(
            f"There are not two trading days between {day_text(asked_start)} and "
            f"{day_text(asked_end)}"
        )
    notes = []
    if start != asked_start:
        notes.append(
            f"start_date {day_text(asked_start)} is not a trading day in the data; "
            f"using the next one, {day_text(start)}."
        )
    if end_date is not None and end != asked_end:
        notes.append(
            f"end_date {day_text(asked_end)} is not a trading day in the data; "
            f"using the one before, {day_text(end)}."
        )
    sessions = len(calendar[(calendar > start) & (calendar <= end)])
    return Window(start, end, "custom", sessions, tuple(notes))


def rank_period(
    symbols: tuple[str, ...],
    rows_by_symbol: dict[str, pd.DataFrame],
    window: Window,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Every stock's return between the window's two days, best first.

    A stock must have traded on the end date. Its start price is its close on
    the start date, or its last close before it when it did not trade that day;
    a stock that only started trading after the start date is skipped.
    """
    histories, skipped, _ = current_histories(symbols, rows_by_symbol, window.end)
    ranked = []
    for symbol, history in histories.items():
        before = history[history["date"] <= window.start]
        if before.empty:
            skipped.append(
                {
                    "symbol": symbol,
                    "code": "short_history",
                    "reason": f"First session {day_text(history['date'].iloc[0])} is "
                    f"after the start date {day_text(window.start)}",
                }
            )
            continue
        start_row, end_row = before.iloc[-1], history.iloc[-1]
        start_close, end_close = float(start_row["close"]), float(end_row["close"])
        ranked.append(
            {
                "symbol": symbol,
                "start_date": day_text(start_row["date"]),
                "end_date": day_text(end_row["date"]),
                "start_close": rounded(start_close),
                "end_close": rounded(end_close),
                "return_percent": rounded(
                    (end_close / start_close - 1) * 100, PERCENT_DECIMALS
                ),
            }
        )
    ranked.sort(key=lambda row: (-row["return_percent"], row["symbol"]))
    # Keep the skipped list in the order the symbols were given.
    order = {symbol: at for at, symbol in enumerate(symbols)}
    skipped.sort(key=lambda entry: order[entry["symbol"]])
    return ranked, skipped


# --- Screens -----------------------------------------------------------------------

BREAKOUT_METHODS = ("highest_high", "resistance")

# Reasons a stock can be left out of a screen, with the wording shown to the user.
REASONS = {
    "illiquid": "average traded value is below the minimum",
    "below_ema": "close is not above the EMA",
    "rsi_too_low": "RSI is below the minimum",
    "rsi_too_high": "RSI is above the maximum",
    "weak_return": "return over the period is below the minimum",
    "far_from_high": "close is too far below the 52-week high",
    "low_volume": "volume is below the multiple of its average",
    "no_breakout": "close is not above the breakout level",
    "too_little_history": "not enough history to check every rule",
    "no_volume_data": "no volume data",
}


class RuleError(ValueError):
    """Raised when a screen rule has an unknown name or a value out of range."""


def _number(name: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RuleError(f"{name} must be a number, got {value!r}")
    if not low <= value <= high:
        raise RuleError(f"{name} must be between {low:g} and {high:g}, got {value}")
    return float(value)


def _whole(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuleError(f"{name} must be a whole number, got {value!r}")
    if not low <= value <= high:
        raise RuleError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _method(value: Any) -> str:
    if value not in BREAKOUT_METHODS:
        raise RuleError(
            f"breakout_method must be one of {', '.join(BREAKOUT_METHODS)}, got {value!r}"
        )
    return value


# Every rule a screen can use and how its value is checked.
RULE_CHECKS = {
    "min_traded_value_crore": lambda v: _number("min_traded_value_crore", v, 0, 100000),
    "above_ema_period": lambda v: _whole("above_ema_period", v, 2, 400),
    "rsi_min": lambda v: _number("rsi_min", v, 0, 100),
    "rsi_max": lambda v: _number("rsi_max", v, 0, 100),
    "return_sessions": lambda v: _whole("return_sessions", v, 1, 504),
    "min_return_percent": lambda v: _number("min_return_percent", v, -100, 1000),
    "within_percent_of_high": lambda v: _number("within_percent_of_high", v, 0, 100),
    "min_volume_ratio": lambda v: _number("min_volume_ratio", v, 0.1, 100),
    "volume_sessions": lambda v: _whole("volume_sessions", v, 5, 250),
    "breakout_sessions": lambda v: _whole("breakout_sessions", v, 2, 260),
    "breakout_method": _method,
}


def check_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Check rule names and values. A rule set to None is switched off."""
    if not isinstance(rules, dict):
        raise RuleError("Rules must be an object of rule names and values")
    unknown = sorted(set(rules) - set(RULE_CHECKS))
    if unknown:
        raise RuleError(
            f"Unknown rule(s): {', '.join(unknown)}. Available: {', '.join(RULE_CHECKS)}"
        )
    cleaned = {
        name: RULE_CHECKS[name](value)
        for name, value in rules.items()
        if value is not None
    }
    low, high = cleaned.get("rsi_min"), cleaned.get("rsi_max")
    if low is not None and high is not None and low > high:
        raise RuleError(f"rsi_min ({low:g}) must not be above rsi_max ({high:g})")
    return cleaned


def describe_rules(rules: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    """The rules in plain English, in the order they are checked."""
    lines = []
    if rules.get("min_traded_value_crore"):
        lines.append(
            f"Average traded value over {settings['traded_value_sessions']} sessions "
            f"is at least Rs {rules['min_traded_value_crore']:g} crore a day"
        )
    if "above_ema_period" in rules:
        lines.append(f"Close is above the {rules['above_ema_period']}-day EMA")
    if "rsi_min" in rules or "rsi_max" in rules:
        low, high = rules.get("rsi_min"), rules.get("rsi_max")
        period = settings["rsi_period"]
        if low is not None and high is not None:
            lines.append(f"RSI({period}) is between {low:g} and {high:g}")
        elif low is not None:
            lines.append(f"RSI({period}) is at least {low:g}")
        else:
            lines.append(f"RSI({period}) is at most {high:g}")
    if "min_return_percent" in rules:
        sessions = rules.get("return_sessions", settings["momentum_return_sessions"])
        lines.append(
            f"Return over {sessions} sessions is above {rules['min_return_percent']:g}%"
        )
    if "within_percent_of_high" in rules:
        lines.append(
            f"Close is within {rules['within_percent_of_high']:g}% of the 52-week high"
        )
    if "min_volume_ratio" in rules:
        sessions = rules.get("volume_sessions", settings["volume_average_sessions"])
        lines.append(
            f"Volume is at least {rules['min_volume_ratio']:g}x the average of the "
            f"previous {sessions} sessions"
        )
    if "breakout_sessions" in rules or rules.get("breakout_method") == "resistance":
        if rules.get("breakout_method") == "resistance":
            lines.append(
                "Close is above the nearest resistance: the lowest swing high of the "
                f"last {settings['resistance_lookback_sessions']} sessions not yet "
                "closed above"
            )
        else:
            lines.append(
                "Close is above the highest high of the previous "
                f"{rules['breakout_sessions']} sessions"
            )
    return lines


def evaluate_stock(
    symbol: str,
    history: pd.DataFrame,
    rules: dict[str, Any],
    settings: dict[str, Any],
    show: tuple[str, ...] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Measure one stock against the rules.

    Returns its numbers and the reasons it fails (empty when it passes). Every
    rule is checked, so a stock is reported under each one it fails. `show`
    names extra measurements ("volume", "breakout") to include even when no
    rule uses them; one that cannot be calculated is left as None.
    """
    close = history["close"]
    latest_close = float(close.iloc[-1])
    row: dict[str, Any] = {"symbol": symbol, "close": rounded(latest_close)}
    failed: list[str] = []

    def fail(reason: str) -> None:
        if reason not in failed:
            failed.append(reason)

    has_volume = "volume" in history and history["volume"].notna().any()

    minimum = rules.get("min_traded_value_crore")
    if minimum:
        row["traded_value_crore"] = None
        if not has_volume:
            fail("no_volume_data")
        else:
            try:
                crore = (
                    average_traded_value(
                        close, history["volume"], settings["traded_value_sessions"]
                    )
                    / CRORE
                )
            except InsufficientHistoryError:
                fail("too_little_history")
            else:
                row["traded_value_crore"] = rounded(crore, PERCENT_DECIMALS)
                if crore < minimum:
                    fail("illiquid")

    if "above_ema_period" in rules:
        period = rules["above_ema_period"]
        row |= {"ema_period": period, "ema": None, "close_vs_ema_percent": None}
        try:
            value = float(ema(close, period).iloc[-1])
        except InsufficientHistoryError:
            fail("too_little_history")
        else:
            row["ema"] = rounded(value)
            row["close_vs_ema_percent"] = rounded(
                (latest_close / value - 1) * 100, PERCENT_DECIMALS
            )
            if not latest_close > value:
                fail("below_ema")

    if "rsi_min" in rules or "rsi_max" in rules:
        row["rsi"] = None
        try:
            value = float(rsi(close, settings["rsi_period"]).iloc[-1])
        except InsufficientHistoryError:
            fail("too_little_history")
        else:
            row["rsi"] = rounded(value, PERCENT_DECIMALS)
            if "rsi_min" in rules and value < rules["rsi_min"]:
                fail("rsi_too_low")
            if "rsi_max" in rules and value > rules["rsi_max"]:
                fail("rsi_too_high")

    if "min_return_percent" in rules:
        sessions = rules.get("return_sessions", settings["momentum_return_sessions"])
        row |= {"return_sessions": sessions, "return_percent": None}
        try:
            value = period_return(close, sessions)
        except InsufficientHistoryError:
            fail("too_little_history")
        else:
            row["return_percent"] = rounded(value, PERCENT_DECIMALS)
            if not value > rules["min_return_percent"]:
                fail("weak_return")

    if "within_percent_of_high" in rules:
        row |= {"high_52w": None, "percent_below_high": None}
        try:
            found = year_range(history, settings["year_sessions"])
        except InsufficientHistoryError:
            fail("too_little_history")
        else:
            row["high_52w"] = rounded(found.high)
            row["percent_below_high"] = rounded(
                found.percent_below_high, PERCENT_DECIMALS
            )
            if found.percent_below_high > rules["within_percent_of_high"]:
                fail("far_from_high")

    wants_volume = "min_volume_ratio" in rules
    if wants_volume or "volume" in show:
        sessions = rules.get("volume_sessions", settings["volume_average_sessions"])
        row |= {"volume": None, "volume_ratio": None}
        if not has_volume:
            if wants_volume:
                fail("no_volume_data")
        else:
            latest_volume = history["volume"].iloc[-1]
            row["volume"] = (
                None if pd.isna(latest_volume) else rounded(latest_volume, 0)
            )
            try:
                ratio = relative_volume(history["volume"], sessions)
            except InsufficientHistoryError:
                if wants_volume:
                    fail("too_little_history")
            else:
                if ratio is None:
                    if wants_volume:
                        fail("no_volume_data")
                else:
                    row["volume_ratio"] = rounded(ratio, PERCENT_DECIMALS)
                    if wants_volume and ratio < rules["min_volume_ratio"]:
                        fail("low_volume")

    method = rules.get("breakout_method", "highest_high")
    wants_breakout = "breakout_sessions" in rules or (
        "breakout_method" in rules and method == "resistance"
    )
    if wants_breakout or "breakout" in show:
        row |= {
            "breakout_method": method,
            "breakout_level": None,
            "breakout_level_date": None,
            "close_vs_level_percent": None,
        }
        try:
            if method == "resistance":
                level = nearest_resistance(
                    history,
                    settings["resistance_pivot_sessions"],
                    settings["resistance_lookback_sessions"],
                )
            else:
                level = highest_high(
                    history,
                    rules.get("breakout_sessions", settings["breakout_sessions"]),
                )
        except InsufficientHistoryError:
            if wants_breakout:
                fail("too_little_history")
        else:
            if level is None:
                # No resistance stands above the previous close.
                if wants_breakout:
                    fail("no_breakout")
            else:
                row["breakout_level"] = rounded(level.level)
                row["breakout_level_date"] = day_text(level.date)
                row["close_vs_level_percent"] = rounded(
                    (latest_close / level.level - 1) * 100, PERCENT_DECIMALS
                )
                if wants_breakout and not latest_close > level.level:
                    fail("no_breakout")

    return row, failed


def run_screen(
    symbols: tuple[str, ...],
    rows_by_symbol: dict[str, pd.DataFrame],
    rules: dict[str, Any],
    settings: dict[str, Any],
    sort_by: str | None = None,
    descending: bool = True,
    show: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Check every stock against the rules.

    Returns the stocks that pass (ordered by `sort_by`, then symbol), the
    stocks left out grouped by reason (a stock appears under every rule it
    fails), the symbols skipped for data reasons and the date of the data.
    """
    histories, skipped, latest = current_histories(symbols, rows_by_symbol)
    matches: list[dict[str, Any]] = []
    excluded: dict[str, list[str]] = {}
    for symbol, history in histories.items():
        row, failed = evaluate_stock(symbol, history, rules, settings, show)
        if failed:
            for reason in failed:
                excluded.setdefault(reason, []).append(symbol)
        else:
            matches.append(row)

    def order(row: dict[str, Any]):
        value = row.get(sort_by) if sort_by else None
        missing = value is None
        key = 0.0 if missing else (-value if descending else value)
        return (missing, key, row["symbol"])

    matches.sort(key=order)
    return {
        "matches": matches,
        "excluded": [
            {
                "reason": reason,
                "meaning": REASONS[reason],
                "count": len(excluded[reason]),
                "symbols": excluded[reason],
            }
            for reason in REASONS
            if reason in excluded
        ],
        "excluded_count": len({s for names in excluded.values() for s in names}),
        "checked": len(histories),
        "skipped": skipped,
        "as_of": None if latest is None else day_text(latest),
    }


# --- Presets -----------------------------------------------------------------------


def liquidity_rule(settings: dict[str, Any]) -> dict[str, Any]:
    """The saved illiquid limit as a screen rule (off when the limit is 0)."""
    limit = settings["illiquid_below_crore"]
    return {"min_traded_value_crore": limit} if limit else {}


def momentum_rules(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        **liquidity_rule(settings),
        "above_ema_period": settings["momentum_ema_period"],
        "rsi_min": settings["momentum_rsi_min"],
        "rsi_max": settings["momentum_rsi_max"],
        "return_sessions": settings["momentum_return_sessions"],
        "min_return_percent": settings["momentum_min_return_percent"],
    }


def presets(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The built-in screens, with their rules filled in from the settings."""
    return {
        "strong_momentum": {
            "name": "Strong momentum, liquid",
            "description": "Liquid stocks in an uptrend that are not yet overbought.",
            "rules": momentum_rules(settings),
            "sort_by": "return_percent",
            "descending": True,
        },
        "near_52_week_high": {
            "name": "Near 52-week high",
            "description": "Liquid stocks closing near their 52-week high.",
            "rules": {
                **liquidity_rule(settings),
                "within_percent_of_high": settings["near_high_percent"],
            },
            "sort_by": "percent_below_high",
            "descending": False,
        },
        "oversold": {
            "name": "Oversold",
            "description": "Liquid stocks whose RSI says they have fallen hard.",
            "rules": {**liquidity_rule(settings), "rsi_max": settings["oversold_rsi"]},
            "sort_by": "rsi",
            "descending": False,
        },
        "volume_breakout": {
            "name": "Volume breakout",
            "description": "Liquid stocks closing at a new high on unusually heavy volume.",
            "rules": {
                **liquidity_rule(settings),
                "min_volume_ratio": settings["volume_spike_multiple"],
                "volume_sessions": settings["volume_average_sessions"],
                "breakout_sessions": settings["breakout_sessions"],
            },
            "sort_by": "volume_ratio",
            "descending": True,
        },
    }


def preset_id(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("preset must be a preset name; see list_screen_presets")
    return "_".join(name.strip().lower().replace("-", " ").replace(",", " ").split())


def get_preset(name: Any, settings: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Find a preset by its id or its display name."""
    available = presets(settings)
    wanted = preset_id(name)
    by_name = {preset_id(p["name"]): key for key, p in available.items()}
    key = wanted if wanted in available else by_name.get(wanted)
    if key is None:
        raise ValueError(f"Unknown preset '{name}'. Available: {', '.join(available)}")
    return key, available[key]


# --- Market breadth ----------------------------------------------------------------


def market_breadth(
    symbols: tuple[str, ...],
    rows_by_symbol: dict[str, pd.DataFrame],
    ema_periods: list[int],
    year_sessions: int,
) -> dict[str, Any]:
    """How widely a move is shared across the stocks.

    Counts the stocks that rose, fell or did not change in the latest session,
    the share above each EMA (of the stocks with enough history for it) and
    the new 52-week highs against the new lows.
    """
    histories, skipped, latest = current_histories(symbols, rows_by_symbol)
    advancers = decliners = unchanged = no_previous = 0
    above = {period: {"above": 0, "measured": 0} for period in ema_periods}
    new_highs: list[str] = []
    new_lows: list[str] = []
    short_year: list[str] = []
    for symbol, history in histories.items():
        close = history["close"]
        if len(close) < 2:
            no_previous += 1
        else:
            change = float(close.iloc[-1]) - float(close.iloc[-2])
            if change > 0:
                advancers += 1
            elif change < 0:
                decliners += 1
            else:
                unchanged += 1
        for period in ema_periods:
            try:
                value = float(ema(close, period).iloc[-1])
            except InsufficientHistoryError:
                continue
            above[period]["measured"] += 1
            above[period]["above"] += float(close.iloc[-1]) > value
        try:
            found = year_range(history, year_sessions)
        except InsufficientHistoryError:
            continue
        if found.sessions < year_sessions:
            short_year.append(symbol)
        if found.is_new_high:
            new_highs.append(symbol)
        if found.is_new_low:
            new_lows.append(symbol)

    def share(count: int, of: int) -> float | None:
        return None if not of else rounded(count / of * 100, PERCENT_DECIMALS)

    moved = advancers + decliners + unchanged
    return {
        "checked": len(histories),
        "advance_decline": {
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "advancers_percent": share(advancers, moved),
            "decliners_percent": share(decliners, moved),
            # Above 1 means more stocks rose than fell.
            "ratio": None if not decliners else rounded(advancers / decliners, 2),
        },
        "above_ema": [
            {
                "period": period,
                "above": counts["above"],
                "measured": counts["measured"],
                "percent": share(counts["above"], counts["measured"]),
            }
            for period, counts in above.items()
        ],
        "year_extremes": {
            "new_highs": len(new_highs),
            "new_lows": len(new_lows),
            "new_high_symbols": new_highs,
            "new_low_symbols": new_lows,
        },
        "short_year_symbols": short_year,
        "skipped": skipped,
        "as_of": None if latest is None else day_text(latest),
    }
