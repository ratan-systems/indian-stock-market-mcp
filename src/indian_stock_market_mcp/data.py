import math
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from dotenv import load_dotenv

from .adjustments import (
    UNKNOWN,
    AdjustmentInfo,
    adjustment_warnings,
    apply_adjustments,
    load_adjustment_info,
)
from .symbols import normalize_symbol
from .universes import DEFAULT_UNIVERSE, get_universe
from .watchlists import Selection, resolve_selection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_LOADED = False

REQUIRED_PRICE_COLUMNS = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
]

OPTIONAL_PRICE_COLUMNS = [
    "volume",
]

# Sessions needed for the weekly close-to-close return.
WEEKLY_SESSIONS = 5


class TickerNotFoundError(ValueError):
    """Raised when a readable dataset does not contain the
    requested ticker."""


class InsufficientSessionsError(ValueError):
    """Raised when a ticker has fewer than the sessions needed for a
    calculation."""


def load_local_configuration() -> None:
    """Load the project's .env file, once, the first time a setting is needed."""
    global _DOTENV_LOADED

    if not _DOTENV_LOADED:
        load_dotenv(PROJECT_ROOT / ".env")
        _DOTENV_LOADED = True


def get_data_path() -> Path:
    load_local_configuration()

    path_value = os.getenv("INDIAN_STOCK_DATA_PATH")

    if not path_value:
        raise ValueError("INDIAN_STOCK_DATA_PATH is not configured")

    data_path = Path(path_value).expanduser()

    if not data_path.is_file():
        raise FileNotFoundError(
            "Configured market-data file was not found. "
            "Check INDIAN_STOCK_DATA_PATH and provide an existing "
            ".parquet or .csv file."
        )

    return data_path


def read_column_names(data_path: Path) -> list[str]:
    """Read only the column names of a .parquet or .csv file."""
    suffix = data_path.suffix.lower()

    if suffix == ".parquet":
        return list(pq.ParquetFile(data_path).schema.names)
    if suffix == ".csv":
        return pd.read_csv(data_path, nrows=0).columns.to_list()
    raise ValueError(" Unsupported data format. Expected .parquet and .csv")


def validate_columns(data_path: Path) -> list[str]:
    available_columns = set(read_column_names(data_path))
    missing_columns = set(REQUIRED_PRICE_COLUMNS) - available_columns

    if missing_columns:
        raise ValueError(f"Missing required column(s): {sorted(missing_columns)}")
    return [column for column in OPTIONAL_PRICE_COLUMNS if column in available_columns]


def inspect_source() -> dict:
    """Describe the configured data file without raising.

    Reports whether the file is configured, present and readable, plus its
    format and columns. Only the file name is returned, never the full path.
    """
    report = {
        "configured": False,
        "available": False,
        "readable": False,
        "file_name": None,
        "format": None,
        "columns": [],
        "missing_required_columns": [],
        "optional_columns_present": [],
        "errors": [],
    }

    def add_error(code: str, message: str) -> dict:
        report["errors"].append({"code": code, "message": message})
        return report

    try:
        data_path = get_data_path()
    except ValueError:
        return add_error(
            "dataset_not_configured", "INDIAN_STOCK_DATA_PATH is not configured"
        )
    except FileNotFoundError:
        report["configured"] = True
        return add_error("file_not_found", "Configured market-data file was not found")

    report["configured"] = True
    report["available"] = True
    report["file_name"] = data_path.name

    suffix = data_path.suffix.lower()
    if suffix not in (".parquet", ".csv"):
        return add_error(
            "unsupported_format", "Unsupported data format. Expected .parquet or .csv"
        )
    report["format"] = suffix.lstrip(".")

    try:
        columns = read_column_names(data_path)
    except Exception as error:
        # Exception text can embed the full path, so report only its type.
        return add_error(
            "unreadable_file",
            f"Market-data file could not be read as {report['format']} "
            f"({type(error).__name__})",
        )

    report["readable"] = True
    report["columns"] = columns
    report["missing_required_columns"] = [
        column for column in REQUIRED_PRICE_COLUMNS if column not in columns
    ]
    report["optional_columns_present"] = [
        column for column in OPTIONAL_PRICE_COLUMNS if column in columns
    ]
    if report["missing_required_columns"]:
        add_error(
            "missing_required_columns",
            "Market data is missing required column(s): "
            f"{report['missing_required_columns']}",
        )
    return report


