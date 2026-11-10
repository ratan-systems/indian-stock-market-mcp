"""Read NSE and BSE daily bhavcopy files into the project's standard columns.

Both exchanges publish the same common layout (the "UDiFF" format, file names
like BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip). Only the series filter and
the source of the symbol differ between them.
"""

import io
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .data import DATA_ISSUE_MESSAGES, collect_data_issues
from .symbols import load_bse_codes

STANDARD_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]

# Bhavcopy column -> standard column.
SOURCE_COLUMNS = {
    "TradDt": "date",
    "TckrSymb": "symbol",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "TtlTradgVol": "volume",
}
REQUIRED_SOURCE_COLUMNS = ["Src", "FinInstrmId", "SctySrs", *SOURCE_COLUMNS]

# The series (NSE) or group (BSE) values that count as ordinary equity. Rows in
# any other series, such as government securities, bonds, SME listings or
# trusts, are dropped and counted. BSE group Z (companies with compliance
# problems) is left out on purpose. ETFs are not filtered out: they trade in
# the same series as company shares (NSE EQ, BSE B), so they are kept.
EQUITY_SERIES = {
    "NSE": frozenset({"EQ", "BE"}),
    "BSE": frozenset({"A", "B", "T", "X", "XT"}),
}


class BhavcopyError(ValueError):
    """Raised when a file is not a readable bhavcopy in the supported layout."""


@dataclass(frozen=True)
class BhavcopyResult:
    """The rows kept from one bhavcopy file, plus what was dropped."""

    exchange: str
    data: pd.DataFrame
    rows_read: int
    rows_dropped: int
    unmapped_bse_rows: int


def read_raw_bhavcopy(path: Path) -> pd.DataFrame:
    """Read a bhavcopy .csv, or the single .csv inside a .zip, as text."""
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                if len(members) != 1:
                    raise BhavcopyError(
                        f"{path.name} must contain exactly one .csv file"
                    )
                content = io.BytesIO(archive.read(members[0]))
            return pd.read_csv(content, dtype=str, encoding="utf-8-sig")
        return pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    except (zipfile.BadZipFile, pd.errors.ParserError, UnicodeDecodeError) as error:
        raise BhavcopyError(f"{path.name} could not be read ({type(error).__name__})")


def read_bhavcopy(path: Path) -> BhavcopyResult:
    """Read one bhavcopy file into the standard columns.

    The exchange comes from the file's Src column, so a file cannot be
    mislabelled by its name. Raises BhavcopyError for an unsupported layout, a
    file with more than one exchange or date, or an unknown exchange.
    """
    raw = read_raw_bhavcopy(path)
    missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in raw.columns]
    if missing:
        raise BhavcopyError(
            f"{path.name} is not a supported bhavcopy: missing {missing}"
        )
    if raw.empty:
        raise BhavcopyError(f"{path.name} contains no rows")

    exchanges = set(raw["Src"].str.strip().str.upper())
    if len(exchanges) != 1 or not exchanges <= EQUITY_SERIES.keys():
        raise BhavcopyError(
            f"{path.name} has unsupported exchange value(s): {sorted(exchanges)}"
        )
    (exchange,) = exchanges
    if raw["TradDt"].nunique() != 1:
        raise BhavcopyError(f"{path.name} must hold a single trading date")

    is_equity = raw["SctySrs"].str.strip().str.upper().isin(EQUITY_SERIES[exchange])
    kept = raw.loc[is_equity].copy()

    unmapped = 0
    if exchange == "BSE":
        # Nifty 50 stocks take their NSE symbol; the rest keep BSE's own ticker.
        mapped = kept["FinInstrmId"].str.strip().map(load_bse_codes())
        unmapped = int(mapped.isna().sum())
        kept["TckrSymb"] = mapped.fillna(kept["TckrSymb"])

    kept = kept.rename(columns=SOURCE_COLUMNS)[STANDARD_COLUMNS]
    kept["symbol"] = kept["symbol"].str.strip().str.upper()
    try:
        kept["date"] = pd.to_datetime(kept["date"])
        for column in ("open", "high", "low", "close", "volume"):
            kept[column] = pd.to_numeric(kept[column])
    except (ValueError, TypeError) as error:
        raise BhavcopyError(
            f"{path.name} has a value that is not a valid date or number"
        ) from error

    return BhavcopyResult(
        exchange=exchange,
        data=kept.reset_index(drop=True),
        rows_read=len(raw),
        rows_dropped=len(raw) - len(kept),
        unmapped_bse_rows=unmapped,
    )


# --- Importing a folder of daily files ---------------------------------------

