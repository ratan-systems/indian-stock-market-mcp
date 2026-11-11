"""NSE index closing values: the official benchmark series.

NSE publishes one file a day with the closing value of every index it runs
(ind_close_all_DDMMYYYY.csv on niftyindices.com, about 140 rows). This module
reads those files, merges a folder of them into one Parquet file, and loads the
result so relative strength can be measured against the real index instead of
an average of its members.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .atomic import write_atomically
from .data import load_local_configuration

INDEX_PATH_ENV = "INDIAN_STOCK_INDEX_PATH"
KIND_METADATA_KEY = b"indian_stock_market_mcp.kind"
KIND = b"index_closes"
COLUMNS = ["date", "index", "open", "high", "low", "close"]
SOURCE_COLUMNS = {
    "Index Name": "index",
    "Index Date": "date",
    "Open Index Value": "open",
    "High Index Value": "high",
    "Low Index Value": "low",
    "Closing Index Value": "close",
}
FILE_PREFIX = "ind_close_all_"


class IndexCloseError(ValueError):
    """Raised when index closing values cannot be read or imported."""


def index_key(name: str) -> str:
    """A name reduced for matching: 'NIFTY Midcap 100' and 'Nifty Midcap  100'
    are the same index."""
    return " ".join(str(name).lower().split())


# --- Reading NSE's daily file ----------------------------------------------------


def read_index_close_file(path: Path) -> pd.DataFrame:
    """Read one day's file into date, index, open, high, low, close.

    NSE writes "-" for values an index does not have (such as the open of a
    dividend-points series). Rows without a usable close are dropped.
    """
    try:
        raw = pd.read_csv(path, dtype=str)
    except (OSError, ValueError, pd.errors.ParserError) as error:
        raise IndexCloseError(f"{path.name} could not be read ({error})") from error
    raw.columns = [column.strip() for column in raw.columns]
    missing = [column for column in SOURCE_COLUMNS if column not in raw.columns]
    if missing:
        raise IndexCloseError(
            f"{path.name} is not an NSE index-close file: missing {missing}"
        )
    frame = raw[list(SOURCE_COLUMNS)].rename(columns=SOURCE_COLUMNS)
    frame["index"] = frame["index"].str.strip()
    frame["date"] = pd.to_datetime(frame["date"], format="%d-%m-%Y", errors="coerce")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["date"].isna().any():
        raise IndexCloseError(f"{path.name} has a row with an unreadable date")
    if frame["date"].nunique() != 1:
        raise IndexCloseError(f"{path.name} holds more than one date")
    frame = frame[frame["close"].notna() & (frame["close"] > 0)]
    frame = frame[frame["index"] != ""]
    if frame.empty:
        raise IndexCloseError(f"{path.name} has no usable index closes")
    return frame[COLUMNS].reset_index(drop=True)


# --- Importing a folder ----------------------------------------------------------


@dataclass(frozen=True)
class IndexImportSummary:
    output: Path
    files_read: int
    files_skipped: int
    files_ignored: int
    total_rows: int
    index_count: int
    first_date: pd.Timestamp
    last_date: pd.Timestamp


def find_index_files(folder: Path) -> tuple[list[Path], int]:
    """The ind_close_all_*.csv files directly in a folder, and how many others."""
    if not folder.is_dir():
        raise IndexCloseError(f"{folder} is not a folder")
    files, ignored = [], 0
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith(FILE_PREFIX) and name.endswith(".csv"):
            files.append(path)
        else:
            ignored += 1
    return files, ignored


def read_existing_output(output: Path) -> pd.DataFrame:
    metadata = pq.read_schema(output).metadata or {}
    if metadata.get(KIND_METADATA_KEY) != KIND:
        raise IndexCloseError(
            f"{output.name} was not created by import-index-closes, so it will not "
            "be changed. Choose a different --output."
        )
    existing = pd.read_parquet(output)[COLUMNS]
    existing["date"] = pd.to_datetime(existing["date"])
    return existing


def write_output(data: pd.DataFrame, output: Path) -> None:
    table = pa.Table.from_pandas(data, preserve_index=False)
    table = table.replace_schema_metadata({KIND_METADATA_KEY: KIND})
    write_atomically(output, lambda temp: pq.write_table(table, temp))


def import_index_folder(folder: Path, output: Path) -> IndexImportSummary:
    """Merge a folder of daily index-close files into one Parquet file.

    Dates already in the output are skipped, so re-running adds only new days.
    Nothing is written unless every file reads cleanly.
    """
    files, ignored = find_index_files(folder)
    if not files:
        raise IndexCloseError(f"No ind_close_all_*.csv files in {folder}")

    existing = read_existing_output(output) if output.exists() else None
    known_dates = set() if existing is None else set(existing["date"])

    new_frames, skipped = [], 0
    for path in files:
        frame = read_index_close_file(path)
        day = frame["date"].iloc[0]
        if day in known_dates:
            skipped += 1
            continue
        known_dates.add(day)
        new_frames.append(frame)

    if new_frames:
        frames = ([existing] if existing is not None else []) + new_frames
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(["date", "index"], keep="last")
        merged = merged.sort_values(["index", "date"]).reset_index(drop=True)
        write_output(merged, output)
    else:
        merged = existing

    return IndexImportSummary(
        output=output,
        files_read=len(new_frames),
        files_skipped=skipped,
        files_ignored=ignored,
        total_rows=len(merged),
        index_count=int(merged["index"].nunique()),
        first_date=merged["date"].min(),
        last_date=merged["date"].max(),
    )


# --- Loading for the server ------------------------------------------------------


def get_index_path() -> Path | None:
    """The configured index-close file, or None when none is configured."""
    load_local_configuration()
    value = os.getenv(INDEX_PATH_ENV)
    return Path(value).expanduser() if value else None


def load_index_closes() -> dict[str, pd.Series] | None:
    """Closing values by index, keyed by matching name, each a series by date.

    Returns None when no index file is configured. Raises IndexCloseError when
    one is configured but cannot be used, so the caller can say why it fell
    back to an average of the index's members.
    """
    path = get_index_path()
    if path is None:
        return None
    if not path.is_file():
        raise IndexCloseError(f"The index file named by {INDEX_PATH_ENV} was not found")
    try:
        metadata = pq.read_schema(path).metadata or {}
        if metadata.get(KIND_METADATA_KEY) != KIND:
            raise IndexCloseError(
                f"The file named by {INDEX_PATH_ENV} was not created by "
                "import-index-closes"
            )
        frame = pd.read_parquet(path)[COLUMNS]
    except (OSError, ValueError, KeyError, pa.ArrowException) as error:
        if isinstance(error, IndexCloseError):
            raise
        raise IndexCloseError(
            f"The index file could not be read ({type(error).__name__})"
        ) from error
    frame["date"] = pd.to_datetime(frame["date"])
    return {
        index_key(name): rows.set_index("date")["close"].sort_index()
        for name, rows in frame.groupby("index")
    }
