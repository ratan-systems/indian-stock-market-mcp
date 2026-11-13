"""Generate the synthetic sample datasets in data/.

sample_equity_daily.csv is a five-session file for a first look;
sample_equity_year.csv has a year of sessions for twelve symbols, enough for
the indicator, screen, comparison and breadth tools.

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


def to_csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def render_csv() -> str:
    """Return the full CSV text for the five-session sample dataset."""
    return to_csv(generate_rows())


YEAR_OUTPUT = DEFAULT_OUTPUT.with_name("sample_equity_year.csv")
YEAR_SEED = 20260731
YEAR_END = date(2026, 7, 31)  # the same last session as the five-session sample
YEAR_SESSIONS = 260
# Starting price and average daily drift (percent). The drifts differ so the
# rankings and screens have winners, losers and stocks in between.
YEAR_SYMBOLS = {
    "BHARTIARTL": (1400.0, 0.12),
    "HDFCBANK": (900.0, 0.03),
    "ICICIBANK": (1300.0, 0.06),
    "INFY": (1500.0, -0.05),
    "ITC": (420.0, 0.0),
    "LT": (3500.0, 0.08),
    "MARUTI": (12000.0, 0.10),
    "RELIANCE": (1300.0, 0.04),
    "SBIN": (800.0, 0.07),
    "SUNPHARMA": (1700.0, -0.02),
    "TATASTEEL": (150.0, -0.08),
    "TCS": (3400.0, -0.10),
}


BREAKOUT_SYMBOL = "SBIN"


def year_days() -> list[date]:
    """The last YEAR_SESSIONS weekdays up to YEAR_END, oldest first."""
    days, day = [], YEAR_END
    while len(days) < YEAR_SESSIONS:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


def generate_year_rows() -> list[list[str]]:
    """Return a year of rows for twelve symbols, ordered by date then symbol."""
    rng = random.Random(YEAR_SEED)
    days = year_days()
    rows = []
    for symbol, (starting_price, drift) in sorted(YEAR_SYMBOLS.items()):
        close = starting_price
        typical_volume = rng.uniform(1_000_000, 8_000_000)
        peak = 0.0  # the highest high so far
        for day in days:
            open_price = round(close * (1 + rng.uniform(-0.004, 0.004)), 2)
            close = round(close * (1 + drift / 100 + rng.uniform(-0.018, 0.018)), 2)
            # About one session in forty trades on unusually heavy volume.
            burst = rng.uniform(2.2, 4.0) if rng.random() < 0.025 else 1.0
            if symbol == BREAKOUT_SYMBOL and day == days[-1]:
                # A last session the breakout tools can find: a new high on
                # three times the usual volume.
                close = round(max(peak, close) * 1.03, 2)
                burst = 3.0
            high = round(max(open_price, close) * (1 + rng.uniform(0, 0.008)), 2)
            low = round(min(open_price, close) * (1 - rng.uniform(0, 0.008)), 2)
            peak = max(peak, high)
            volume = int(typical_volume * rng.uniform(0.6, 1.4) * burst)
            rows.append(
                [
                    day.isoformat(),
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


def render_year_csv() -> str:
    """Return the full CSV text for the one-year sample dataset."""
    return to_csv(generate_year_rows())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--year-output", type=Path, default=YEAR_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the output file differs from the generated data",
    )
    arguments = parser.parse_args()

    files = [
        (arguments.output, render_csv()),
        (arguments.year_output, render_year_csv()),
    ]
    if arguments.check:
        stale = [
            path
            for path, expected in files
            if not path.exists() or path.read_text() != expected
        ]
        for path in stale:
            print(f"{path} is out of date; rerun without --check")
        if not stale:
            print("The sample datasets are up to date")
        return 1 if stale else 0

    for path, expected in files:
        path.write_text(expected)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
