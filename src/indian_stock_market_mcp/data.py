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

def get_recent_price_history(symbol:str,sessions:int=5)->pd.DataFrame:
    if not isinstance(sessions,int) or not 1<= sessions<=100:
        raise ValueError("session must be an integer between 1 and 100")
    df=load_symbol_data(symbol)
  
    df=df.tail(sessions)
    df=df.reset_index(drop=True)

    return df
