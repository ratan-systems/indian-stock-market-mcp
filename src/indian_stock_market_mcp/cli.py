"""Command-line entry point: start the server, import bhavcopy files, or
check imported prices for splits and bonuses."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .adjustments import AdjustmentsError, RefreshSummary, refresh_adjustments
from .bhavcopy import BhavcopyError, ImportSummary, import_bhavcopy_folder
from .index_closes import (
    IndexCloseError,
    IndexImportSummary,
    import_index_folder,
)

DEFAULT_OUTPUT = Path("bhavcopy.parquet")
DEFAULT_INDEX_OUTPUT = Path("index_closes.parquet")
MAX_LISTED = 10


def format_refresh_summary(summary: RefreshSummary) -> str:
    """Return the plain-text report printed after a split/bonus check."""
    lines = [
        "Price adjustment (splits and bonuses checked against Yahoo):",
        (
            f"  Large falls (15%+) found:  {summary.candidates} "
            f"({summary.already_checked} settled by an earlier check)"
        ),
        f"  Adjusted:                  {len(summary.adjusted)}",
    ]
    for event in summary.adjusted[:MAX_LISTED]:
        lines.append(
            f"    {event.symbol}  {event.ex_date.isoformat()}  factor {event.factor:g}"
        )
    if len(summary.adjusted) > MAX_LISTED:
        lines.append(f"    ... and {len(summary.adjusted) - MAX_LISTED} more")
    lines.append(f"  Real falls, not adjusted:  {summary.no_adjustment}")
    lines.append(
        f"  Could not verify:          {len(summary.unverified)}"
        + (" (retried on the next refresh)" if summary.unverified else "")
    )
    for symbol, day, reason in summary.unverified[:MAX_LISTED]:
        lines.append(f"    {symbol}  {day.isoformat()}  {reason}")
    if len(summary.unverified) > MAX_LISTED:
        lines.append(f"    ... and {len(summary.unverified) - MAX_LISTED} more")
    return "\n".join(lines)


def run_refresh(dataset: Path, *, fatal: bool) -> None:
    """Check a dataset's price falls and print the result. An import keeps its
    data even if the check fails, so only the standalone command exits."""
    print("\nChecking large price falls against Yahoo (this can take a while)...")
    sys.stdout.flush()
    try:
        summary = refresh_adjustments(dataset)
    except AdjustmentsError as error:
        if fatal:
            sys.exit(f"Error: {error}")
        print(f"Price adjustment skipped: {error}")
        return
    print(format_refresh_summary(summary))


def format_summary(summary: ImportSummary) -> str:
    """Return the plain-text report printed after an import."""
    lines = [
        f"Exchange:        {summary.exchange}",
        f"Output file:     {summary.output}",
        f"Files read:      {summary.files_read}",
        f"Files skipped:   {summary.files_skipped} (date already imported)",
        f"Files ignored:   {summary.files_ignored} (not BhavCopy_*.csv or .zip)",
        f"Rows read:       {summary.rows_read}",
        f"Rows kept:       {summary.rows_kept}",
        f"Rows dropped:    {summary.rows_dropped} (not an equity series)",
    ]
    if summary.exchange == "BSE":
        lines.append(
            f"BSE own ticker:  {summary.unmapped_bse_rows} kept row(s) are outside "
            "the Nifty 50 code mapping"
        )
    lines += [
        f"Date range:      {summary.first_date:%Y-%m-%d} to {summary.last_date:%Y-%m-%d}",
        f"Symbols:         {summary.symbol_count}",
        f"Rows in output:  {summary.total_rows}",
    ]
    return "\n".join(lines)


def format_index_summary(summary: IndexImportSummary) -> str:
    """Return the plain-text report printed after an index-close import."""
    return "\n".join(
        [
            f"Output file:     {summary.output}",
            f"Files read:      {summary.files_read}",
            f"Files skipped:   {summary.files_skipped} (date already imported)",
            f"Files ignored:   {summary.files_ignored} (not ind_close_all_*.csv)",
            f"Indices:         {summary.index_count}",
            f"Date range:      {summary.first_date:%Y-%m-%d} to {summary.last_date:%Y-%m-%d}",
            f"Rows in output:  {summary.total_rows}",
            "",
            (
                "Point INDIAN_STOCK_INDEX_PATH at this file to use official index "
                "values as benchmarks."
            ),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="indian-stock-market-mcp",
        description="Run the MCP server (no arguments) or import market data.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser(
        "import-bhavcopy",
        help="merge a folder of daily NSE or BSE bhavcopy files into a Parquet file",
    )
    importer.add_argument("folder", type=Path, help="folder holding BhavCopy_* files")
    importer.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Parquet file to create or extend (default: %(default)s)",
    )
    importer.add_argument(
        "--no-adjust",
        action="store_true",
        help="skip the split and bonus check against Yahoo after importing",
    )
    refresher = commands.add_parser(
        "refresh-adjustments",
        help="check an imported file's large price falls for splits and bonuses",
    )
    refresher.add_argument(
        "dataset", type=Path, help="Parquet file from import-bhavcopy"
    )
    index_importer = commands.add_parser(
        "import-index-closes",
        help="merge NSE daily index-close files (ind_close_all_*.csv) into a Parquet file",
    )
    index_importer.add_argument(
        "folder", type=Path, help="folder holding ind_close_all_* files"
    )
    index_importer.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_INDEX_OUTPUT,
        help="Parquet file to create or extend (default: %(default)s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        from . import server

        server.main()
        return

    parsed = build_parser().parse_args(args)
    if parsed.command == "refresh-adjustments":
        run_refresh(parsed.dataset, fatal=True)
        return
    if parsed.command == "import-index-closes":
        try:
            index_summary = import_index_folder(parsed.folder, parsed.output)
        except IndexCloseError as error:
            sys.exit(f"Error: {error}")
        print(format_index_summary(index_summary))
        return

    try:
        summary = import_bhavcopy_folder(parsed.folder, parsed.output)
    except BhavcopyError as error:
        sys.exit(f"Error: {error}")
    print(format_summary(summary))
    if not parsed.no_adjust:
        run_refresh(parsed.output, fatal=False)


if __name__ == "__main__":
    main()
