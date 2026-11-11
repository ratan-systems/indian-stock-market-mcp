"""Tests of benchmark resolution: official index values, equal-weight averages,
and the auto_size / auto_sector keywords."""

import pandas as pd
import pytest

from indian_stock_market_mcp import benchmarks, index_closes
from indian_stock_market_mcp.benchmarks import BenchmarkBook, BenchmarkError
from indian_stock_market_mcp.index_closes import index_key
from indian_stock_market_mcp.universes import Universe, get_universe
from indian_stock_market_mcp.watchlists import create_watchlist

DAYS = pd.bdate_range("2026-03-02", periods=3)


def make_prices(paths):
    """Prices from {symbol: [close day 1, 2, 3]}."""
    rows = [
        {"date": day, "symbol": symbol, "close": close}
        for symbol, closes in paths.items()
        for day, close in zip(DAYS, closes, strict=True)
    ]
    return pd.DataFrame(rows)


def official(*values):
    return pd.Series(list(values), index=DAYS[: len(values)])


def members(universe_id, count):
    return list(get_universe(universe_id).symbols[:count])


# --- Which benchmark fits which stock ------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "segment", "universe_id"),
    [
        ("HDFCBANK", "large", "NIFTY_100"),
        (members("NIFTY_MIDCAP_150", 1)[0], "mid", "NIFTY_MIDCAP_150"),
        (members("NIFTY_SMALLCAP_250", 1)[0], "small", "NIFTY_SMALLCAP_250"),
        (members("NIFTY_MICROCAP_250", 1)[0], "micro", "NIFTY_MICROCAP_250"),
    ],
)
def test_a_stock_belongs_to_its_size_segment(symbol, segment, universe_id):
    found_segment, universe = benchmarks.size_segment(symbol)

    assert found_segment == segment
    assert universe.id == universe_id


def test_a_stock_listed_in_two_segments_goes_to_the_largest(monkeypatch):
    # The real NSE size indices do not overlap; this pins the tie-break.
    day = DAYS[0].date()
    monkeypatch.setattr(
        benchmarks,
        "load_universes",
        lambda: {
            "NIFTY_100": Universe("NIFTY_100", "Nifty 100", day, ("X",)),
            "NIFTY_MIDCAP_150": Universe(
                "NIFTY_MIDCAP_150", "Nifty Midcap 150", day, ("X",)
            ),
        },
    )

    assert benchmarks.size_segment("X")[0] == "large"


def test_a_stock_in_no_size_index_has_no_segment():
    assert benchmarks.size_segment("NOSUCHSTOCK") is None


@pytest.mark.parametrize(
    ("symbol", "universe_id"),
    [
        ("HDFCBANK", "NIFTY_PRIVATE_BANK"),  # not Bank (14) or Financial Services (20)
        ("SBIN", "NIFTY_PSU_BANK"),
        ("TCS", "NIFTY_IT"),  # not Services (30)
    ],
)
def test_the_sector_is_the_smallest_sector_index_containing_the_stock(
    symbol, universe_id
):
    assert benchmarks.sector_universe(symbol).id == universe_id


def test_a_stock_in_no_sector_index_has_no_sector():
    assert benchmarks.sector_universe("NOSUCHSTOCK") is None


# --- Official index values --------------------------------------------------------------


def test_a_universe_with_index_values_uses_them():
    series = official(100.0, 101.0, 103.0)
    book = BenchmarkBook(
        make_prices({"A": [1, 2, 3]}), {index_key("Nifty Private Bank"): series}
    )

    result = book.resolve("nifty private bank")

    assert result.official is True
    assert result.id == "NIFTY_PRIVATE_BANK"
    assert result.name == "Nifty Private Bank"
    assert result.levels.tolist() == [100.0, 101.0, 103.0]
    assert result.note is None


def test_index_names_match_however_nse_capitalises_them():
    series = official(1.0, 2.0)
    book = BenchmarkBook(
        make_prices({"A": [1, 2, 3]}), {index_key("NIFTY Midcap 100"): series}
    )

    assert book.resolve("NIFTY_MIDCAP_100").official is True


# --- Equal-weight averages -----------------------------------------------------------------


def private_bank_prices(**overrides):
    paths = {symbol: [100, 110, 121] for symbol in members("NIFTY_PRIVATE_BANK", 3)}
    paths.update(overrides)
    return make_prices(paths)


def test_without_an_index_file_the_average_of_the_members_is_used_and_says_why():
    book = BenchmarkBook(private_bank_prices(), index_closes=None)

    result = book.resolve("NIFTY_PRIVATE_BANK")

    assert result.official is False
    assert result.levels.round(6).tolist() == [100.0, 110.0, 121.0]
    assert "No index file is configured" in result.note
    assert "3 of 10 members" in result.note


def test_the_average_is_of_daily_returns_across_the_members_that_traded():
    first, second = members("NIFTY_PRIVATE_BANK", 2)
    prices = make_prices({first: [100, 110, 121], second: [100, 120, 120]})
    # Day 2: returns +10% and +20% average +15%. Day 3: +10% and 0% average +5%.

    result = BenchmarkBook(prices).resolve("NIFTY_PRIVATE_BANK")

    assert result.levels.round(6).tolist() == [100.0, 115.0, 120.75]


