"""Tests of saved watchlists: the file, the tools and using one as a universe."""

import asyncio
import json
import os
from datetime import date
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from indian_stock_market_mcp import data, universes, watchlists
from indian_stock_market_mcp import server as server_module
from indian_stock_market_mcp.watchlists import (
    WATCHLIST_PATH_ENV,
    UnknownUniverseError,
    WatchlistError,
    create_watchlist,
    delete_watchlist,
    get_watchlist,
    load_watchlists,
    resolve_selection,
    update_watchlist,
)

SAMPLE_CSV = (
    Path(__file__).parents[1] / "data" / "sample_equity_daily.csv"
)  # RELIANCE, TCS, INFY


@pytest.fixture
def sample_dataset(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(SAMPLE_CSV))


@pytest.fixture
def no_dataset(monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", "")
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)


@pytest.fixture(autouse=True)
def fixed_date(monkeypatch):
    monkeypatch.setattr(watchlists, "today", lambda: date(2026, 11, 11))


def call_envelope(name, arguments=None):
    result = asyncio.run(server_module.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def codes(envelope):
    return [w["code"] for w in envelope["warnings"]]


# --- Where the file lives ----------------------------------------------------


def test_the_default_location_is_in_the_home_folder(monkeypatch):
    monkeypatch.delenv(WATCHLIST_PATH_ENV)

    assert watchlists.get_watchlist_path() == (
        Path.home() / ".indian-stock-market-mcp" / "watchlists.json"
    )


def test_the_environment_setting_chooses_the_file_and_expands_the_home_folder(
    monkeypatch,
):
    monkeypatch.setenv(WATCHLIST_PATH_ENV, "~/elsewhere/lists.json")

    assert watchlists.get_watchlist_path() == Path.home() / "elsewhere" / "lists.json"


def test_nothing_is_written_until_a_watchlist_is_saved(tmp_path, monkeypatch):
    path = tmp_path / "deep" / "folder" / "lists.json"
    monkeypatch.setenv(WATCHLIST_PATH_ENV, str(path))

    assert load_watchlists() == {}
    assert not path.exists() and not path.parent.exists()

    create_watchlist("mine", ["TCS"])
    assert path.is_file()


# --- Saving and reloading ----------------------------------------------------


def test_a_saved_watchlist_is_reloaded_with_everything_kept():
    created = create_watchlist("My Banks", ["HDFCBANK", "ICICIBANK", "SBIN"])

    reloaded = get_watchlist("My Banks")

    assert reloaded == created
    assert (reloaded.id, reloaded.name) == ("MY_BANKS", "My Banks")
    assert reloaded.symbols == ("HDFCBANK", "ICICIBANK", "SBIN")
    assert reloaded.created == reloaded.updated == date(2026, 11, 11)


def test_the_file_holds_plain_readable_json():
    create_watchlist("My Banks", ["TCS"])

    content = json.loads(watchlists.get_watchlist_path().read_text())

    assert content == {
        "watchlists": {
            "MY_BANKS": {
                "name": "My Banks",
                "symbols": ["TCS"],
                "created": "2026-11-11",
                "updated": "2026-11-11",
            }
        }
    }


def test_every_symbol_is_cleaned_and_repeats_are_dropped_in_order():
    saved = create_watchlist(
        "mine", ["tcs.ns", " NSE:infy ", "TCS", "BSE:RELIANCE.BO", "M&M"]
    )

    assert saved.symbols == ("TCS", "INFY", "RELIANCE", "M&M")


@pytest.mark.parametrize(
    "lookup", ["my banks", "MY BANKS", "My-Banks", "my_banks", " My Banks "]
)
def test_a_watchlist_is_found_whatever_the_spelling_of_its_name(lookup):
    create_watchlist("My Banks", ["TCS"])

    assert get_watchlist(lookup).name == "My Banks"


def test_several_watchlists_are_kept_side_by_side():
    create_watchlist("one", ["TCS"])
    create_watchlist("two", ["INFY"])

    assert list(load_watchlists()) == ["ONE", "TWO"]


# --- Names and limits --------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["", "   ", None, 5, "-leading", "has/slash", "x" * 41, "a.b"]
)
def test_unusable_names_are_refused(name):
    with pytest.raises(WatchlistError):
        create_watchlist(name, ["TCS"])


@pytest.mark.parametrize(
    "name",
    [
        "NIFTY_50",
        "nifty 50",
        "Nifty-500",
        "NIFTY500",
        "bank nifty",
        "BANKNIFTY",
        "nifty it",
    ],
)
def test_universe_names_cannot_be_used_for_a_watchlist(name):
    with pytest.raises(WatchlistError, match="name of a bundled universe"):
        create_watchlist(name, ["TCS"])

    assert load_watchlists() == {}


