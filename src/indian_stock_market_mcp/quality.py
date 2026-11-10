"""Dataset quality report: problems that make prices unreliable for research.

The report never raises for bad values; it counts and lists them. Each finding
is either blocking (it makes the "backtest ready" verdict fail) or a note.
"""

import numpy as np
import pandas as pd

from .data import (
    collect_data_issues,
    get_data_path,
    read_raw_dataset,
    validate_columns,
)

DEFAULT_JUMP_THRESHOLD_PERCENT = 25.0
DEFAULT_MIN_HISTORY_SESSIONS = 5
DEFAULT_STALE_AFTER_SESSIONS = 10

MAX_LISTED = 20
MAX_LISTED_DATES = 10
MAX_LISTED_SYMBOLS = 50

# A date counts as a trading day when at least this share of a typical day's
# rows is present, so holidays and partial files do not set the calendar.
CALENDAR_SHARE = 0.5

PRICE_COLUMNS = ["open", "high", "low", "close"]


def validate_settings(
    jump_threshold_percent: float,
    min_history_sessions: int,
    stale_after_sessions: int,
) -> None:
    is_number = isinstance(jump_threshold_percent, int | float) and not isinstance(
        jump_threshold_percent, bool
    )
    if not is_number or not 0 < jump_threshold_percent <= 1000:
        raise ValueError("jump_threshold_percent must be a number above 0 up to 1000")
    for name, value in (
        ("min_history_sessions", min_history_sessions),
        ("stale_after_sessions", stale_after_sessions),
    ):
        valid = isinstance(value, int) and not isinstance(value, bool)
        if not valid or not 1 <= value <= 100000:
            raise ValueError(f"{name} must be an integer between 1 and 100000")


def date_text(value: pd.Timestamp) -> str:
    return value.strftime("%Y-%m-%d")


def make_finding(
    code: str,
    message: str,
    *,
    blocking: bool,
    count: int,
    symbols: list[str] | None = None,
) -> dict:
    finding = {
        "code": code,
        "blocking": blocking,
        "count": int(count),
        "message": message,
    }
    if symbols:
        finding["symbols"] = symbols[:MAX_LISTED]
    return finding


def find_impossible_prices(usable: pd.DataFrame) -> pd.Series:
    """Flag rows whose prices cannot be real: missing, non-positive or inconsistent."""
    prices = usable[PRICE_COLUMNS]
    open_, high, low, close = (prices[column] for column in PRICE_COLUMNS)
    finite = pd.Series(
        np.isfinite(prices.to_numpy(dtype="float64")).all(axis=1), index=prices.index
    )
    return (
        ~finite
        | (prices <= 0).any(axis=1)
        | (high < low)
        | (high < open_)
        | (high < close)
        | (low > open_)
        | (low > close)
    )


