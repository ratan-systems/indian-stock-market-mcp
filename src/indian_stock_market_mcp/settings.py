"""Saved indicator settings: the user's own defaults for the analysis tools.

Only the values the user changed are stored, so a default improved in a later
version still reaches anyone who never touched it. Every value is checked
against an allowed range before it is saved, and again when it is read back.
"""

import json
import math
import os
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from .atomic import write_json_atomically
from .universes import normalize_universe_name

SETTINGS_PATH_ENV = "INDIAN_STOCK_SETTINGS_PATH"
DEFAULT_SETTINGS_PATH = Path("~/.indian-stock-market-mcp/indicator_settings.json")

# Words that stand for "the benchmark that fits each stock", not a fixed index.
AUTO_BENCHMARKS = ("auto_size", "auto_sector")
MAX_BENCHMARKS = 5
PERIOD_NAME = re.compile(r"[A-Za-z0-9_]{1,10}")

DEFAULTS: dict[str, Any] = {
    "ema_periods": [20, 50, 200],
    "rsi_period": 14,
    "atr_period": 14,
    "traded_value_sessions": 20,
    "illiquid_below_crore": 1.0,
    "year_sessions": 252,
    "near_high_percent": 5.0,
    "relative_strength_periods": {"1m": 21, "3m": 63, "6m": 126},
    "default_benchmarks": ["NIFTY_50", *AUTO_BENCHMARKS],
    # Screens (topic 3). The momentum screen: close above this EMA, RSI inside
    # the band, and a return above the minimum over the return window.
    "momentum_ema_period": 50,
    "momentum_rsi_min": 50.0,
    "momentum_rsi_max": 70.0,
    "momentum_return_sessions": 63,
    "momentum_min_return_percent": 0.0,
    "oversold_rsi": 30.0,
    "volume_spike_multiple": 2.0,
    "volume_average_sessions": 20,
    "breakout_sessions": 20,
    "resistance_pivot_sessions": 5,
    "resistance_lookback_sessions": 120,
}


class SettingsError(ValueError):
    """Raised when a setting or the settings file cannot be used."""


def get_settings_path() -> Path:
    """Where the settings file lives: the environment setting, else the home folder."""
    configured = os.getenv(SETTINGS_PATH_ENV)
    return (
        Path(configured).expanduser()
        if configured
        else DEFAULT_SETTINGS_PATH.expanduser()
    )


def check_int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"{name} must be a whole number, got {value!r}")
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, got {value}")
    return value


def check_number(name: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SettingsError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value) or not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, got {value}")
    return float(value)


def check_list(name: str, value: Any, most: int) -> list:
    if not isinstance(value, list) or not 1 <= len(value) <= most:
        raise SettingsError(f"{name} must be a list of 1 to {most} items")
    return value


def check_ema_periods(value: Any) -> list[int]:
    items = check_list("ema_periods", value, 5)
    periods = [check_int("ema_periods item", item, 2, 400) for item in items]
    return list(dict.fromkeys(periods))


def check_rs_periods(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or not 1 <= len(value) <= 6:
        raise SettingsError(
            "relative_strength_periods must map 1 to 6 names to session counts, "
            'for example {"3m": 63}'
        )
    cleaned = {}
    for label, sessions in value.items():
        if not isinstance(label, str) or not PERIOD_NAME.fullmatch(label):
            raise SettingsError(
                f"Period name {label!r} must be 1 to 10 letters, digits or '_'"
            )
        cleaned[label] = check_int(
            f"relative_strength_periods[{label}]", sessions, 5, 504
        )
    return cleaned


def clean_benchmark(name: Any) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
        raise SettingsError(f"Benchmark name {name!r} must be 1 to 40 characters")
    if name.strip().lower() in AUTO_BENCHMARKS:
        return name.strip().lower()
    return normalize_universe_name(name)


def check_benchmarks(value: Any) -> list[str]:
    items = check_list("default_benchmarks", value, MAX_BENCHMARKS)
    return list(dict.fromkeys(clean_benchmark(item) for item in items))


VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "ema_periods": check_ema_periods,
    "rsi_period": lambda v: check_int("rsi_period", v, 2, 100),
    "atr_period": lambda v: check_int("atr_period", v, 2, 100),
    "traded_value_sessions": lambda v: check_int("traded_value_sessions", v, 5, 250),
    "illiquid_below_crore": lambda v: check_number("illiquid_below_crore", v, 0, 1000),
    "year_sessions": lambda v: check_int("year_sessions", v, 200, 260),
    "near_high_percent": lambda v: check_number("near_high_percent", v, 0.5, 50),
    "relative_strength_periods": check_rs_periods,
    "default_benchmarks": check_benchmarks,
    "momentum_ema_period": lambda v: check_int("momentum_ema_period", v, 2, 400),
    "momentum_rsi_min": lambda v: check_number("momentum_rsi_min", v, 0, 100),
    "momentum_rsi_max": lambda v: check_number("momentum_rsi_max", v, 0, 100),
    "momentum_return_sessions": lambda v: check_int(
        "momentum_return_sessions", v, 1, 504
    ),
    "momentum_min_return_percent": lambda v: check_number(
        "momentum_min_return_percent", v, -100, 1000
    ),
    "oversold_rsi": lambda v: check_number("oversold_rsi", v, 1, 50),
    "volume_spike_multiple": lambda v: check_number("volume_spike_multiple", v, 1, 100),
    "volume_average_sessions": lambda v: check_int(
        "volume_average_sessions", v, 5, 250
    ),
    "breakout_sessions": lambda v: check_int("breakout_sessions", v, 2, 260),
    "resistance_pivot_sessions": lambda v: check_int(
        "resistance_pivot_sessions", v, 2, 20
    ),
    "resistance_lookback_sessions": lambda v: check_int(
        "resistance_lookback_sessions", v, 20, 504
    ),
}


