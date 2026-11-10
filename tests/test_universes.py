"""Tests of the bundled index universes and the tools that use them."""

import asyncio
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import data, universes
from indian_stock_market_mcp import server as server_module
from indian_stock_market_mcp.universes import (
    STALE_AFTER_DAYS,
    UnknownUniverseError,
    get_universe,
    load_universes,
    normalize_universe_name,
)

ROOT = Path(__file__).parents[1]
SAMPLE_CSV = ROOT / "data" / "sample_equity_daily.csv"
EXPECTED_IDS = [
    "NIFTY_50",
    "NIFTY_NEXT_50",
    "NIFTY_100",
    "NIFTY_200",
    "NIFTY_500",
    "NIFTY_BANK",
    "NIFTY_IT",
    "NIFTY_PHARMA",
    "NIFTY_AUTO",
    "NIFTY_FMCG",
    "NIFTY_METAL",
    "NIFTY_ENERGY",
]


def call_envelope(name, arguments=None):
    result = asyncio.run(server_module.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def write_dataset(tmp_path, monkeypatch, symbols, sessions=5, returns=None):
    """A CSV with `sessions` consecutive days for each symbol."""
    rows = []
    for number, symbol in enumerate(symbols):
        end = 100.0 * (1 + (returns[number] if returns else 0.0))
        closes = [100.0] * (sessions - 1) + [end]
        for day, close in enumerate(closes):
            rows.append(
                {
                    "date": f"2026-03-{day + 2:02d}",
                    "symbol": symbol,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000,
                }
            )
    path = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))


@pytest.fixture
def no_dataset(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", "")
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)


def fixed_today(monkeypatch, day: date):
    monkeypatch.setattr(universes, "today", lambda: day)


# --- The bundled lists -------------------------------------------------------


def test_exactly_the_expected_universes_are_bundled():
    assert list(load_universes()) == EXPECTED_IDS


@pytest.mark.parametrize("universe_id", EXPECTED_IDS)
def test_every_bundled_universe_loads_and_is_clean(universe_id):
    universe = load_universes()[universe_id]

    assert universe.id == universe_id
    assert universe.name
    assert isinstance(universe.as_of, date)
    assert universe.symbols
    assert len(set(universe.symbols)) == len(universe.symbols)
    assert all(s and s == s.strip().upper() for s in universe.symbols)


@pytest.mark.parametrize(
    "universe_id, size",
    [("NIFTY_50", 50), ("NIFTY_NEXT_50", 50), ("NIFTY_100", 100), ("NIFTY_200", 200)],
)
def test_the_named_index_sizes_are_right(universe_id, size):
    assert len(get_universe(universe_id).symbols) == size


def test_nifty_500_holds_about_five_hundred_symbols():
    assert 500 <= len(get_universe("NIFTY_500").symbols) <= 505


def test_the_broad_indices_nest_inside_each_other():
    sets = {key: set(value.symbols) for key, value in load_universes().items()}

    assert sets["NIFTY_100"] == sets["NIFTY_50"] | sets["NIFTY_NEXT_50"]
    assert not sets["NIFTY_50"] & sets["NIFTY_NEXT_50"]
    assert sets["NIFTY_100"] <= sets["NIFTY_200"] <= sets["NIFTY_500"]


@pytest.mark.parametrize(
    "universe_id",
    [
        "NIFTY_BANK",
        "NIFTY_IT",
        "NIFTY_PHARMA",
        "NIFTY_AUTO",
        "NIFTY_FMCG",
        "NIFTY_METAL",
        "NIFTY_ENERGY",
    ],
)
def test_sector_indices_draw_on_the_nifty_500(universe_id):
    assert set(get_universe(universe_id).symbols) <= set(
        get_universe("NIFTY_500").symbols
    )


def test_the_nifty_50_helper_matches_the_universe():
    assert data.get_nifty50_universe() == list(get_universe("NIFTY_50").symbols)


