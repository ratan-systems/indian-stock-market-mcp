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


def validate_columns(data_path: Path) -> list[str]:

    if data_path.suffix.lower() == ".parquet":
        available_columns = set(pq.ParquetFile(data_path).schema.names)
        missing_columns = set(REQUIRED_PRICE_COLUMNS) - available_columns

        if missing_columns:
            raise ValueError(f"Missing required column(s): {sorted(missing_columns)}")
    elif data_path.suffix.lower() == ".csv":
        available_columns = set(pd.read_csv(data_path, nrows=0).columns.to_list())
        missing_columns = set(REQUIRED_PRICE_COLUMNS) - available_columns

        if missing_columns:
            raise ValueError(f"Missing required column(s): {sorted(missing_columns)}")
    else:
        raise ValueError(" Unsupported data format. Expected .parquet and .csv")
    return [column for column in OPTIONAL_PRICE_COLUMNS if column in available_columns]


def validate_price_data(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        raise ValueError("Configured market-data file contains no records")
    validated_data = data.copy()
    validated_data["symbol"] = (
        validated_data["symbol"].astype("string").str.strip().str.upper()
    )
    if validated_data["symbol"].isna().any() or validated_data["symbol"].eq("").any():
        raise ValueError("Market data contains missing or empty symbol values")
    validated_data["date"] = pd.to_datetime(validated_data["date"], errors="coerce")
    if validated_data["date"].isna().any():
        raise ValueError("Market data contains missing or invalid date values")
    if validated_data.duplicated(subset=["symbol", "date"]).any():
        raise ValueError("Market data contains duplicate symbol-date records")

    price_columns = ["open", "high", "low", "close"]
    numeric_prices = validated_data[price_columns].apply(pd.to_numeric, errors="coerce")
    if numeric_prices.isna().any().any():
        raise ValueError("Market data contains missing or non-numeric OHLC prices")
    if not np.isfinite(numeric_prices.to_numpy(dtype="float64")).all():
        raise ValueError("Market data contains non-finite OHLC prices")
    if (numeric_prices <= 0).any().any():
        raise ValueError("Market data contains non-positive OHLC prices")
    validated_data[price_columns] = numeric_prices

    inconsistent_ohlc = (
        (numeric_prices["high"] < numeric_prices["low"])
        | (numeric_prices["high"] < numeric_prices["open"])
        | (numeric_prices["high"] < numeric_prices["close"])
        | (numeric_prices["low"] > numeric_prices["open"])
        | (numeric_prices["low"] > numeric_prices["close"])
    )
    if inconsistent_ohlc.any():
        raise ValueError("Market data contains inconsistent OHLC relationships")

    if "volume" in validated_data.columns:
        present_volume = validated_data["volume"].notna()
        if present_volume.any():
            numeric_volume = pd.to_numeric(
                validated_data.loc[present_volume, "volume"], errors="coerce"
            )
            if numeric_volume.isna().any():
                raise ValueError("Market data contains non-numeric volume values")
            if not np.isfinite(numeric_volume.to_numpy(dtype="float64")).all():
                raise ValueError("Market data contains non-finite volume values")
            if (numeric_volume < 0).any():
                raise ValueError("Market data contains negative volume values")
            validated_data.loc[present_volume, "volume"] = numeric_volume

    return validated_data


def load_full_dataset(data_path: Path) -> pd.DataFrame:
    available_optional_columns = validate_columns(data_path)
    columns = [*REQUIRED_PRICE_COLUMNS, *available_optional_columns]

    if data_path.suffix.lower() == ".parquet":
        raw_data = pd.read_parquet(data_path, engine="pyarrow", columns=columns)
    else:
        raw_data = pd.read_csv(data_path, usecols=columns)

    if "volume" not in raw_data.columns:
        raw_data["volume"] = pd.NA

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
    df = get_recent_price_history(symbol, sessions=5)

    if len(df) < 5:
        raise InsufficientSessionsError(
            "Not enough data: need 5 sessions to calculate weekly performance"
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