def test_a_name_that_only_resembles_a_universe_is_fine():
    assert create_watchlist("nifty 50 favourites", ["TCS"]).id == "NIFTY_50_FAVOURITES"


def test_a_name_already_taken_is_refused_in_any_spelling():
    create_watchlist("My Banks", ["TCS"])

    with pytest.raises(WatchlistError, match="already exists"):
        create_watchlist("my-banks", ["INFY"])

    assert get_watchlist("My Banks").symbols == ("TCS",)


@pytest.mark.parametrize("symbols", [[], ["", "TCS"], ["TCS", 5], "TCS", None])
def test_a_watchlist_needs_a_list_of_valid_symbols(symbols):
    with pytest.raises(WatchlistError):
        create_watchlist("mine", symbols)

    assert load_watchlists() == {}


def test_the_size_limits_are_enforced(monkeypatch):
    monkeypatch.setattr(watchlists, "MAX_SYMBOLS", 2)
    monkeypatch.setattr(watchlists, "MAX_WATCHLISTS", 1)

    with pytest.raises(WatchlistError, match="at most 2 symbols"):
        create_watchlist("big", ["A", "B", "C"])
    create_watchlist("first", ["A", "B"])
    with pytest.raises(WatchlistError, match="At most 1 watchlists"):
        create_watchlist("second", ["A"])


# --- Viewing -----------------------------------------------------------------


def test_an_unknown_watchlist_names_the_saved_ones():
    create_watchlist("one", ["TCS"])

    with pytest.raises(
        WatchlistError, match="Unknown watchlist 'nope'. Saved watchlists: ONE"
    ):
        get_watchlist("nope")


def test_the_error_for_an_unknown_watchlist_says_when_none_are_saved():
    with pytest.raises(WatchlistError, match="none saved"):
        get_watchlist("nope")


# --- Updating ----------------------------------------------------------------


def test_symbols_are_added_with_cleanup_and_without_repeats(monkeypatch):
    create_watchlist("mine", ["TCS", "INFY"])

    saved, added, not_found = update_watchlist(
        "mine", add_symbols=["infy.ns", "reliance", "RELIANCE"]
    )

    assert saved.symbols == ("TCS", "INFY", "RELIANCE")
    assert (added, not_found) == (["RELIANCE"], [])


def test_symbols_are_removed_and_missing_ones_are_reported():
    create_watchlist("mine", ["TCS", "INFY", "SBIN"])

    saved, added, not_found = update_watchlist("mine", remove_symbols=["sbin", "NOPE"])

    assert saved.symbols == ("TCS", "INFY")
    assert (added, not_found) == ([], ["NOPE"])


def test_a_rename_keeps_the_symbols_and_the_created_date(monkeypatch):
    create_watchlist("old name", ["TCS"])
    monkeypatch.setattr(watchlists, "today", lambda: date(2026, 12, 1))

    saved, _, _ = update_watchlist("old name", new_name="New Name")

    assert (saved.name, saved.id, saved.symbols) == ("New Name", "NEW_NAME", ("TCS",))
    assert (saved.created, saved.updated) == (date(2026, 11, 11), date(2026, 12, 1))
    assert list(load_watchlists()) == ["NEW_NAME"]


def test_changing_only_the_capitalisation_of_a_name_is_allowed():
    create_watchlist("my banks", ["TCS"])

    saved, _, _ = update_watchlist("my banks", new_name="My Banks")

    assert (saved.name, saved.id) == ("My Banks", "MY_BANKS")


def test_add_remove_and_rename_work_in_one_call():
    create_watchlist("mine", ["TCS", "INFY"])

    saved, added, _ = update_watchlist(
        "mine", add_symbols=["SBIN"], remove_symbols=["TCS"], new_name="renamed"
    )

    assert saved.symbols == ("INFY", "SBIN")
    assert saved.id == "RENAMED"
    assert added == ["SBIN"]


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({}, "Nothing to update"),
        ({"add_symbols": [], "remove_symbols": []}, "Nothing to update"),
        ({"add_symbols": ["TCS"], "remove_symbols": ["tcs.ns"]}, "same symbol"),
        ({"remove_symbols": ["TCS", "INFY"]}, "at least one symbol"),
        ({"new_name": "nifty 50"}, "name of a bundled universe"),
        ({"new_name": "other"}, "already exists"),
        ({"add_symbols": [""]}, "Invalid symbol"),
    ],
)
def test_a_refused_update_changes_nothing(arguments, message):
    create_watchlist("mine", ["TCS", "INFY"])
    create_watchlist("other", ["SBIN"])
    before = watchlists.get_watchlist_path().read_text()

    with pytest.raises(WatchlistError, match=message):
        update_watchlist("mine", **arguments)

    assert watchlists.get_watchlist_path().read_text() == before