# --- Names -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("NIFTY_500", "NIFTY_500"),
        ("nifty_500", "NIFTY_500"),
        ("Nifty 500", "NIFTY_500"),
        (" nifty-500 ", "NIFTY_500"),
        ("NIFTY500", "NIFTY_500"),
        ("nifty next 50", "NIFTY_NEXT_50"),
        ("bank nifty", "NIFTY_BANK"),
        ("BANKNIFTY", "NIFTY_BANK"),
        ("Nifty IT", "NIFTY_IT"),
    ],
)
def test_common_spellings_find_the_same_universe(name, expected):
    assert normalize_universe_name(name) == expected
    assert get_universe(name).id == expected


@pytest.mark.parametrize("name", ["", "   ", None, 5])
def test_empty_or_non_string_names_are_rejected(name):
    with pytest.raises(ValueError, match="non-empty string"):
        normalize_universe_name(name)


def test_an_unknown_universe_lists_the_available_ones():
    with pytest.raises(UnknownUniverseError) as error:
        get_universe("nifty 1000")

    message = str(error.value)
    assert "Unknown universe 'nifty 1000'" in message
    assert all(universe_id in message for universe_id in EXPECTED_IDS)


# --- list_universes ----------------------------------------------------------


def test_list_universes_works_without_a_dataset(no_dataset):
    envelope = call_envelope("list_universes")

    assert [u["id"] for u in envelope["data"]["universes"]] == EXPECTED_IDS
    assert envelope["data"]["dataset_checked"] is False
    assert all(
        "symbols_missing_from_dataset" not in u for u in envelope["data"]["universes"]
    )
    assert "universe_symbols_missing" not in {w["code"] for w in envelope["warnings"]}
    assert envelope["source"]["file_name"] == "universes.json"


def test_list_universes_reports_each_universe_with_its_as_of_date(no_dataset):
    first = call_envelope("list_universes")["data"]["universes"][0]

    assert set(first) == {"id", "name", "symbol_count", "as_of", "age_days", "outdated"}
    assert first["symbol_count"] == 50
    assert first["as_of"] == get_universe("NIFTY_50").as_of.isoformat()


def test_list_universes_counts_symbols_missing_from_the_dataset(tmp_path, monkeypatch):
    write_dataset(tmp_path, monkeypatch, ["RELIANCE", "TCS", "INFY"])

    envelope = call_envelope("list_universes")
    by_id = {u["id"]: u for u in envelope["data"]["universes"]}

    assert envelope["data"]["dataset_checked"] is True
    assert by_id["NIFTY_50"]["symbols_in_dataset"] == 3
    assert by_id["NIFTY_50"]["symbols_missing_from_dataset"] == 47
    assert len(by_id["NIFTY_50"]["missing_sample"]) == 20
    warning = next(
        w for w in envelope["warnings"] if w["code"] == "universe_symbols_missing"
    )
    assert "NIFTY_50 47" in warning["message"]


def test_a_universe_fully_covered_by_the_dataset_is_not_named_in_the_warning(
    tmp_path, monkeypatch
):
    write_dataset(tmp_path, monkeypatch, list(get_universe("NIFTY_IT").symbols))

    envelope = call_envelope("list_universes")
    by_id = {u["id"]: u for u in envelope["data"]["universes"]}

    assert by_id["NIFTY_IT"]["symbols_missing_from_dataset"] == 0
    warning = next(
        w for w in envelope["warnings"] if w["code"] == "universe_symbols_missing"
    )
    assert "NIFTY_IT" not in warning["message"]


def test_an_unreadable_dataset_does_not_stop_list_universes(tmp_path, monkeypatch):
    path = tmp_path / "broken.csv"
    path.write_text("not,a,price,file\n")
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))

    assert call_envelope("list_universes")["data"]["dataset_checked"] is False


# --- Old lists ---------------------------------------------------------------


@pytest.mark.parametrize(
    "days_later, outdated",
    [
        (STALE_AFTER_DAYS - 1, False),
        (STALE_AFTER_DAYS, False),
        (STALE_AFTER_DAYS + 1, True),
    ],
)
def test_a_list_is_outdated_only_after_the_limit(
    monkeypatch, no_dataset, days_later, outdated
):
    as_of = get_universe("NIFTY_50").as_of
    fixed_today(monkeypatch, date.fromordinal(as_of.toordinal() + days_later))

    envelope = call_envelope("list_universes")

    assert envelope["data"]["universes"][0]["outdated"] is outdated
    assert (
        "universe_outdated" in {w["code"] for w in envelope["warnings"]}
    ) is outdated