# Ordered from first-checked to last-checked. validate_price_data raises the
# message of the first issue with a non-zero count.
DATA_ISSUE_MESSAGES = {
    "empty_dataset": "Configured market-data file contains no records",
    "invalid_symbol": "Market data contains missing or empty symbol values",
    "invalid_date": "Market data contains missing or invalid date values",
    "duplicate_symbol_date": "Market data contains duplicate symbol-date records",
    "invalid_ohlc_price": "Market data contains missing or non-numeric OHLC prices",
    "non_finite_ohlc_price": "Market data contains non-finite OHLC prices",
    "non_positive_ohlc_price": "Market data contains non-positive OHLC prices",
    "inconsistent_ohlc": "Market data contains inconsistent OHLC relationships",
    "non_numeric_volume": "Market data contains non-numeric volume values",
    "non_finite_volume": "Market data contains non-finite volume values",
    "negative_volume": "Market data contains negative volume values",
}


def collect_data_issues(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Normalize price data and count every kind of problem found.

    Never raises for bad values. Returns the normalized frame and a mapping of
    each key in DATA_ISSUE_MESSAGES to the number of affected rows. The frame
    is only safe to use when every count is zero. Expects the required
    columns to exist.
    """
    issues = dict.fromkeys(DATA_ISSUE_MESSAGES, 0)
    normalized = data.copy()
    if normalized.empty:
        issues["empty_dataset"] = 1
        return normalized, issues

    normalized["symbol"] = normalized["symbol"].astype("string").str.strip().str.upper()
    invalid_symbol = normalized["symbol"].isna() | normalized["symbol"].eq("").fillna(
        False
    )
    issues["invalid_symbol"] = int(invalid_symbol.sum())

    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    invalid_date = normalized["date"].isna()
    issues["invalid_date"] = int(invalid_date.sum())

    # Duplicates are only meaningful for rows with a usable symbol and date.
    keyed = normalized.loc[~invalid_symbol & ~invalid_date]
    issues["duplicate_symbol_date"] = int(
        keyed.duplicated(subset=["symbol", "date"]).sum()
    )

    price_columns = ["open", "high", "low", "close"]
    prices = normalized[price_columns].apply(pd.to_numeric, errors="coerce")
    missing_price = prices.isna()
    issues["invalid_ohlc_price"] = int(missing_price.any(axis=1).sum())
    values = prices.to_numpy(dtype="float64")
    non_finite = ~np.isfinite(values) & ~missing_price.to_numpy()
    issues["non_finite_ohlc_price"] = int(non_finite.any(axis=1).sum())
    non_positive = (values <= 0) & np.isfinite(values)
    issues["non_positive_ohlc_price"] = int(non_positive.any(axis=1).sum())
    inconsistent_ohlc = (
        (prices["high"] < prices["low"])
        | (prices["high"] < prices["open"])
        | (prices["high"] < prices["close"])
        | (prices["low"] > prices["open"])
        | (prices["low"] > prices["close"])
    )
    issues["inconsistent_ohlc"] = int(inconsistent_ohlc.sum())
    normalized[price_columns] = prices

    if "volume" in normalized.columns:
        present_volume = normalized["volume"].notna()
        if present_volume.any():
            volume = pd.to_numeric(
                normalized.loc[present_volume, "volume"], errors="coerce"
            )
            issues["non_numeric_volume"] = int(volume.isna().sum())
            finite_volume = np.isfinite(volume.to_numpy(dtype="float64"))
            issues["non_finite_volume"] = int((~finite_volume & volume.notna()).sum())
            issues["negative_volume"] = int((volume < 0).sum())
            if not (
                issues["non_numeric_volume"]
                or issues["non_finite_volume"]
                or issues["negative_volume"]
            ):
                normalized.loc[present_volume, "volume"] = volume

    return normalized, issues


def validate_price_data(data: pd.DataFrame) -> pd.DataFrame:
    validated_data, issues = collect_data_issues(data)
    for issue, message in DATA_ISSUE_MESSAGES.items():
        if issues[issue]:
            raise ValueError(message)
    return validated_data


def read_raw_dataset(data_path: Path, optional_columns: list[str]) -> pd.DataFrame:
    """Read the price columns without validating any values."""
    columns = [*REQUIRED_PRICE_COLUMNS, *optional_columns]

    if data_path.suffix.lower() == ".parquet":
        raw_data = pd.read_parquet(data_path, engine="pyarrow", columns=columns)
    else:
        raw_data = pd.read_csv(data_path, usecols=columns)

    if "volume" not in raw_data.columns:
        raw_data["volume"] = pd.NA

    return raw_data


def load_full_dataset(data_path: Path) -> pd.DataFrame:
    available_optional_columns = validate_columns(data_path)
    raw_data = read_raw_dataset(data_path, available_optional_columns)

    return validate_price_data(raw_data)


def load_symbol_data(symbol: str) -> pd.DataFrame:
    normalized_symbol = normalize_symbol(symbol)
    data_path = get_data_path()
    full_data = load_full_dataset(data_path)

    symbol_data = full_data[full_data["symbol"] == normalized_symbol].copy()
    if symbol_data.empty:
        raise TickerNotFoundError(f"Symbol '{normalized_symbol}' was not found")

    return symbol_data.sort_values("date").reset_index(drop=True)


def get_adjustment_info() -> AdjustmentInfo:
    """What is known about the configured dataset's split/bonus adjustment."""
    return load_adjustment_info(get_data_path())


def load_symbol_history(symbol: str) -> pd.DataFrame:
    """A stock's full validated history, oldest first, adjusted for the splits
    and bonuses saved beside the dataset (if any).

    The one entry point the analysis tools read prices through. Only
    get_price_history with adjusted=False reads load_symbol_data directly, to
    show prices as the exchange published them.
    """
    history = load_symbol_data(symbol)
    return apply_adjustments(history, get_adjustment_info().events)


def validate_ticker(symbol: str) -> dict:
    normalized_symbol = normalize_symbol(symbol)
    try:
        load_symbol_data(normalized_symbol)
    except TickerNotFoundError as error:
        return {
            "valid": False,
            "ticker": normalized_symbol,
            "message": str(error),
        }
    return {
        "valid": True,
        "ticker": normalized_symbol,
        "message": "Ticker is available",
    }


def get_recent_price_history(
    symbol: str, sessions: int = 5, adjusted: bool = True
) -> pd.DataFrame:
    """The latest `sessions` rows, split/bonus adjusted unless `adjusted` is False."""
    if not isinstance(sessions, int) or not 1 <= sessions <= 100:
        raise ValueError("session must be an integer between 1 and 100")
    df = load_symbol_history(symbol) if adjusted else load_symbol_data(symbol)

    df = df.tail(sessions)
    df = df.reset_index(drop=True)

    return df


def weekly_performance_from_rows(symbol: str, rows: pd.DataFrame) -> dict:
    """Calculate the five-session return from one symbol's validated rows."""
    df = rows.sort_values("date").tail(WEEKLY_SESSIONS).reset_index(drop=True)

    if len(df) < WEEKLY_SESSIONS:
        raise InsufficientSessionsError(
            f"Not enough data: need {WEEKLY_SESSIONS} sessions to calculate weekly performance"
        )

    start_row = df.iloc[0]
    end_row = df.iloc[-1]
    start_price = float(start_row["close"])
    end_price = float(end_row["close"])

    return_percentage = ((end_price - start_price) / start_price) * 100

    if not math.isfinite(return_percentage):
        raise ValueError("Calculated return is not a finite number")

    return {
        "symbol": symbol,
        "start_date": start_row["date"].strftime("%Y-%m-%d"),
        "end_date": end_row["date"].strftime("%Y-%m-%d"),
        "start_close": float(start_price),
        "end_close": float(end_price),
        "return_percent": float(return_percentage),
        "session_count": len(df),
    }


def get_weekly_performance(symbol: str) -> dict:
    return weekly_performance_from_rows(
        normalize_symbol(symbol), load_symbol_history(symbol)
    )


def get_nifty50_universe() -> list[str]:
    """Return the normalized Nifty 50 symbol universe."""
    return list(get_universe("NIFTY_50").symbols)


def load_adjusted_dataset() -> pd.DataFrame:
    """The whole validated dataset with split and bonus adjustments applied."""
    data_path = get_data_path()
    return apply_adjustments(
        load_full_dataset(data_path), load_adjustment_info(data_path).events
    )


def load_selection_data(
    universe: str,
) -> tuple[Selection, pd.DataFrame, dict[str, pd.DataFrame]]:
    """Resolve a universe or watchlist name and load the adjusted dataset once.

    Returns the selection, the whole adjusted dataset and the same rows grouped
    by symbol, for tools that look at many stocks.
    """
    chosen = resolve_selection(universe)
    full_data = load_adjusted_dataset()
    return chosen, full_data, dict(tuple(full_data.groupby("symbol")))


def load_selection_rows(universe: str) -> tuple[Selection, dict[str, pd.DataFrame]]:
    """Like load_selection_data, without the whole dataset."""
    chosen, _, rows_by_symbol = load_selection_data(universe)
    return chosen, rows_by_symbol


def rank_weekly_performers(top_n: int = 5, universe: str = DEFAULT_UNIVERSE) -> dict:
    if not isinstance(top_n, int) or not 1 <= top_n <= 50:
        raise ValueError("top_n must be an integer between 1 and 50")
    chosen, rows_by_symbol = load_selection_rows(universe)
    rankings = []
    skipped = []

    for symbol in chosen.symbols:
        try:
            if symbol not in rows_by_symbol:
                raise TickerNotFoundError(f"Symbol '{symbol}' was not found")
            result = weekly_performance_from_rows(symbol, rows_by_symbol[symbol])
        except (TickerNotFoundError, InsufficientSessionsError) as error:
            skipped.append(
                {
                    "symbol": symbol,
                    "code": (
                        "symbol_not_found"
                        if isinstance(error, TickerNotFoundError)
                        else "short_history"
                    ),
                    "reason": str(error),
                }
            )
            continue
        rankings.append(result)

    rankings.sort(key=lambda result: (-result["return_percent"], result["symbol"]))
    rankings = rankings[0:top_n]

    return {
        "universe": chosen.id,
        "top_n": top_n,
        "rankings": rankings,
        "skipped": skipped,
    }


def dataset_source() -> dict:
    """Describe the configured dataset for a response envelope.

    Returns the file name, its format and the date the file was last changed,
    never the full path: answers go to an AI assistant, and a path can give
    away a user name or folder layout.
    """
    data_path = get_data_path()
    try:
        changed = datetime.fromtimestamp(data_path.stat().st_mtime, tz=UTC)
        modified = changed.astimezone().date().isoformat()
    except OSError:
        modified = None
    return {
        "kind": "dataset",
        "file_name": data_path.name,
        "format": data_path.suffix.lower().lstrip("."),
        "file_modified": modified,
    }


def dataset_as_of() -> str:
    """Return the latest market date in the configured dataset."""
    full_data = load_full_dataset(get_data_path())
    return full_data["date"].max().strftime("%Y-%m-%d")


def get_available_universe() -> list[str]:
    """Return all normalized symbols in the configured market data."""
    data_path = get_data_path()
    full_data = load_full_dataset(data_path)

    return sorted(full_data["symbol"].unique().tolist())


MAX_LISTED_THIN_SYMBOLS = 20


def diagnose_dataset() -> dict:
    """Build a JSON-safe health report for the configured dataset.

    Never raises. status is "invalid" when the data cannot be used,
    "incomplete" when it is usable but some analysis is unavailable, and
    "healthy" otherwise. Price-adjustment status comes from the adjustments
    file saved beside an imported dataset; any other dataset is reported with
    an unknown-adjustment warning because its data carries no such metadata.
    Those standing adjustment warnings do not make a dataset incomplete.
    """
    source = inspect_source()
    errors = list(source["errors"])
    adjustment_info = (
        get_adjustment_info() if source["available"] else AdjustmentInfo(UNKNOWN)
    )
    warnings = adjustment_warnings(adjustment_info, explain_unknown=True)
    standing_warnings = len(warnings)
    summary = {"row_count": None, "symbol_count": None, "date_range": None}
    capabilities = {
        "price_history": False,
        "weekly_return": False,
        "volume_analysis": False,
    }
    data_quality = {
        "issue_counts": dict.fromkeys(DATA_ISSUE_MESSAGES, 0),
        "symbols_with_insufficient_sessions": {"count": 0, "symbols": []},
    }

    if source["readable"] and not source["missing_required_columns"]:
        try:
            data_path = get_data_path()
            raw_data = read_raw_dataset(data_path, source["optional_columns_present"])
            data, issues = collect_data_issues(raw_data)
        except Exception as error:
            errors.append(
                {
                    "code": "data_load_failed",
                    "message": f"Market data could not be loaded ({type(error).__name__})",
                }
            )
        else:
            data_quality["issue_counts"] = issues
            for issue, message in DATA_ISSUE_MESSAGES.items():
                if issues[issue]:
                    # The empty-dataset flag is not a row count.
                    detail = (
                        "" if issue == "empty_dataset" else f" ({issues[issue]} row(s))"
                    )
                    errors.append({"code": issue, "message": f"{message}{detail}"})

            usable = data.dropna(subset=["symbol", "date"])
            usable = usable[usable["symbol"] != ""]
            sessions = usable.groupby("symbol")["date"].nunique()
            thin = sorted(sessions[sessions < WEEKLY_SESSIONS].index.tolist())
            data_quality["symbols_with_insufficient_sessions"] = {
                "count": len(thin),
                "symbols": thin[:MAX_LISTED_THIN_SYMBOLS],
            }
            summary = {
                "row_count": len(data),
                "symbol_count": int(sessions.size),
                "date_range": (
                    {
                        "start": usable["date"].min().strftime("%Y-%m-%d"),
                        "end": usable["date"].max().strftime("%Y-%m-%d"),
                    }
                    if not usable.empty
                    else None
                ),
            }

            # Volume and session warnings add nothing for an empty file.
            if not data.empty:
                has_volume = "volume" in source["optional_columns_present"]
                if not has_volume:
                    warnings.append(
                        {
                            "code": "volume_column_missing",
                            "message": "No volume column; volume-based analysis is unavailable.",
                        }
                    )
                elif data["volume"].notna().any():
                    capabilities["volume_analysis"] = True
                else:
                    warnings.append(
                        {
                            "code": "volume_all_missing",
                            "message": "Volume column has no values; volume-based analysis is unavailable.",
                        }
                    )
                if thin:
                    warnings.append(
                        {
                            "code": "insufficient_sessions",
                            "message": (
                                f"{len(thin)} symbol(s) have fewer than "
                                f"{WEEKLY_SESSIONS} sessions and are excluded "
                                "from weekly return calculations."
                            ),
                        }
                    )
            capabilities["price_history"] = not errors
            capabilities["weekly_return"] = not errors and bool(
                (sessions >= WEEKLY_SESSIONS).any()
            )

    if errors:
        status = "invalid"
        capabilities = dict.fromkeys(capabilities, False)
    elif len(warnings) > standing_warnings:
        status = "incomplete"
    else:
        status = "healthy"

    return {
        "status": status,
        "source": {
            key: source[key]
            for key in ("configured", "available", "readable", "file_name", "format")
        },
        "schema": {
            "columns": source["columns"],
            "required_columns": REQUIRED_PRICE_COLUMNS,
            "missing_required_columns": source["missing_required_columns"],
            "optional_columns_present": source["optional_columns_present"],
        },
        "summary": summary,
        "price_adjustment": {
            "status": adjustment_info.status,
            "adjusted_events": len(adjustment_info.events),
            "unverified_events": len(adjustment_info.unverified),
        },
        "capabilities": capabilities,
        "data_quality": data_quality,
        "errors": errors,
        "warnings": warnings,
    }
