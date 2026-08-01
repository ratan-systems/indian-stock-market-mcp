import json
import math
import os
from importlib.resources import files
from pathlib import Path

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

    if data_path.suffix.lower()==".parquet":
        available_columns = set(pq.ParquetFile(data_path).schema.names)
        missing_columns = set(REQUIRED_PRICE_COLUMNS) - available_columns

        if missing_columns:
            raise ValueError(
                f"Missing required column(s): {sorted(missing_columns)}"
            )
    elif data_path.suffix.lower()=='.csv':
        available_columns=set(pd.read_csv(data_path,nrows=0).columns.to_list())
        missing_columns=set(REQUIRED_PRICE_COLUMNS)-available_columns

        if missing_columns:
            raise ValueError(
                f"Missing required column(s): {sorted(missing_columns)}"
            )
    else:
        raise ValueError(" Unsupported data format. Expected .parquet and .csv")
    return [
        column
        for column in OPTIONAL_PRICE_COLUMNS
        if column in available_columns
    ]



def load_symbol_data(symbol: str) -> pd.DataFrame:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol must be a non-empty string")

    normalized_symbol = symbol.strip().upper()
    data_path = get_data_path()
    
    
    available_optional_columns = validate_columns(data_path)

    
    if data_path.suffix.lower()==".parquet":
        parquet_columns = [
            *REQUIRED_PRICE_COLUMNS,
            *available_optional_columns,
        ]
        symbol_data = pd.read_parquet(
            data_path,
            engine="pyarrow",
            columns=parquet_columns,
            filters=[("symbol", "==", normalized_symbol)],
        )

        if symbol_data.empty:
            stored_symbols = pd.read_parquet(
                data_path,
                engine="pyarrow",
                columns=["symbol"],
            )["symbol"]
            matching_symbols = stored_symbols[
                stored_symbols.astype("string").str.strip().str.upper()
                == normalized_symbol
            ].dropna().unique().tolist()

            symbol_data = pd.concat(
                [
                    pd.read_parquet(
                        data_path,
                        engine="pyarrow",
                        columns=parquet_columns,
                        filters=[("symbol", "==", stored_symbol)],
                    )
                    for stored_symbol in matching_symbols
                ],
                ignore_index=True,
            ) if matching_symbols else pd.DataFrame(columns=parquet_columns)

        if "volume" not in symbol_data.columns:
            symbol_data['volume']=pd.NA
        if symbol_data.empty:
            raise ValueError(f"Symbol '{normalized_symbol}' was not found")
    
    elif data_path.suffix.lower()=='.csv':
        df=pd.read_csv(data_path)
        df["symbol"]=df["symbol"].str.strip().str.upper()
        symbol_data=df[df["symbol"]==normalized_symbol].copy()
        if "volume" not in symbol_data.columns:
            symbol_data['volume']=pd.NA
        if symbol_data.empty:
            raise ValueError(f"Symbol '{normalized_symbol}' was not found")

    symbol_data["symbol"] = (
        symbol_data["symbol"].astype("string").str.strip().str.upper()
    )
    symbol_data['date']=pd.to_datetime(symbol_data['date'],errors='raise')
    if symbol_data['date'].isna().any():
        raise ValueError("Market data contains missing date values")
    if symbol_data['date'].duplicated().any():
        raise ValueError(
            f"Market data contains duplicate date values for '{normalized_symbol}'"
        )
    
    return symbol_data.sort_values("date").reset_index(drop=True)

def validate_ticker(symbol:str)->dict:
    normalized_symbol=symbol.strip().upper()
    try:
        load_symbol_data(normalized_symbol)
    except ValueError as error:
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

def get_recent_price_history(symbol:str,sessions:int=5)->pd.DataFrame:
    if not isinstance(sessions,int) or not 1<= sessions<=100:
        raise ValueError("session must be an integer between 1 and 100")
    df=load_symbol_data(symbol)
  
    df=df.tail(sessions)
    df=df.reset_index(drop=True)

    return df

def get_weekly_performance(symbol: str) -> dict:
    df = get_recent_price_history(symbol, sessions=5)

    if len(df) < 5:
        raise ValueError(
            "Not enough data: need 5 sessions to calculate weekly performance"
        )
    if df["close"].isna().any():
        raise ValueError("Cannot calculate return with missing close prices")

    start_row = df.iloc[0]
    end_row = df.iloc[-1]
    start_price = float(start_row["close"])
    end_price = float(end_row["close"])

    if start_price == 0:
        raise ValueError("Cannot calculate return when starting close price is zero")

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
        raise FileNotFoundError(f"Nifty 50 symbol file not found: {NIFTY50_PATH}")

    with NIFTY50_PATH.open() as file:
        nifty50_symbols=json.load(file)
    
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
        except ValueError as error:
            skipped.append({
                "symbol": symbol,
                "reason": str(error),
            })
            continue
        rankings.append(result)

    rankings.sort(key=lambda result: (-result['return_percent'],result['symbol']))
    rankings=rankings[0:top_n]

    return {
        "top_n":top_n,
        "rankings": rankings,
        "skipped": skipped,
    }

def get_available_universe() -> list[str]:
    """Return all normalized symbols in the configured market data."""
    data_path = get_data_path()
    validate_columns(data_path)

    if data_path.suffix.lower() == ".parquet":
        symbols = pd.read_parquet(
            data_path,
            engine="pyarrow",
            columns=["symbol"],
        )["symbol"]
    else:
        symbols = pd.read_csv(data_path, usecols=["symbol"])["symbol"]

    symbols = (
        symbols.astype("string")
        .str.strip()
        .str.upper()
        .dropna()
        .loc[lambda values: values.ne("")]
        .drop_duplicates()
        .sort_values()
        .tolist()
    )

    if not symbols:
        raise ValueError("No symbols found in the configured market-data file")

    return symbols