def test_the_outdated_warning_names_the_universes_and_their_age(
    monkeypatch, no_dataset
):
    as_of = get_universe("NIFTY_50").as_of
    fixed_today(monkeypatch, date.fromordinal(as_of.toordinal() + 200))

    warning = next(
        w
        for w in call_envelope("list_universes")["warnings"]
        if w["code"] == "universe_outdated"
    )

    assert "NIFTY_50 (200 days)" in warning["message"]
    assert "180 days" in warning["message"]


def test_the_nifty_50_tool_reports_its_as_of_date_and_warns_when_old(
    monkeypatch, no_dataset
):
    envelope = call_envelope("get_nifty50_universe")
    assert envelope["as_of"] == get_universe("NIFTY_50").as_of.isoformat()

    fixed_today(monkeypatch, date(2099, 1, 1))
    assert "universe_outdated" in {
        w["code"] for w in call_envelope("get_nifty50_universe")["warnings"]
    }


# --- Ranking a universe ------------------------------------------------------


def test_ranking_a_chosen_universe_uses_only_its_symbols(tmp_path, monkeypatch):
    bank = list(get_universe("NIFTY_BANK").symbols)
    returns = [0.01 * (number + 1) for number in range(len(bank))]
    write_dataset(tmp_path, monkeypatch, bank + ["TCS"], returns=returns + [0.5])

    envelope = call_envelope(
        "rank_weekly_performers", {"top_n": 3, "universe": "bank nifty"}
    )

    ranked = [row["symbol"] for row in envelope["data"]["rankings"]]
    assert envelope["data"]["universe"] == "NIFTY_BANK"
    assert envelope["parameters"] == {"top_n": 3, "universe": "NIFTY_BANK"}
    assert ranked == bank[::-1][:3]
    assert "TCS" not in ranked
    assert envelope["data"]["skipped"] == []


def test_ranking_without_a_universe_still_uses_the_nifty_50(tmp_path, monkeypatch):
    write_dataset(tmp_path, monkeypatch, ["TCS", "RELIANCE"], returns=[0.1, 0.2])

    envelope = call_envelope("rank_weekly_performers")

    assert envelope["data"]["universe"] == "NIFTY_50"
    assert [row["symbol"] for row in envelope["data"]["rankings"]] == [
        "RELIANCE",
        "TCS",
    ]
    assert len(envelope["data"]["skipped"]) == 48


def test_ranking_an_unknown_universe_is_a_clear_tool_error(tmp_path, monkeypatch):
    write_dataset(tmp_path, monkeypatch, ["TCS"])

    with pytest.raises(ToolError, match="Unknown universe 'nifty 1000'"):
        call_envelope("rank_weekly_performers", {"universe": "nifty 1000"})


def test_ranking_the_nifty_500_loads_the_dataset_only_once(tmp_path, monkeypatch):
    write_dataset(tmp_path, monkeypatch, ["TCS", "RELIANCE"])
    loads = []
    original = data.load_full_dataset
    monkeypatch.setattr(
        data, "load_full_dataset", lambda path: loads.append(path) or original(path)
    )

    envelope = call_envelope("rank_weekly_performers", {"universe": "NIFTY_500"})

    assert len(loads) == 1
    assert (
        len(envelope["data"]["skipped"]) == len(get_universe("NIFTY_500").symbols) - 2
    )


def test_ranking_warns_when_the_chosen_universe_is_old(tmp_path, monkeypatch):
    write_dataset(tmp_path, monkeypatch, ["TCS"])
    fixed_today(monkeypatch, date(2099, 1, 1))

    codes = {w["code"] for w in call_envelope("rank_weekly_performers")["warnings"]}

    assert "universe_outdated" in codes


# --- The build script --------------------------------------------------------