EXCHANGE_METADATA_KEY = b"indian_stock_market_mcp.exchange"
BHAVCOPY_SUFFIXES = (".csv", ".zip")


@dataclass(frozen=True)
class ImportSummary:
    """What one import run did."""

    exchange: str
    output: Path
    files_read: int
    files_skipped: int
    files_ignored: int
    rows_read: int
    rows_kept: int
    rows_dropped: int
    unmapped_bse_rows: int
    total_rows: int
    symbol_count: int
    first_date: pd.Timestamp
    last_date: pd.Timestamp


def find_bhavcopy_files(folder: Path) -> tuple[list[Path], int]:
    """Return the bhavcopy files directly in a folder, and how many others.

    Files count as bhavcopy when the name starts with BhavCopy_ and ends in
    .csv or .zip (any case). Sub-folders are not searched.
    """
    if not folder.is_dir():
        raise BhavcopyError(f"{folder} is not a folder")
    files, ignored = [], 0
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("bhavcopy_") and name.endswith(BHAVCOPY_SUFFIXES):
            files.append(path)
        else:
            ignored += 1
    return files, ignored


def read_existing_output(output: Path) -> tuple[pd.DataFrame, str]:
    """Read an earlier import's Parquet file and the exchange it holds."""
    metadata = pq.read_schema(output).metadata or {}
    exchange = metadata.get(EXCHANGE_METADATA_KEY, b"").decode()
    if exchange not in EQUITY_SERIES:
        raise BhavcopyError(
            f"{output.name} was not created by import-bhavcopy, so it will not be "
            "changed. Choose a different --output."
        )
    existing = pd.read_parquet(output, columns=STANDARD_COLUMNS)
    existing["date"] = pd.to_datetime(existing["date"])
    return existing, exchange


def write_output(data: pd.DataFrame, exchange: str, output: Path) -> None:
    """Write the Parquet file in one step so a failure cannot leave half a file."""
    table = pa.Table.from_pandas(data, preserve_index=False)
    table = table.replace_schema_metadata({EXCHANGE_METADATA_KEY: exchange.encode()})
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    os.close(handle)
    try:
        pq.write_table(table, temp_name)
        os.replace(temp_name, output)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def check_import_data(data: pd.DataFrame) -> None:
    """Refuse data the server could not load, naming the first problem."""
    _, issues = collect_data_issues(data)
    for issue, message in DATA_ISSUE_MESSAGES.items():
        if issues[issue]:
            raise BhavcopyError(f"{message} ({issues[issue]} row(s)); nothing written")


def import_bhavcopy_folder(folder: Path, output: Path) -> ImportSummary:
    """Merge a folder of daily bhavcopy files into one Parquet file.

    One output file holds one exchange. Dates already in the output are
    skipped, so re-running adds only new days. Nothing is written unless every
    file reads cleanly and the merged data passes the server's checks.
    """
    files, ignored = find_bhavcopy_files(folder)
    if not files:
        raise BhavcopyError(f"No bhavcopy files (BhavCopy_*.csv or .zip) in {folder}")

    existing, exchange = None, None
    if output.exists():
        existing, exchange = read_existing_output(output)
    known_dates = set() if existing is None else set(existing["date"])

    new_frames, skipped = [], 0
    rows_read = rows_dropped = unmapped = 0
    for path in files:
        result = read_bhavcopy(path)
        if exchange is None:
            exchange = result.exchange
        elif result.exchange != exchange:
            raise BhavcopyError(
                f"{path.name} is {result.exchange} data but this import is "
                f"{exchange}. Import one exchange per output file."
            )
        trade_date = result.data["date"].iloc[0]
        if trade_date in known_dates:
            skipped += 1
            continue
        known_dates.add(trade_date)
        new_frames.append(result.data)
        rows_read += result.rows_read
        rows_dropped += result.rows_dropped
        unmapped += result.unmapped_bse_rows

    if new_frames:
        frames = ([existing] if existing is not None else []) + new_frames
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.sort_values(["date", "symbol"]).reset_index(drop=True)
        check_import_data(merged)
        write_output(merged, exchange, output)
    else:
        merged = existing

    return ImportSummary(
        exchange=exchange,
        output=output,
        files_read=len(new_frames),
        files_skipped=skipped,
        files_ignored=ignored,
        rows_read=rows_read,
        rows_kept=sum(len(frame) for frame in new_frames),
        rows_dropped=rows_dropped,
        unmapped_bse_rows=unmapped,
        total_rows=len(merged),
        symbol_count=int(merged["symbol"].nunique()),
        first_date=merged["date"].min(),
        last_date=merged["date"].max(),
    )
