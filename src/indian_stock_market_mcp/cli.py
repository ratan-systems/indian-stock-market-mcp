"""Command-line entry point: start the server, or import bhavcopy files."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .bhavcopy import BhavcopyError, ImportSummary, import_bhavcopy_folder

DEFAULT_OUTPUT = Path("bhavcopy.parquet")


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
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        from . import server

        server.main()
        return

    parsed = build_parser().parse_args(args)
    try:
        summary = import_bhavcopy_folder(parsed.folder, parsed.output)
    except BhavcopyError as error:
        sys.exit(f"Error: {error}")
    print(format_summary(summary))


if __name__ == "__main__":
    main()
