"""Build the bundled BSE scrip-code -> NSE symbol mapping for the Nifty 50.

Matches NSE and BSE listings by ISIN, so a stock is linked by the identifier
both exchanges share rather than by name.

Inputs:
  --nse  NSE Nifty 50 constituents CSV (ind_nifty50list.csv from
         niftyindices.com), with Symbol and ISIN Code columns.
  --bse  BSE equity listing as JSON: either the full "List of Scrips" response
         (a list of objects with SCRIP_CD, scrip_id and ISIN_NUMBER) or a list
         of [scrip_code, scrip_id, name, isin] rows.

Run with: python scripts/build_bse_codes.py --nse FILE --bse FILE
Writes src/indian_stock_market_mcp/resources/bse_codes.json and reports every
Nifty 50 stock it could not match or whose BSE id differs from its NSE symbol.
"""

import argparse
import csv
import json
from pathlib import Path

OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "indian_stock_market_mcp"
    / "resources"
    / "bse_codes.json"
)


def read_nse(path: Path) -> dict[str, str]:
    """Return {ISIN: NSE symbol}."""
    with path.open(newline="") as file:
        return {
            row["ISIN Code"].strip(): row["Symbol"].strip().upper()
            for row in csv.DictReader(file)
        }


def read_bse(path: Path) -> dict[str, tuple[str, str]]:
    """Return {ISIN: (scrip code, scrip id)}."""
    listing = json.loads(path.read_text())
    result = {}
    for item in listing:
        if isinstance(item, dict):
            code, scrip_id, isin = (
                item["SCRIP_CD"],
                item["scrip_id"],
                item["ISIN_NUMBER"],
            )
        else:
            code, scrip_id, _, isin = item
        result[isin.strip()] = (str(code).strip(), scrip_id.strip().upper())
    return result


# BSE Ltd. (NSE symbol BSE, ISIN INE118H01025) is not listed on BSE's own
# exchange, so BSE's listing has no scrip code for it and it stays unmapped.
# Codes quoted for it elsewhere (532615, 500180, 532809, 538397) belong to other
# securities in BSE's own data; do not add one without checking it there.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--nse", type=Path, required=True)
    parser.add_argument("--bse", type=Path, required=True)
    args = parser.parse_args()

    nse = read_nse(args.nse)
    bse = read_bse(args.bse)

    mapping = {}
    for isin, symbol in nse.items():
        if isin not in bse:
            print(f"UNMATCHED  {symbol} ({isin}): not in the BSE listing")
            continue
        code, scrip_id = bse[isin]
        if scrip_id != symbol:
            print(f"DIFFERS    {symbol}: BSE id is {scrip_id} (code {code})")
        mapping[code] = symbol

    ordered = dict(sorted(mapping.items()))
    OUTPUT.write_text(json.dumps(ordered, indent=2) + "\n")
    print(f"Wrote {len(ordered)} of {len(nse)} entries to {OUTPUT}")


if __name__ == "__main__":
    main()
