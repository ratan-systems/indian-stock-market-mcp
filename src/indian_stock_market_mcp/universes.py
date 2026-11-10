"""Bundled index universes: named lists of symbols, each with an as-of date."""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.resources import files

UNIVERSES_PATH = files("indian_stock_market_mcp").joinpath("resources/universes.json")

DEFAULT_UNIVERSE = "NIFTY_50"

# A list older than this is probably out of date: NSE reviews most indices
# twice a year.
STALE_AFTER_DAYS = 180

# Other ways of writing a universe name, after cleanup.
ALIASES = {
    "BANK_NIFTY": "NIFTY_BANK",
    "BANKNIFTY": "NIFTY_BANK",
    "NIFTY50": "NIFTY_50",
    "NIFTY100": "NIFTY_100",
    "NIFTY200": "NIFTY_200",
    "NIFTY500": "NIFTY_500",
}


class UnknownUniverseError(ValueError):
    """Raised when a universe name is not one of the bundled universes."""


@dataclass(frozen=True)
class Universe:
    id: str
    name: str
    as_of: date
    symbols: tuple[str, ...]


def today() -> date:
    """Today's date; a function so tests can replace it."""
    return datetime.now(UTC).date()


def load_universes() -> dict[str, Universe]:
    """Read and validate every bundled universe, in file order."""
    if not UNIVERSES_PATH.is_file():
        raise FileNotFoundError(
            "Bundled universes file was not found. Reinstall the package"
        )
    content = json.loads(UNIVERSES_PATH.read_text())
    entries = content.get("universes") if isinstance(content, dict) else None
    if not isinstance(entries, dict):
        raise TypeError("Universes file must contain a 'universes' object")
    if not entries:
        raise ValueError("Universes file cannot be empty")

    universes = {}
    for universe_id, entry in entries.items():
        symbols = entry.get("symbols") if isinstance(entry, dict) else None
        if not isinstance(symbols, list):
            raise TypeError(f"Universe {universe_id} must contain a list of symbols")
        if not all(isinstance(symbol, str) for symbol in symbols):
            raise ValueError(f"Universe {universe_id} must contain only strings")
        symbols = [symbol.strip().upper() for symbol in symbols]
        if not all(symbols):
            raise ValueError(f"Universe {universe_id} cannot contain empty symbols")
        if not symbols:
            raise ValueError(f"Universe {universe_id} cannot be empty")
        try:
            as_of = date.fromisoformat(entry["as_of"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"Universe {universe_id} needs an as_of date in YYYY-MM-DD form"
            ) from error
        universes[universe_id] = Universe(
            id=universe_id,
            name=str(entry.get("name", universe_id)),
            as_of=as_of,
            symbols=tuple(dict.fromkeys(symbols)),
        )
    return universes


def normalize_universe_name(name: str) -> str:
    """Return the canonical id for a universe name: 'nifty bank' -> NIFTY_BANK."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Universe must be a non-empty string")
    cleaned = "_".join(name.upper().replace("-", " ").replace("_", " ").split())
    return ALIASES.get(cleaned, cleaned)


def get_universe(name: str = DEFAULT_UNIVERSE) -> Universe:
    """Return one universe by name, accepting common spellings."""
    universe_id = normalize_universe_name(name)
    universes = load_universes()
    if universe_id not in universes:
        raise UnknownUniverseError(
            f"Unknown universe '{name.strip()}'. Available: {', '.join(universes)}"
        )
    return universes[universe_id]


def universe_age_days(universe: Universe) -> int:
    return (today() - universe.as_of).days