def build_quality_report(
    frame: pd.DataFrame,
    *,
    jump_threshold_percent: float = DEFAULT_JUMP_THRESHOLD_PERCENT,
    min_history_sessions: int = DEFAULT_MIN_HISTORY_SESSIONS,
    stale_after_sessions: int = DEFAULT_STALE_AFTER_SESSIONS,
) -> dict:
    """Check price data for gaps, duplicates, impossible prices and jumps.

    Rows with an invalid symbol or date and duplicate rows (after the first)
    are reported and left out of the other checks. Rows with impossible prices
    still count as present for gaps and history length, but are left out of
    the price-move check.
    """
    validate_settings(
        jump_threshold_percent, min_history_sessions, stale_after_sessions
    )
    normalized, issues = collect_data_issues(frame)

    findings: list[dict] = []
    report = {
        "backtest_ready": False,
        "settings": {
            "jump_threshold_percent": jump_threshold_percent,
            "min_history_sessions": min_history_sessions,
            "stale_after_sessions": stale_after_sessions,
        },
        "summary": {
            "row_count": len(normalized),
            "rows_checked": 0,
            "symbol_count": 0,
            "trading_days": 0,
            "date_range": None,
        },
        "checks": {},
        "history": None,
        "findings": findings,
    }

    def no_usable_data(message: str) -> dict:
        findings.append(make_finding("no_usable_data", message, blocking=True, count=0))
        return report

    if issues["empty_dataset"]:
        return no_usable_data("The dataset contains no rows.")

    symbol = normalized["symbol"].astype("string")
    has_symbol = (symbol.notna() & symbol.ne("")).fillna(False).astype(bool)
    usable = normalized.loc[has_symbol & normalized["date"].notna()].copy()
    usable["symbol"] = usable["symbol"].astype(str)
    invalid_rows = len(normalized) - len(usable)
    if invalid_rows:
        findings.append(
            make_finding(
                "invalid_rows",
                f"{invalid_rows} row(s) have a missing symbol or date and were left out.",
                blocking=True,
                count=invalid_rows,
            )
        )
    if usable.empty:
        return no_usable_data("No row has both a symbol and a valid date.")

    # Duplicates: every row after the first for the same symbol and date.
    duplicate = usable.duplicated(["symbol", "date"], keep="first")
    duplicates = usable.loc[duplicate].sort_values(["symbol", "date"])
    report["checks"]["duplicate_rows"] = {
        "count": len(duplicates),
        "symbols_affected": int(duplicates["symbol"].nunique()),
        "examples": [
            {"symbol": row.symbol, "date": date_text(row.date)}
            for row in duplicates.drop_duplicates(["symbol", "date"])
            .head(MAX_LISTED)
            .itertuples()
        ],
    }
    if len(duplicates):
        findings.append(
            make_finding(
                "duplicate_rows",
                f"{len(duplicates)} duplicate row(s) for the same symbol and date.",
                blocking=True,
                count=len(duplicates),
                symbols=sorted(duplicates["symbol"].unique()),
            )
        )

    # Impossible prices.
    impossible = find_impossible_prices(usable) & ~duplicate
    bad_rows = usable.loc[impossible].sort_values(["symbol", "date"])
    report["checks"]["impossible_prices"] = {
        "count": len(bad_rows),
        "symbols_affected": int(bad_rows["symbol"].nunique()),
        "examples": [
            {
                "symbol": row.symbol,
                "date": date_text(row.date),
                "open": None if pd.isna(row.open) else float(row.open),
                "high": None if pd.isna(row.high) else float(row.high),
                "low": None if pd.isna(row.low) else float(row.low),
                "close": None if pd.isna(row.close) else float(row.close),
            }
            for row in bad_rows.head(MAX_LISTED).itertuples()
        ],
    }
    if len(bad_rows):
        findings.append(
            make_finding(
                "impossible_prices",
                f"{len(bad_rows)} row(s) have prices that cannot be real "
                "(missing, zero or negative, or high/low inconsistent).",
                blocking=True,
                count=len(bad_rows),
                symbols=sorted(bad_rows["symbol"].unique()),
            )
        )

    if impossible.all():
        return no_usable_data("Every row has impossible prices.")
    clean = usable.loc[~duplicate].copy()
    report["summary"]["rows_checked"] = len(clean)

    # Trading calendar: dates with a typical share of the usual daily rows.
    daily_rows = clean.groupby("date").size()
    calendar = daily_rows.index[daily_rows >= CALENDAR_SHARE * daily_rows.median()]
    position = pd.Series(np.arange(len(calendar)), index=calendar)
    clean["idx"] = clean["date"].map(position)
    on_calendar = clean.dropna(subset=["idx"]).astype({"idx": int})

    per_symbol = clean.groupby("symbol").agg(
        sessions=("date", "size"), first=("date", "min"), last=("date", "max")
    )
    report["summary"].update(
        symbol_count=len(per_symbol),
        trading_days=len(calendar),
        date_range={
            "start": date_text(clean["date"].min()),
            "end": date_text(clean["date"].max()),
        },
    )

    # History length for each symbol (shortest first).
    ordered = per_symbol.sort_values(["sessions"], kind="stable")
    report["history"] = {
        "min_sessions": int(per_symbol["sessions"].min()),
        "median_sessions": float(per_symbol["sessions"].median()),
        "max_sessions": int(per_symbol["sessions"].max()),
        "listed": len(ordered.head(MAX_LISTED_SYMBOLS)),
        "symbols": [
            {
                "symbol": name,
                "sessions": int(row.sessions),
                "first_date": date_text(row.first),
                "last_date": date_text(row.last),
            }
            for name, row in ordered.head(MAX_LISTED_SYMBOLS).iterrows()
        ],
    }
    short = per_symbol.loc[per_symbol["sessions"] < min_history_sessions]
    short_names = sorted(short.index)
    report["checks"]["short_history"] = {
        "count": len(short_names),
        "symbols": short_names[:MAX_LISTED],
    }
    if len(short_names) == len(per_symbol):
        findings.append(
            make_finding(
                "short_history",
                f"No symbol has {min_history_sessions} sessions of history.",
                blocking=True,
                count=len(short_names),
                symbols=short_names,
            )
        )
    elif short_names:
        findings.append(
            make_finding(
                "short_history",
                f"{len(short_names)} symbol(s) have fewer than "
                f"{min_history_sessions} sessions of history.",
                blocking=False,
                count=len(short_names),
                symbols=short_names,
            )
        )

    # Missing trading days, counted inside each symbol's own first-to-last span.
    span = on_calendar.groupby("symbol")["idx"].agg(["min", "max", "count"])
    span["missing"] = (span["max"] - span["min"] + 1) - span["count"]
    gaps = span.loc[span["missing"] > 0].sort_index()
    gaps = gaps.sort_values("missing", ascending=False, kind="stable")
    by_symbol = on_calendar.groupby("symbol")["idx"]
    gap_examples = []
    for name, row in gaps.head(MAX_LISTED).iterrows():
        present = set(by_symbol.get_group(name))
        missing_positions = sorted(set(range(row["min"], row["max"] + 1)) - present)
        gap_examples.append(
            {
                "symbol": name,
                "missing_days": int(row["missing"]),
                "dates": [
                    date_text(calendar[i]) for i in missing_positions[:MAX_LISTED_DATES]
                ],
            }
        )
    report["checks"]["missing_trading_days"] = {
        "symbols_affected": len(gaps),
        "total_missing_days": int(gaps["missing"].sum()),
        "examples": gap_examples,
        "calendar_note": (
            "Trading days are the dates most symbols have a row for; a day "
            "missing for every symbol cannot be detected."
        ),
    }
    if len(gaps):
        findings.append(
            make_finding(
                "missing_trading_days",
                f"{len(gaps)} symbol(s) are missing {int(gaps['missing'].sum())} "
                "trading day(s) between their first and last date.",
                blocking=False,
                count=len(gaps),
                symbols=list(gaps.index),
            )
        )

    # Stale symbols: no data for many trading days before the dataset ends.
    since_last = (len(calendar) - 1) - span["max"]
    stale = since_last.loc[since_last > stale_after_sessions].sort_index()
    stale = stale.sort_values(ascending=False, kind="stable")
    last_dates = per_symbol["last"]
    report["checks"]["stale_symbols"] = {
        "count": len(stale),
        "examples": [
            {
                "symbol": name,
                "last_date": date_text(last_dates[name]),
                "trading_days_since": int(days),
            }
            for name, days in stale.head(MAX_LISTED).items()
        ],
    }
    if len(stale):
        findings.append(
            make_finding(
                "stale_data",
                f"{len(stale)} symbol(s) have no data for more than "
                f"{stale_after_sessions} trading days before the dataset's last date.",
                blocking=False,
                count=len(stale),
                symbols=list(stale.index),
            )
        )

    # One-day moves too large to be real, between consecutive trading days only.
    movable = on_calendar.loc[~impossible.reindex(on_calendar.index)]
    ordered_rows = movable.sort_values(["symbol", "idx"])
    grouped = ordered_rows.groupby("symbol")
    consecutive = (ordered_rows["idx"] - grouped["idx"].shift(1)) == 1
    previous_close = grouped["close"].shift(1)
    change = (ordered_rows["close"] / previous_close - 1) * 100
    jumps = ordered_rows.loc[consecutive & (change.abs() > jump_threshold_percent)]
    jumps = jumps.assign(
        previous_close=previous_close.loc[jumps.index], change=change.loc[jumps.index]
    )
    jumps = jumps.reindex(
        jumps.assign(size=jumps["change"].abs())
        .sort_values(["size", "symbol", "date"], ascending=[False, True, True])
        .index
    )
    report["checks"]["large_moves"] = {
        "count": len(jumps),
        "symbols_affected": int(jumps["symbol"].nunique()),
        "examples": [
            {
                "symbol": row.symbol,
                "date": date_text(row.date),
                "previous_close": float(row.previous_close),
                "close": float(row.close),
                "change_percent": round(float(row.change), 2),
            }
            for row in jumps.head(MAX_LISTED).itertuples()
        ],
    }
    if len(jumps):
        findings.append(
            make_finding(
                "large_price_move",
                f"{len(jumps)} one-day move(s) beyond {jump_threshold_percent:g}% "
                "in a close; often an unadjusted split or bonus.",
                blocking=True,
                count=len(jumps),
                symbols=list(dict.fromkeys(jumps["symbol"])),
            )
        )

    report["backtest_ready"] = not any(f["blocking"] for f in findings)
    return report


def get_quality_report(
    *,
    jump_threshold_percent: float = DEFAULT_JUMP_THRESHOLD_PERCENT,
    min_history_sessions: int = DEFAULT_MIN_HISTORY_SESSIONS,
    stale_after_sessions: int = DEFAULT_STALE_AFTER_SESSIONS,
) -> dict:
    """Build the quality report for the configured dataset."""
    validate_settings(
        jump_threshold_percent, min_history_sessions, stale_after_sessions
    )
    data_path = get_data_path()
    raw = read_raw_dataset(data_path, validate_columns(data_path))
    return build_quality_report(
        raw,
        jump_threshold_percent=jump_threshold_percent,
        min_history_sessions=min_history_sessions,
        stale_after_sessions=stale_after_sessions,
    )
