"""Saved watchlists: named lists of symbols kept in a local JSON file.

A watchlist name works anywhere a universe name does. Names of the bundled
universes are reserved, so a name always means one thing.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .symbols import normalize_symbol
from .universes import (
    DEFAULT_UNIVERSE,
    Universe,
    UnknownUniverseError,
    load_universes,
    normalize_universe_name,
    today,
)

WATCHLIST_PATH_ENV = "INDIAN_STOCK_WATCHLIST_PATH"
DEFAULT_WATCHLIST_PATH = Path("~/.indian-stock-market-mcp/watchlists.json")

MAX_WATCHLISTS = 100
MAX_SYMBOLS = 1000
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")


class WatchlistError(ValueError):
    """Raised when a watchlist or the watchlists file cannot be used."""


@dataclass(frozen=True)
class Watchlist:
    id: str
    name: str
    symbols: tuple[str, ...]
    created: date
    updated: date


@dataclass(frozen=True)
class Selection:
    """What a universe argument resolved to: a bundled universe or a watchlist."""

    id: str
    name: str
    symbols: tuple[str, ...]
    kind: str  # "universe" or "watchlist"
    universe: Universe | None = None


def get_watchlist_path() -> Path:
    """Where the watchlists file lives: the environment setting, else the home folder."""
    configured = os.getenv(WATCHLIST_PATH_ENV)
    return (
        Path(configured).expanduser()
        if configured
        else DEFAULT_WATCHLIST_PATH.expanduser()
    )


def watchlist_id(name: str) -> str:
    """The id a name is stored and looked up by: 'My Banks' -> MY_BANKS."""
    return "_".join(name.upper().replace("-", " ").replace("_", " ").split())


def check_name(name: str) -> tuple[str, str]:
    """Return (display name, id) for a usable new watchlist name."""
    if not isinstance(name, str) or not name.strip():
        raise WatchlistError("Watchlist name must be a non-empty string")
    display = name.strip()
    if not NAME_PATTERN.fullmatch(display):
        raise WatchlistError(
            "Watchlist name must be 1 to 40 characters: letters, digits, spaces, "
            "'-' and '_', starting with a letter or digit"
        )
    if normalize_universe_name(display) in load_universes():
        raise WatchlistError(
            f"'{display}' is the name of a bundled universe and cannot be used for "
            "a watchlist"
        )
    return display, watchlist_id(display)


def clean_symbols(symbols: list[str]) -> list[str]:
    """Clean every symbol and drop repeats, keeping the first of each."""
    if not isinstance(symbols, list):
        raise WatchlistError("Symbols must be a list of strings")
    try:
        cleaned = [normalize_symbol(symbol) for symbol in symbols]
    except ValueError as error:
        raise WatchlistError(f"Invalid symbol in list: {error}") from error
    return list(dict.fromkeys(cleaned))


def check_symbol_count(symbols: list[str]) -> None:
    if not symbols:
        raise WatchlistError(
            "A watchlist needs at least one symbol; delete it to remove it"
        )
    if len(symbols) > MAX_SYMBOLS:
        raise WatchlistError(f"A watchlist holds at most {MAX_SYMBOLS} symbols")


def load_watchlists(path: Path | None = None) -> dict[str, Watchlist]:
    """Read every saved watchlist; a missing file means none are saved yet."""
    path = path or get_watchlist_path()
    if not path.exists():
        return {}
    try:
        content = json.loads(path.read_text())
        entries = content["watchlists"]
        loaded = {
            watchlist_id_: Watchlist(
                id=watchlist_id_,
                name=str(entry["name"]),
                symbols=tuple(entry["symbols"]),
                created=date.fromisoformat(entry["created"]),
                updated=date.fromisoformat(entry["updated"]),
            )
            for watchlist_id_, entry in entries.items()
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise WatchlistError(
            f"The watchlists file {path.name} could not be read "
            f"({type(error).__name__}). Fix or delete it; it was not changed."
        ) from error
    return loaded


def save_watchlists(watchlists: dict[str, Watchlist], path: Path | None = None) -> None:
    """Write all watchlists in one step so a failure cannot leave half a file."""
    path = path or get_watchlist_path()
    content = {
        "watchlists": {
            key: {
                "name": value.name,
                "symbols": list(value.symbols),
                "created": value.created.isoformat(),
                "updated": value.updated.isoformat(),
            }
            for key, value in watchlists.items()
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(content, file, indent=2)
            file.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def get_watchlist(name: str) -> Watchlist:
    """Return one saved watchlist by name."""
    if not isinstance(name, str) or not name.strip():
        raise WatchlistError("Watchlist name must be a non-empty string")
    saved = load_watchlists()
    key = watchlist_id(name)
    if key not in saved:
        available = ", ".join(saved) or "none saved"
        raise WatchlistError(
            f"Unknown watchlist '{name.strip()}'. Saved watchlists: {available}"
        )
    return saved[key]


def create_watchlist(name: str, symbols: list[str]) -> Watchlist:
    display, key = check_name(name)
    cleaned = clean_symbols(symbols)
    check_symbol_count(cleaned)
    saved = load_watchlists()
    if key in saved:
        raise WatchlistError(
            f"Watchlist '{saved[key].name}' already exists; update it instead"
        )
    if len(saved) >= MAX_WATCHLISTS:
        raise WatchlistError(f"At most {MAX_WATCHLISTS} watchlists can be saved")
    created = today()
    saved[key] = Watchlist(key, display, tuple(cleaned), created, created)
    save_watchlists(saved)
    return saved[key]


def update_watchlist(
    name: str,
    *,
    add_symbols: list[str] | None = None,
    remove_symbols: list[str] | None = None,
    new_name: str | None = None,
) -> tuple[Watchlist, list[str], list[str]]:
    """Add and remove symbols, or rename, in one save.

    Returns the updated watchlist, the symbols actually added, and the symbols
    asked to be removed that were not in the watchlist.
    """
    if not add_symbols and not remove_symbols and new_name is None:
        raise WatchlistError(
            "Nothing to update: give add_symbols, remove_symbols or new_name"
        )
    saved = load_watchlists()
    current = get_watchlist(name)

    to_add = clean_symbols(add_symbols or [])
    to_remove = clean_symbols(remove_symbols or [])
    overlap = sorted(set(to_add) & set(to_remove))
    if overlap:
        raise WatchlistError(
            f"Cannot add and remove the same symbol: {', '.join(overlap)}"
        )

    members = list(current.symbols)
    added = [symbol for symbol in to_add if symbol not in members]
    members += added
    not_found = [symbol for symbol in to_remove if symbol not in members]
    members = [symbol for symbol in members if symbol not in to_remove]
    check_symbol_count(members)

    display, key = current.name, current.id
    if new_name is not None:
        display, key = check_name(new_name)
        if key != current.id and key in saved:
            raise WatchlistError(f"Watchlist '{saved[key].name}' already exists")

    del saved[current.id]
    saved[key] = Watchlist(key, display, tuple(members), current.created, today())
    save_watchlists(saved)
    return saved[key], added, not_found


def delete_watchlist(name: str) -> Watchlist:
    removed = get_watchlist(name)
    saved = load_watchlists()
    del saved[removed.id]
    save_watchlists(saved)
    return removed


def resolve_selection(name: str = DEFAULT_UNIVERSE) -> Selection:
    """Resolve a name to a bundled universe or, failing that, a saved watchlist."""
    universe_id = normalize_universe_name(name)
    universes = load_universes()
    if universe_id in universes:
        universe = universes[universe_id]
        return Selection(
            universe.id, universe.name, universe.symbols, "universe", universe
        )

    saved = load_watchlists()
    if universe_id in saved:
        watchlist = saved[universe_id]
        return Selection(watchlist.id, watchlist.name, watchlist.symbols, "watchlist")
    raise UnknownUniverseError(
        f"Unknown universe or watchlist '{name.strip()}'. "
        f"Universes: {', '.join(universes)}. "
        f"Watchlists: {', '.join(saved) or 'none saved'}"
    )
