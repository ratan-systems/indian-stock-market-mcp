"""Benchmarks for relative strength: what a stock is measured against.

A benchmark is any bundled universe or saved watchlist, or one of two keywords
that pick the fit for each stock:

- auto_size: the stock's size segment index (Nifty 100 for large caps, Midcap
  150, Smallcap 250 or Microcap 250).
- auto_sector: the smallest sector index that contains the stock, so HDFC Bank
  gets Nifty Private Bank rather than Nifty Financial Services.

Each benchmark becomes a series of index levels by date. It is official when
the imported index-close file has values for the universe's index, and
otherwise an equal-weight average of the members' daily returns, which is
labelled as an approximation with the reason.
"""

from dataclasses import dataclass

import pandas as pd

from .index_closes import IndexCloseError, index_key, load_index_closes
from .settings import AUTO_BENCHMARKS
from .universes import Universe, UnknownUniverseError, load_universes
from .watchlists import resolve_selection

AUTO_SIZE, AUTO_SECTOR = AUTO_BENCHMARKS

# Size segments, largest first. A stock belongs to the first that contains it.
SIZE_SEGMENTS = (
    ("large", "NIFTY_100"),
    ("mid", "NIFTY_MIDCAP_150"),
    ("small", "NIFTY_SMALLCAP_250"),
    ("micro", "NIFTY_MICROCAP_250"),
)
MIN_MEMBERS = 2


class BenchmarkError(ValueError):
    """Raised when a benchmark cannot be resolved or built."""


@dataclass(frozen=True)
class Benchmark:
    key: str  # what was asked for: "NIFTY_50", "auto_size", a watchlist name
    id: str  # the universe or watchlist it resolved to
    name: str  # display name, such as "Nifty Midcap 150"
    official: bool  # True when built from official index closes
    levels: pd.Series  # index level by date
    segment: str | None = None  # size segment or sector name for the auto keywords
    note: str | None = None  # why an approximation was used


def size_segment(symbol: str) -> tuple[str, Universe] | None:
    """The size segment of a stock and its universe, or None if in none."""
    universes = load_universes()
    for segment, universe_id in SIZE_SEGMENTS:
        universe = universes.get(universe_id)
        if universe and symbol in universe.symbols:
            return segment, universe
    return None


def sector_universe(symbol: str) -> Universe | None:
    """The smallest sector universe containing the stock, or None."""
    containing = [
        universe
        for universe in load_universes().values()
        if universe.group == "sector" and symbol in universe.symbols
    ]
    return min(containing, key=lambda u: (len(u.symbols), u.id)) if containing else None


def equal_weight_levels(
    prices: pd.DataFrame, symbols: tuple[str, ...]
) -> tuple[pd.Series, int]:
    """An index level (starting at 100) from the average daily return of the
    members that traded each day, and how many members the dataset had."""
    members = prices[prices["symbol"].isin(symbols)]
    closes = members.pivot(index="date", columns="symbol", values="close").sort_index()
    if closes.shape[1] < MIN_MEMBERS:
        raise BenchmarkError(
            f"Only {closes.shape[1]} of the {len(symbols)} members are in the "
            f"dataset; at least {MIN_MEMBERS} are needed for an average"
        )
    daily = closes.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)
    return (1 + daily).cumprod() * 100, closes.shape[1]


class BenchmarkBook:
    """Resolves benchmark names for one dataset, building each series once."""

    def __init__(
        self,
        prices: pd.DataFrame,
        index_closes: dict[str, pd.Series] | None = None,
        index_problem: str | None = None,
    ) -> None:
        self.prices = prices
        self.index_closes = index_closes
        self.index_problem = index_problem
        self._cache: dict[str, Benchmark] = {}

    @classmethod
    def load(cls, prices: pd.DataFrame) -> "BenchmarkBook":
        """A book using the configured index-close file, if there is one."""
        try:
            return cls(prices, load_index_closes())
        except IndexCloseError as error:
            return cls(prices, None, str(error))

    def resolve(self, key: str, symbol: str | None = None) -> Benchmark | None:
        """The benchmark for a name or keyword.

        Returns None when an auto keyword has no match for the stock (it is in
        no size segment, or in no sector index). Raises BenchmarkError for an
        unknown name or a benchmark that cannot be built.
        """
        lowered = key.strip().lower() if isinstance(key, str) else ""
        if lowered in AUTO_BENCHMARKS:
            if symbol is None:
                raise BenchmarkError(
                    f"{lowered} needs a stock to choose a benchmark for"
                )
            return self.resolve_auto(lowered, symbol)
        try:
            selection = resolve_selection(key)
        except (UnknownUniverseError, ValueError) as error:
            raise BenchmarkError(str(error)) from error
        return self.build(
            key, selection.id, selection.name, selection.universe, selection.symbols
        )

    def resolve_auto(self, keyword: str, symbol: str) -> Benchmark | None:
        if keyword == AUTO_SIZE:
            found = size_segment(symbol)
            if found is None:
                return None
            segment, universe = found
        else:
            universe = sector_universe(symbol)
            if universe is None:
                return None
            segment = universe.name
        benchmark = self.build(
            keyword, universe.id, universe.name, universe, universe.symbols
        )
        return Benchmark(**{**benchmark.__dict__, "key": keyword, "segment": segment})

    def build(
        self,
        key: str,
        benchmark_id: str,
        name: str,
        universe: Universe | None,
        symbols: tuple[str, ...],
    ) -> Benchmark:
        cached = self._cache.get(benchmark_id)
        if cached is not None:
            return Benchmark(**{**cached.__dict__, "key": key})

        official, note = self.official_series(universe)
        if official is not None:
            benchmark = Benchmark(key, benchmark_id, name, True, official)
        else:
            levels, used = equal_weight_levels(self.prices, symbols)
            benchmark = Benchmark(
                key,
                benchmark_id,
                name,
                False,
                levels,
                note=(
                    f"{note}; using the equal-weight average of the {used} of "
                    f"{len(symbols)} members found in the dataset"
                ),
            )
        self._cache[benchmark_id] = benchmark
        return benchmark

    def official_series(
        self, universe: Universe | None
    ) -> tuple[pd.Series | None, str]:
        """The official index series for a universe, or None and the reason."""
        if universe is None:
            return None, "A watchlist has no official index"
        if not universe.index_name:
            return None, f"{universe.name} has no index name to look up"
        if self.index_problem:
            return None, self.index_problem
        if self.index_closes is None:
            return None, "No index file is configured (set INDIAN_STOCK_INDEX_PATH)"
        series = self.index_closes.get(index_key(universe.index_name))
        if series is None or len(series) < 2:
            return None, f"The index file has no values for {universe.index_name}"
        return series, ""