def test_a_member_missing_on_a_day_is_left_out_of_that_days_average():
    first, second = members("NIFTY_PRIVATE_BANK", 2)
    prices = make_prices({first: [100, 110, 121], second: [100, 100, 100]})
    prices = prices[~((prices["symbol"] == second) & (prices["date"] == DAYS[2]))]

    result = BenchmarkBook(prices).resolve("NIFTY_PRIVATE_BANK")

    # Day 3 has only `first`, which rose 10%; day 2 averages +10% and 0%.
    assert result.levels.round(6).tolist() == [100.0, 105.0, 115.5]


def test_an_index_file_without_that_index_falls_back_with_the_reason():
    book = BenchmarkBook(
        private_bank_prices(), {index_key("Nifty 50"): official(1.0, 2.0)}
    )

    result = book.resolve("NIFTY_PRIVATE_BANK")

    assert result.official is False
    assert "has no values for Nifty Private Bank" in result.note


def test_an_index_with_a_single_value_cannot_be_used_as_a_series():
    book = BenchmarkBook(
        private_bank_prices(), {index_key("Nifty Private Bank"): official(100.0)}
    )

    assert book.resolve("NIFTY_PRIVATE_BANK").official is False


def test_an_unreadable_index_file_is_the_reason_given_for_the_fallback():
    book = BenchmarkBook(
        private_bank_prices(), None, "The index file could not be read"
    )

    assert "The index file could not be read" in book.resolve("NIFTY_PRIVATE_BANK").note


def test_a_benchmark_needs_at_least_two_members_in_the_dataset():
    only_one = make_prices({members("NIFTY_PRIVATE_BANK", 1)[0]: [100, 110, 121]})

    with pytest.raises(BenchmarkError, match="at least 2"):
        BenchmarkBook(only_one).resolve("NIFTY_PRIVATE_BANK")


def test_a_watchlist_can_be_a_benchmark_and_is_always_an_average():
    create_watchlist("mine", ["AAA", "BBB"])
    prices = make_prices({"AAA": [100, 110, 121], "BBB": [100, 110, 121]})
    book = BenchmarkBook(prices, {index_key("Nifty 50"): official(1.0, 2.0)})

    result = book.resolve("mine")

    assert (result.id, result.official) == ("MINE", False)
    assert "watchlist has no official index" in result.note


# --- The auto keywords ----------------------------------------------------------------------


def test_auto_sector_resolves_per_stock():
    paths = {
        s: [100, 110, 121]
        for s in members("NIFTY_PRIVATE_BANK", 3) + members("NIFTY_IT", 3)
    }
    book = BenchmarkBook(make_prices(paths))

    bank = book.resolve("auto_sector", "HDFCBANK")
    it = book.resolve("AUTO_SECTOR", "TCS")

    assert (bank.id, bank.key, bank.segment) == (
        "NIFTY_PRIVATE_BANK",
        "auto_sector",
        "Nifty Private Bank",
    )
    assert it.id == "NIFTY_IT"


def test_auto_size_names_the_segment():
    large = members("NIFTY_100", 3)
    book = BenchmarkBook(make_prices({s: [100, 110, 121] for s in large}))

    result = book.resolve("auto_size", large[0])

    assert (result.id, result.segment, result.key) == (
        "NIFTY_100",
        "large",
        "auto_size",
    )


def test_an_auto_keyword_with_no_match_gives_none():
    book = BenchmarkBook(make_prices({"A": [1, 2, 3]}))

    assert book.resolve("auto_size", "NOSUCHSTOCK") is None
    assert book.resolve("auto_sector", "NOSUCHSTOCK") is None


def test_an_auto_keyword_needs_a_stock():
    with pytest.raises(BenchmarkError, match="needs a stock"):
        BenchmarkBook(make_prices({"A": [1, 2, 3]})).resolve("auto_size")


# --- Errors and reuse ----------------------------------------------------------------------


def test_an_unknown_name_is_an_error_naming_the_choices():
    with pytest.raises(BenchmarkError, match="Unknown universe or watchlist"):
        BenchmarkBook(make_prices({"A": [1, 2, 3]})).resolve("NOT_AN_INDEX")


def test_a_series_is_built_once_and_shared_between_stocks_and_names():
    first = members("NIFTY_PRIVATE_BANK", 3)[0]
    book = BenchmarkBook(private_bank_prices())

    by_name = book.resolve("NIFTY_PRIVATE_BANK")
    by_auto = book.resolve("auto_sector", first)

    assert by_auto.levels is by_name.levels
    assert (by_name.key, by_auto.key) == ("NIFTY_PRIVATE_BANK", "auto_sector")


def test_load_uses_the_configured_index_file_or_records_why_not(tmp_path, monkeypatch):
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "ind_close_all_02032026.csv").write_text(
        "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
        "Closing Index Value\nNifty Private Bank,02-03-2026,1,2,0.5,100\n"
    )
    (folder / "ind_close_all_03032026.csv").write_text(
        "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
        "Closing Index Value\nNifty Private Bank,03-03-2026,1,2,0.5,102\n"
    )
    output = tmp_path / "index_closes.parquet"
    index_closes.import_index_folder(folder, output)
    monkeypatch.setenv(index_closes.INDEX_PATH_ENV, str(output))

    good = BenchmarkBook.load(private_bank_prices()).resolve("NIFTY_PRIVATE_BANK")

    assert good.official is True
    assert good.levels.tolist() == [100.0, 102.0]

    monkeypatch.setenv(index_closes.INDEX_PATH_ENV, str(tmp_path / "missing.parquet"))
    book = BenchmarkBook.load(private_bank_prices())

    assert "was not found" in book.resolve("NIFTY_PRIVATE_BANK").note