def validate_changes(changes: Any) -> dict[str, Any]:
    """Check setting names and values; return the cleaned values."""
    if not isinstance(changes, dict) or not changes:
        raise SettingsError("changes must be a non-empty object of setting values")
    unknown = sorted(set(changes) - set(VALIDATORS))
    if unknown:
        raise SettingsError(
            f"Unknown setting(s): {', '.join(unknown)}. "
            f"Available: {', '.join(VALIDATORS)}"
        )
    return {name: VALIDATORS[name](value) for name, value in changes.items()}


def load_overrides(path: Path | None = None) -> dict[str, Any]:
    """Read the saved changes; a missing file means nothing was changed."""
    path = path or get_settings_path()
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text())["settings"]
        return validate_changes(stored) if stored else {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise SettingsError(
            f"The settings file {path.name} could not be read "
            f"({type(error).__name__}: {error}). Fix or delete it; it was not changed."
        ) from error


def save_overrides(overrides: dict[str, Any], path: Path | None = None) -> None:
    """Write the saved changes in one step so a failure cannot leave half a file."""
    write_json_atomically(path or get_settings_path(), {"settings": overrides})


def load_settings() -> dict[str, Any]:
    """Every setting: the built-in default unless the user changed it."""
    return {**deepcopy(DEFAULTS), **load_overrides()}


def effective_settings(**overrides: Any) -> dict[str, Any]:
    """The saved settings with per-question overrides on top.

    An override of None means "use the saved value", so a tool argument left
    out never changes anything.
    """
    given = {name: value for name, value in overrides.items() if value is not None}
    return {**load_settings(), **validate_changes(given)} if given else load_settings()


def update_settings(changes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Save changed values. Returns every setting and the names that now differ
    from the default (a value set back to its default is no longer stored)."""
    cleaned = validate_changes(changes)
    overrides = {**load_overrides(), **cleaned}
    overrides = {k: v for k, v in overrides.items() if v != DEFAULTS[k]}
    save_overrides(overrides)
    return {**deepcopy(DEFAULTS), **overrides}, sorted(overrides)


def reset_settings(names: list[str] | None = None) -> tuple[dict[str, Any], list[str]]:
    """Put settings back to their defaults: the named ones, or all of them.

    Returns every setting and the names that were actually reset.
    """
    overrides = load_overrides()
    if names is None:
        chosen = list(overrides)
    else:
        unknown = sorted(set(names) - set(DEFAULTS))
        if unknown:
            raise SettingsError(
                f"Unknown setting(s): {', '.join(unknown)}. "
                f"Available: {', '.join(DEFAULTS)}"
            )
        chosen = [name for name in names if name in overrides]
    remaining = {k: v for k, v in overrides.items() if k not in chosen}
    if chosen:
        save_overrides(remaining)
    return {**deepcopy(DEFAULTS), **remaining}, chosen


def describe_settings() -> dict[str, Any]:
    """The current values, the defaults, and which values the user changed."""
    overrides = load_overrides()
    return {
        "settings": {**deepcopy(DEFAULTS), **overrides},
        "defaults": deepcopy(DEFAULTS),
        "customised": sorted(overrides),
    }
