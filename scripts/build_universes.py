"""Build the bundled index universes from NSE Indices constituent lists.

NSE Indices publishes each index's constituents as a CSV (for example
ind_nifty50list.csv on niftyindices.com) with a Symbol column. The files carry
no date, so each universe's "as of" date is the day you ran this script, which
should be the day you downloaded them.

Run with: python scripts/build_universes.py --input FOLDER [--as-of YYYY-MM-DD]
Writes src/indian_stock_market_mcp/resources/universes.json and prints the
size of every universe, plus a note for any that looks wrong.
"""

import argparse
import csv
import json
from datetime import UTC, date, datetime
from pathlib import Path

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "indian_stock_market_mcp"
    / "resources"
    / "universes.json"
)

SOURCE = "NSE Indices constituent lists (niftyindices.com)"

# Universe id -> (display name, NSE file name). Order is the order listed.
UNIVERSES = {
    "NIFTY_50": ("Nifty 50", "ind_nifty50list.csv"),
    "NIFTY_NEXT_50": ("Nifty Next 50", "ind_niftynext50list.csv"),
    "NIFTY_100": ("Nifty 100", "ind_nifty100list.csv"),
    "NIFTY_200": ("Nifty 200", "ind_nifty200list.csv"),
    "NIFTY_500": ("Nifty 500", "ind_nifty500list.csv"),
    "NIFTY_BANK": ("Nifty Bank", "ind_niftybanklist.csv"),
    "NIFTY_IT": ("Nifty IT", "ind_niftyitlist.csv"),
    "NIFTY_PHARMA": ("Nifty Pharma", "ind_niftypharmalist.csv"),
    "NIFTY_AUTO": ("Nifty Auto", "ind_niftyautolist.csv"),
    "NIFTY_FMCG": ("Nifty FMCG", "ind_niftyfmcglist.csv"),
    "NIFTY_METAL": ("Nifty Metal", "ind_niftymetallist.csv"),
    "NIFTY_ENERGY": ("Nifty Energy", "ind_niftyenergylist.csv"),
}

# Index names that state their size. A different count is reported as a note,
# not an error: NSE's own Nifty 500 file currently lists 501 names.
NOMINAL_SIZES = {
    "NIFTY_50": 50,
    "NIFTY_NEXT_50": 50,
    "NIFTY_100": 100,
    "NIFTY_200": 200,
    "NIFTY_500": 500,
}


class UniverseBuildError(ValueError):
    """Raised when an input file cannot produce a clean universe."""


def read_symbols(path: Path) -> list[str]:
    """Return the symbols in one NSE constituents CSV, in file order."""
    if not path.is_file():
        raise UniverseBuildError(f"{path.name} not found in the input folder")
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    if not rows or "Symbol" not in rows[0]:
        raise UniverseBuildError(f"{path.name} has no Symbol column")
    symbols = [row["Symbol"].strip().upper() for row in rows]
    if not all(symbols):
        raise UniverseBuildError(f"{path.name} has an empty symbol")
    if len(set(symbols)) != len(symbols):
        raise UniverseBuildError(f"{path.name} lists a symbol more than once")
    return symbols


def build(input_folder: Path, as_of: date) -> dict:
    universes = {}
    for universe_id, (name, file_name) in UNIVERSES.items():
        universes[universe_id] = {
            "name": name,
            "as_of": as_of.isoformat(),
            "symbols": read_symbols(input_folder / file_name),
        }
    return {"source": SOURCE, "universes": universes}


def check(universes: dict) -> list[str]:
    """Return notes about anything that looks unusual; never raises."""
    notes = []
    sets = {key: set(value["symbols"]) for key, value in universes.items()}
    for key, size in NOMINAL_SIZES.items():
        actual = len(sets[key])
        if actual != size:
            notes.append(f"{key} has {actual} symbols, its name says {size}")
    nested = [
        ("NIFTY_50", "NIFTY_100"),
        ("NIFTY_NEXT_50", "NIFTY_100"),
        ("NIFTY_100", "NIFTY_200"),
        ("NIFTY_200", "NIFTY_500"),
    ]
    for inner, outer in nested:
        if not sets[inner] <= sets[outer]:
            notes.append(f"{inner} is not contained in {outer}")
    return notes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--input", type=Path, required=True, help="folder of CSVs")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
        help="date the files were downloaded (default: today)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result = build(args.input, args.as_of)
    args.output.write_text(json.dumps(result, indent=2) + "\n")

    for universe_id, universe in result["universes"].items():
        print(f"{universe_id:15} {len(universe['symbols']):4} symbols")
    for note in check(result["universes"]):
        print(f"NOTE  {note}")
    print(
        f"Wrote {len(result['universes'])} universes as of {args.as_of} to {args.output}"
    )


if __name__ == "__main__":
    main()
