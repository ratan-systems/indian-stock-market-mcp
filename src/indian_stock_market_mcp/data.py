import json
import math
import os
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from dotenv import load_dotenv

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

NIFTY50_PATH = files("indian_stock_market_mcp").joinpath("resources/nifty50.json")


class TickerNotFoundError(ValueError):
    """Raised when a readable dataset does not contain the
    requested ticker."""


class InsufficientSessionsError(ValueError):
    """Raised when a ticker has fewer than the sessions needed for a
    calculation."""


def get_data_path() -> Path:
    global _DOTENV_LOADED

    # Load local configuration only when market data is first requested.
    if not _DOTENV_LOADED:
        load_dotenv(PROJECT_ROOT / ".env")
        _DOTENV_LOADED = True

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
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol must be a non-empty string")

    normalized_symbol = symbol.strip().upper()
    data_path = get_data_path()
    full_data = load_full_dataset(data_path)

    symbol_data = full_data[full_data["symbol"] == normalized_symbol].copy()
    if symbol_data.empty:
        raise TickerNotFoundError(f"Symbol '{normalized_symbol}' was not found")

    return symbol_data.sort_values("date").reset_index(drop=True)


def validate_ticker(symbol: str) -> dict:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol must be a non-empty string")
    normalized_symbol = symbol.strip().upper()
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


def get_recent_price_history(symbol: str, sessions: int = 5) -> pd.DataFrame:
    if not isinstance(sessions, int) or not 1 <= sessions <= 100:
        raise ValueError("session must be an integer between 1 and 100")
    df = load_symbol_data(symbol)

    df = df.tail(sessions)
    df = df.reset_index(drop=True)

    return df


def get_weekly_performance(symbol: str) -> dict:
    df = get_recent_price_history(symbol, sessions=WEEKLY_SESSIONS)

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
        "symbol": symbol.strip().upper(),
        "start_date": start_row["date"].strftime("%Y-%m-%d"),
        "end_date": end_row["date"].strftime("%Y-%m-%d"),
        "start_close": float(start_price),
        "end_close": float(end_price),
        "return_percent": float(return_percentage),
        "session_count": len(df),
    }


def load_nifty50_symbols() -> list[str]:
    if not NIFTY50_PATH.is_file():
        raise FileNotFoundError(
            "Bundled Nifty 50 symbol file was not found. Reinstall the package"
        )

    with NIFTY50_PATH.open() as file:
        nifty50_symbols = json.load(file)

    if not isinstance(nifty50_symbols, list):
        raise TypeError("Nifty 50 symbol file must contain a JSON list")

    if not nifty50_symbols:
        raise ValueError("Nifty 50 symbol file cannot be empty")

    if not all(isinstance(symbol, str) for symbol in nifty50_symbols):
        raise ValueError("Nifty 50 symbol file must contain only strings")

    nifty50_symbols = [symbol.strip().upper() for symbol in nifty50_symbols]
    if not all(nifty50_symbols):
        raise ValueError("Nifty 50 symbol file cannot contain empty symbols")

    nifty50_symbols = list(dict.fromkeys(nifty50_symbols))
    return nifty50_symbols


def get_nifty50_universe() -> list[str]:
    """Return the normalized Nifty 50 symbol universe."""
    return load_nifty50_symbols()


def rank_weekly_performers(top_n: int = 5) -> dict:
    if not isinstance(top_n, int) or not 1 <= top_n <= 50:
        raise ValueError("top_n must be an integer between 1 and 50")
    nifty_symbols = load_nifty50_symbols()
    rankings = []
    skipped = []

    for symbol in nifty_symbols:
        try:
            result = get_weekly_performance(symbol)
        except (TickerNotFoundError, InsufficientSessionsError) as error:
            skipped.append(
                {
                    "symbol": symbol,
                    "reason": str(error),
                }
            )
            continue
        rankings.append(result)

    rankings.sort(key=lambda result: (-result["return_percent"], result["symbol"]))
    rankings = rankings[0:top_n]

    return {
        "top_n": top_n,
        "rankings": rankings,
        "skipped": skipped,
    }


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
    "healthy" otherwise. Price-adjustment status is always reported as an
    unknown warning because the data carries no adjustment metadata.
    """
    source = inspect_source()
    errors = list(source["errors"])
    warnings = [
        {
            "code": "price_adjustment_unknown",
            "message": (
                "Price-adjustment status for splits and dividends is unknown; "
                "verify it before relying on returns."
            ),
        }
    ]
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
    elif len(warnings) > 1:
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
        "capabilities": capabilities,
        "data_quality": data_quality,
        "errors": errors,
        "warnings": warnings,
    }