def test_updating_an_unknown_watchlist_is_an_error():
    with pytest.raises(WatchlistError, match="Unknown watchlist"):
        update_watchlist("nope", add_symbols=["TCS"])


# --- Deleting ----------------------------------------------------------------


def test_a_deleted_watchlist_is_gone_and_the_others_stay():
    create_watchlist("one", ["TCS"])
    create_watchlist("two", ["INFY"])

    removed = delete_watchlist("ONE")

    assert removed.name == "one"
    assert list(load_watchlists()) == ["TWO"]
    with pytest.raises(WatchlistError, match="Unknown watchlist"):
        delete_watchlist("one")


# --- A damaged or unwritable file --------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"nope": 1}',
        '{"watchlists": []}',
        '{"watchlists": {"A": {"name": "A"}}}',
        '{"watchlists": {"A": {"name": "A", "symbols": ["X"], "created": "soon", "updated": "2026-01-01"}}}',
    ],
)
def test_a_damaged_file_is_reported_and_never_overwritten(content):
    path = watchlists.get_watchlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

    with pytest.raises(WatchlistError, match="could not be read.*was not changed"):
        create_watchlist("mine", ["TCS"])

    assert path.read_text() == content


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(monkeypatch):
    create_watchlist("mine", ["TCS"])
    path = watchlists.get_watchlist_path()
    before = path.read_text()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        create_watchlist("another", ["INFY"])

    assert path.read_text() == before
    assert [p.name for p in path.parent.iterdir()] == [path.name]


# --- Using a watchlist as a universe -----------------------------------------


def test_a_bundled_universe_wins_and_a_watchlist_is_the_fallback():
    create_watchlist("mine", ["TCS", "INFY"])

    assert resolve_selection("nifty 50").kind == "universe"
    chosen = resolve_selection("MINE")
    assert (chosen.kind, chosen.id, chosen.symbols, chosen.universe) == (
        "watchlist",
        "MINE",
        ("TCS", "INFY"),
        None,
    )


def test_the_default_selection_is_the_nifty_50():
    assert resolve_selection().id == universes.DEFAULT_UNIVERSE


def test_an_unknown_name_lists_both_universes_and_watchlists():
    create_watchlist("mine", ["TCS"])

    with pytest.raises(UnknownUniverseError) as error:
        resolve_selection("nope")

    message = str(error.value)
    assert "Unknown universe or watchlist 'nope'" in message
    assert "NIFTY_50" in message and "Watchlists: MINE" in message


def test_a_damaged_file_does_not_stop_a_bundled_universe_from_resolving():
    path = watchlists.get_watchlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")

    assert resolve_selection("nifty bank").kind == "universe"


# --- Through the MCP tools ---------------------------------------------------


def test_create_reports_the_saved_watchlist_in_the_standard_envelope(sample_dataset):
    envelope = call_envelope(
        "create_watchlist", {"name": "My Picks", "symbols": ["tcs.ns", "INFY"]}
    )

    assert envelope["data"] == {
        "id": "MY_PICKS",
        "name": "My Picks",
        "symbols": ["TCS", "INFY"],
        "symbol_count": 2,
        "created": "2026-11-11",
        "updated": "2026-11-11",
    }
    assert envelope["source"] == {
        "kind": "local_file",
        "file_name": "watchlists.json",
        "format": "json",
    }
    assert envelope["parameters"] == {"name": "My Picks", "symbols": ["TCS", "INFY"]}
    assert envelope["warnings"] == []


def test_the_full_path_of_the_file_is_never_returned(sample_dataset):
    envelope = call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS"]})

    assert str(watchlists.get_watchlist_path().parent) not in json.dumps(envelope)


def test_symbols_missing_from_the_dataset_are_saved_and_reported(sample_dataset):
    envelope = call_envelope(
        "create_watchlist", {"name": "mine", "symbols": ["TCS", "nope", "ZZZ"]}
    )

    assert envelope["data"]["symbols"] == ["TCS", "NOPE", "ZZZ"]
    warning = envelope["warnings"][0]
    assert warning["code"] == "symbol_not_found"
    assert warning["symbols"] == ["NOPE", "ZZZ"]


def test_without_a_dataset_symbols_are_saved_and_the_warning_says_they_were_not_checked(
    no_dataset,
):
    envelope = call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS"]})

    assert envelope["data"]["symbols"] == ["TCS"]
    assert codes(envelope) == ["symbols_unchecked"]


def test_a_saved_watchlist_survives_a_new_call_and_is_viewable(sample_dataset):
    call_envelope("create_watchlist", {"name": "one", "symbols": ["TCS"]})
    call_envelope("create_watchlist", {"name": "two", "symbols": ["INFY", "NOPE"]})

    everything = call_envelope("get_watchlists")
    single = call_envelope("get_watchlists", {"name": "TWO"})

    assert [w["id"] for w in everything["data"]["watchlists"]] == ["ONE", "TWO"]
    assert [w["symbols"] for w in single["data"]["watchlists"]] == [["INFY", "NOPE"]]
    assert single["parameters"] == {"name": "TWO"}
    assert everything["parameters"] == {}
    assert everything["warnings"][0]["symbols"] == ["NOPE"]


