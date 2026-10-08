"""Generate the synthetic sample dataset in data/sample_equity_daily.csv.

The prices are made up. They come from a seeded pseudo-random walk, so the
output is identical on every run and every machine. No real market data is
used. Ticker symbols are real only so the sample works with the bundled
Nifty 50 universe; the prices attached to them are fictional.

Usage:
    python scripts/generate_sample_data.py            # rewrite the sample file
    python scripts/generate_sample_data.py --check    # fail if it is out of date
"""

import argparse
import csv
import io
import random
import sys
from datetime import date, timedelta
from pathlib import Path

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1] / "data" / "sample_equity_daily.csv"
)

SEED = 20260727
START_DATE = date(2026, 7, 27)  # a Monday; five consecutive weekdays follow
SESSIONS = 5
STARTING_PRICES = {"INFY": 1500.0, "RELIANCE": 1000.0, "TCS": 2000.0}
COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]


def generate_rows() -> list[list[str]]:
    """Return the sample rows, ordered by date and then symbol."""
    rng = random.Random(SEED)
    rows = []
    for symbol, starting_price in sorted(STARTING_PRICES.items()):
        close = starting_price
        for offset in range(SESSIONS):
            open_price = round(close, 2)
            close = round(open_price * (1 + rng.uniform(-0.03, 0.03)), 2)
            high = round(max(open_price, close) * (1 + rng.uniform(0, 0.01)), 2)
            low = round(min(open_price, close) * (1 - rng.uniform(0, 0.01)), 2)
            volume = int(rng.uniform(1_000_000, 10_000_000))
            rows.append(
                [
                    (START_DATE + timedelta(days=offset)).isoformat(),
                    symbol,
                    f"{open_price:.2f}",
                    f"{high:.2f}",
                    f"{low:.2f}",
                    f"{close:.2f}",
                    str(volume),
                ]
            )
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def render_csv() -> str:
    """Return the full CSV text for the sample dataset."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(generate_rows())
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the output file differs from the generated data",
    )
    arguments = parser.parse_args()

    expected = render_csv()
    if arguments.check:
        current = arguments.output.read_text() if arguments.output.exists() else None
        if current != expected:
            print(f"{arguments.output} is out of date; rerun without --check")
            return 1
        print(f"{arguments.output} is up to date")
        return 0

    arguments.output.write_text(expected)
    print(f"Wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
