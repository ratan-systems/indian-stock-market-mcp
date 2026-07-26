import json
import math
import os
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

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

NIFTY50_PATH = PROJECT_ROOT / "data" / "nifty50.json"


def get_data_path() -> Path:
    path_value = os.getenv("INDIAN_STOCK_DATA_PATH")

    if not path_value:
        raise ValueError("INDIAN_STOCK_DATA_PATH is not configured")

    data_path = Path(path_value).expanduser()

    if not data_path.is_file():
        raise FileNotFoundError(f"Parquet file not found: {data_path}")

    return data_path

def validate_columns(data_path: Path) -> list[str]:
    available_columns = set(pq.ParquetFile(data_path).schema.names)
    missing_columns = set(REQUIRED_PRICE_COLUMNS) - available_columns

    if missing_columns:
        raise ValueError(
            f"Missing required column(s): {sorted(missing_columns)}"
        )

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

    symbol_data = pd.read_parquet(
        data_path,
        engine="pyarrow",
        columns=[*REQUIRED_PRICE_COLUMNS, *available_optional_columns],
        filters=[("symbol", "==", normalized_symbol)],
    )
    if "volume" not in symbol_data.columns:
        symbol_data['volume']=pd.NA
    if symbol_data.empty:
        raise ValueError(f"Symbol '{normalized_symbol}' was not found")

    
    symbol_data['date']=pd.to_datetime(symbol_data['date'],errors='raise')
    if symbol_data['date'].isna().any():
        raise ValueError("Market data contains missing date values")
    
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

    rankings.sort(key=lambda result: result['return_percent'],reverse=True)
    rankings=rankings[0:top_n]

    return {
        "top_n":top_n,
        "rankings": rankings,
        "skipped": skipped,
    }