def test_viewing_does_not_warn_about_an_unreadable_dataset(no_dataset):
    call_envelope("create_watchlist", {"name": "one", "symbols": ["TCS"]})

    assert call_envelope("get_watchlists")["warnings"] == []


def test_viewing_with_nothing_saved_returns_an_empty_list(sample_dataset):
    envelope = call_envelope("get_watchlists")

    assert envelope["data"] == {"watchlists": []}
    assert envelope["warnings"] == []


def test_update_reports_what_changed_and_warns_only_about_added_symbols(sample_dataset):
    call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS", "OLDBAD"]})

    envelope = call_envelope(
        "update_watchlist",
        {
            "name": "mine",
            "add_symbols": ["infy", "newbad"],
            "remove_symbols": ["tcs", "zzz"],
        },
    )

    assert envelope["data"]["symbols"] == ["OLDBAD", "INFY", "NEWBAD"]
    assert envelope["data"]["added"] == ["INFY", "NEWBAD"]
    assert envelope["data"]["not_in_watchlist"] == ["ZZZ"]
    assert [w["symbols"] for w in envelope["warnings"]] == [["NEWBAD"]]
    assert envelope["parameters"]["new_name"] is None


def test_delete_confirms_what_was_removed(sample_dataset):
    call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS", "INFY"]})

    envelope = call_envelope("delete_watchlist", {"name": "mine"})

    assert envelope["data"]["deleted"] is True
    assert envelope["data"]["symbols"] == ["TCS", "INFY"]
    assert call_envelope("get_watchlists")["data"]["watchlists"] == []


@pytest.mark.parametrize(
    "tool, arguments, message",
    [
        (
            "create_watchlist",
            {"name": "nifty 50", "symbols": ["TCS"]},
            "name of a bundled universe",
        ),
        ("create_watchlist", {"name": "mine", "symbols": []}, "at least one symbol"),
        ("get_watchlists", {"name": "nope"}, "Unknown watchlist"),
        (
            "update_watchlist",
            {"name": "nope", "add_symbols": ["TCS"]},
            "Unknown watchlist",
        ),
        ("update_watchlist", {"name": "nope"}, "Nothing to update"),
        ("delete_watchlist", {"name": "nope"}, "Unknown watchlist"),
    ],
)
def test_the_tools_fail_with_a_clear_message(sample_dataset, tool, arguments, message):
    with pytest.raises(ToolError, match=message):
        call_envelope(tool, arguments)


# --- Ranking a watchlist -----------------------------------------------------


def test_a_watchlist_can_be_ranked_by_name(sample_dataset):
    call_envelope(
        "create_watchlist", {"name": "My Picks", "symbols": ["TCS", "INFY", "nope"]}
    )

    envelope = call_envelope(
        "rank_weekly_performers", {"universe": "my picks", "top_n": 5}
    )

    assert envelope["data"]["universe"] == "MY_PICKS"
    assert envelope["parameters"] == {"top_n": 5, "universe": "MY_PICKS"}
    assert sorted(row["symbol"] for row in envelope["data"]["rankings"]) == [
        "INFY",
        "TCS",
    ]
    assert [item["symbol"] for item in envelope["data"]["skipped"]] == ["NOPE"]
    assert "symbol_not_found" in codes(envelope)


def test_a_watchlist_is_never_reported_as_an_outdated_universe(
    sample_dataset, monkeypatch
):
    call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS", "INFY"]})
    monkeypatch.setattr(universes, "today", lambda: date(2099, 1, 1))

    envelope = call_envelope("rank_weekly_performers", {"universe": "mine"})

    assert "universe_outdated" not in codes(envelope)


def test_a_universe_name_still_beats_a_watchlist_in_ranking(sample_dataset):
    call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS"]})

    assert call_envelope("rank_weekly_performers")["data"]["universe"] == "NIFTY_50"


def test_ranking_an_unknown_name_lists_the_watchlists(sample_dataset):
    call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS"]})

    with pytest.raises(ToolError, match="Watchlists: MINE"):
        call_envelope("rank_weekly_performers", {"universe": "nope"})


def test_a_deleted_watchlist_can_no_longer_be_ranked(sample_dataset):
    call_envelope("create_watchlist", {"name": "mine", "symbols": ["TCS"]})
    call_envelope("delete_watchlist", {"name": "mine"})

    with pytest.raises(ToolError, match="Unknown universe or watchlist 'mine'"):
        call_envelope("rank_weekly_performers", {"universe": "mine"})
