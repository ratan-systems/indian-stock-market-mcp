"""Check live that Yahoo still gives the right factors for two known events.

Run by hand (it needs the internet, so it is not part of the tests or CI):

    uv run python scripts/check_adjustments.py

It asks Yahoo for its split-adjusted closes around two corporate actions whose
factors are known, pairs them with the exchange's unadjusted closes from the
same days, and checks that the adjustment method gives the known factor.
Run it again whenever yfinance is upgraded or Yahoo changes how it serves data.
"""

import sys
from datetime import date, timedelta

from indian_stock_market_mcp.adjustments import (
    ADJUSTED,
    compute_factor,
    fetch_yahoo_closes,
    yahoo_symbol,
)

# symbol, day before, ex-date, exchange close before, exchange close on ex-date,
# expected factor. The exchange closes are NSE bhavcopy values as published.
KNOWN_EVENTS = [
    # Bajaj Finance: 4:1 bonus and 1:2 split together.
    ("BAJFINANCE", date(2025, 6, 13), date(2025, 6, 16), 9331.0, 938.0, 10.0),
    # HDFC Bank: 1:1 bonus.
    ("HDFCBANK", date(2025, 8, 25), date(2025, 8, 26), 1964.1, 973.4, 2.0),
]


def main() -> int:
    failures = 0
    for symbol, before, ex_date, raw_before, raw_after, expected in KNOWN_EVENTS:
        ticker = yahoo_symbol(symbol, "NSE")
        closes = fetch_yahoo_closes(
            ticker, before - timedelta(days=5), ex_date + timedelta(days=5)
        )
        yahoo_before, yahoo_after = closes.get(before), closes.get(ex_date)
        if yahoo_before is None or yahoo_after is None:
            print(f"FAIL  {symbol}: Yahoo has no price for {before} or {ex_date}")
            failures += 1
            continue
        status, factor = compute_factor(
            raw_before, raw_after, yahoo_before, yahoo_after
        )
        good = status == ADJUSTED and factor == expected
        failures += not good
        print(
            f"{'ok  ' if good else 'FAIL'}  {symbol} {ex_date}: expected {expected:g}, "
            f"got {factor} ({status}); Yahoo closes {yahoo_before} and {yahoo_after}"
        )
    print("All known events match." if not failures else f"{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