def load_script():
    path = ROOT / "scripts" / "build_universes.py"
    spec = importlib.util.spec_from_file_location("build_universes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_inputs(folder: Path, module, symbols_for=None):
    folder.mkdir(exist_ok=True)
    for universe_id, (_, file_name) in module.UNIVERSES.items():
        symbols = (symbols_for or {}).get(
            universe_id, [f"{universe_id}_A", f"{universe_id}_B"]
        )
        lines = ["Company Name,Industry,Symbol,Series,ISIN Code"]
        lines += [f"{s} Ltd,Test,{s},EQ,INE000000000" for s in symbols]
        (folder / file_name).write_text("\n".join(lines) + "\n")


def test_the_build_script_writes_every_universe_with_the_given_date(
    tmp_path, monkeypatch, capsys
):
    module = load_script()
    write_inputs(tmp_path / "in", module)
    output = tmp_path / "universes.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build",
            "--input",
            str(tmp_path / "in"),
            "--as-of",
            "2026-05-01",
            "--output",
            str(output),
        ],
    )

    module.main()

    written = json.loads(output.read_text())
    assert list(written["universes"]) == EXPECTED_IDS
    assert written["universes"]["NIFTY_50"] == {
        "name": "Nifty 50",
        "as_of": "2026-05-01",
        "symbols": ["NIFTY_50_A", "NIFTY_50_B"],
    }
    assert "NOTE  NIFTY_50 has 2 symbols, its name says 50" in capsys.readouterr().out


def test_the_build_script_cleans_symbols_and_keeps_file_order(tmp_path):
    module = load_script()
    folder = tmp_path / "in"
    write_inputs(folder, module, {"NIFTY_50": [" tcs ", "RELIANCE", "m&m"]})

    result = module.build(folder, date(2026, 5, 1))

    assert result["universes"]["NIFTY_50"]["symbols"] == ["TCS", "RELIANCE", "M&M"]


@pytest.mark.parametrize(
    "symbols, message",
    [(["TCS", "TCS"], "more than once"), (["TCS", " "], "empty symbol")],
)
def test_the_build_script_refuses_bad_input(tmp_path, symbols, message):
    module = load_script()
    folder = tmp_path / "in"
    write_inputs(folder, module, {"NIFTY_50": symbols})

    with pytest.raises(module.UniverseBuildError, match=message):
        module.build(folder, date(2026, 5, 1))


def test_the_build_script_reports_a_missing_file_and_a_missing_column(tmp_path):
    module = load_script()
    folder = tmp_path / "in"
    write_inputs(folder, module)
    (folder / "ind_niftyitlist.csv").unlink()
    with pytest.raises(
        module.UniverseBuildError, match="ind_niftyitlist.csv not found"
    ):
        module.build(folder, date(2026, 5, 1))

    (folder / "ind_niftyitlist.csv").write_text("Company,Ticker\nX,Y\n")
    with pytest.raises(module.UniverseBuildError, match="no Symbol column"):
        module.build(folder, date(2026, 5, 1))


def test_the_build_script_notes_when_indices_stop_nesting(tmp_path):
    module = load_script()
    folder = tmp_path / "in"
    write_inputs(folder, module, {"NIFTY_100": ["ONLY_HERE"]})

    notes = module.check(module.build(folder, date(2026, 5, 1))["universes"])

    assert "NIFTY_50 is not contained in NIFTY_100" in notes


# --- Reading the file --------------------------------------------------------


def test_a_universes_file_with_several_universes_loads_in_order(tmp_path, monkeypatch):
    path = tmp_path / "universes.json"
    path.write_text(
        json.dumps(
            {
                "universes": {
                    "B_LIST": {
                        "name": "B",
                        "as_of": "2026-01-02",
                        "symbols": ["tcs", "TCS", "x"],
                    },
                    "A_LIST": {"as_of": "2026-01-03", "symbols": ["INFY"]},
                }
            }
        )
    )
    monkeypatch.setattr(universes, "UNIVERSES_PATH", path)

    loaded = load_universes()

    assert list(loaded) == ["B_LIST", "A_LIST"]
    assert loaded["B_LIST"].symbols == ("TCS", "X")
    assert loaded["B_LIST"].as_of == date(2026, 1, 2)
    assert loaded["A_LIST"].name == "A_LIST"
