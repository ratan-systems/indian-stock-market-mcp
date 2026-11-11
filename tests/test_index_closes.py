"""Tests of NSE index-close reading, importing and loading."""

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from indian_stock_market_mcp import cli, data, index_closes
from indian_stock_market_mcp.index_closes import (
    INDEX_PATH_ENV,
    IndexCloseError,
    index_key,
    load_index_closes,
)

HEADER = (
    "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
    "Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),"
    "P/E,P/B,Div Yield"
)


def day_file(folder: Path, day: str, rows=None, name=None) -> Path:
    """Write a file in NSE's layout. `day` is DD-MM-YYYY."""
    rows = rows or [
        ("Nifty 50", 24899.5, 24919.65, 24689.6, 24712.05),
        ("NIFTY Midcap 100", 57000.0, 57100.0, 56500.0, 56700.5),
    ]
    lines = [HEADER]
    for index, open_, high, low, close in rows:
        lines.append(
            f"{index},{day},{open_},{high},{low},{close},-255.7,-1.02,1,1,21.7,3.3,1.3"
        )
    path = folder / (name or f"ind_close_all_{day.replace('-', '')}.csv")
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "files"
    path.mkdir()
    return path


# --- Reading one file --------------------------------------------------------------


def test_a_file_is_read_into_date_index_and_ohlc(folder):
    frame = index_closes.read_index_close_file(day_file(folder, "26-08-2025"))

    assert list(frame.columns) == ["date", "index", "open", "high", "low", "close"]
    assert frame["index"].tolist() == ["Nifty 50", "NIFTY Midcap 100"]
    assert frame["date"].iloc[0] == pd.Timestamp("2025-08-26")
    assert frame["close"].tolist() == [24712.05, 56700.5]
    assert frame["open"].iloc[0] == 24899.5


def test_dashes_for_missing_values_are_tolerated_but_a_missing_close_drops_the_row(
    folder,
):
    path = folder / "ind_close_all_26082025.csv"
    path.write_text(
        HEADER
        + "\nNifty50 Dividend Points,26-08-2025,-,-,-,206.33,0.0,-,-,-,-,-,-"
        + "\nIndia VIX,26-08-2025,11.7,12.5,10.9,12.19,0.44,3.7,-,-,-,-,-"
        + "\nNo Close Index,26-08-2025,1,2,0.5,-,0,0,-,-,-,-,-\n"
    )

    frame = index_closes.read_index_close_file(path)

    assert frame["index"].tolist() == ["Nifty50 Dividend Points", "India VIX"]
    assert pd.isna(frame["open"].iloc[0])
    assert frame["close"].iloc[0] == 206.33


def test_names_are_stripped(folder):
    path = day_file(folder, "26-08-2025", rows=[("  Nifty 50  ", 1, 2, 0.5, 1.5)])

    assert index_closes.read_index_close_file(path)["index"].tolist() == ["Nifty 50"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("a,b\n1,2\n", "not an NSE index-close file"),
        (
            HEADER + "\nNifty 50,26-08-2025,1,2,0.5,1.5,0,0,0,0,0,0,0\n"
            "Nifty 100,27-08-2025,1,2,0.5,1.5,0,0,0,0,0,0,0\n",
            "more than one date",
        ),
        (
            HEADER + "\nNifty 50,not-a-date,1,2,0.5,1.5,0,0,0,0,0,0,0\n",
            "unreadable date",
        ),
        (
            HEADER + "\nNifty 50,26-08-2025,1,2,0.5,-,0,0,0,0,0,0,0\n",
            "no usable index closes",
        ),
        (
            HEADER + "\nNifty 50,26-08-2025,1,2,0.5,0,0,0,0,0,0,0,0\n",
            "no usable index closes",
        ),
    ],
)
def test_unusable_files_are_rejected_with_a_clear_message(folder, content, message):
    path = folder / "ind_close_all_26082025.csv"
    path.write_text(content)

    with pytest.raises(IndexCloseError, match=message):
        index_closes.read_index_close_file(path)


def test_a_missing_file_is_an_error(folder):
    with pytest.raises(IndexCloseError, match="could not be read"):
        index_closes.read_index_close_file(folder / "nope.csv")


def test_index_names_match_regardless_of_case_and_spacing():
    assert index_key("NIFTY Midcap  100") == index_key("Nifty Midcap 100")


# --- Importing a folder -------------------------------------------------------------


def test_a_folder_is_merged_into_one_parquet_sorted_by_index_and_date(folder, tmp_path):
    day_file(folder, "27-08-2025")
    day_file(folder, "26-08-2025")
    output = tmp_path / "out" / "index_closes.parquet"

    summary = index_closes.import_index_folder(folder, output)

    stored = pd.read_parquet(output)
    assert list(stored["index"]) == ["NIFTY Midcap 100"] * 2 + ["Nifty 50"] * 2
    assert stored["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-08-26",
        "2025-08-27",
        "2025-08-26",
        "2025-08-27",
    ]
    assert summary.files_read == 2
    assert summary.total_rows == 4
    assert summary.index_count == 2
    assert summary.first_date == pd.Timestamp("2025-08-26")
    assert summary.last_date == pd.Timestamp("2025-08-27")
    assert (
        pq.read_schema(output).metadata[index_closes.KIND_METADATA_KEY]
        == b"index_closes"
    )


def test_rerunning_adds_only_new_days(folder, tmp_path):
    output = tmp_path / "index_closes.parquet"
    day_file(folder, "26-08-2025")
    index_closes.import_index_folder(folder, output)
    day_file(folder, "27-08-2025")

    summary = index_closes.import_index_folder(folder, output)

    assert (summary.files_read, summary.files_skipped) == (1, 1)
    assert summary.total_rows == 4


def test_a_rerun_with_nothing_new_changes_nothing(folder, tmp_path):
    output = tmp_path / "index_closes.parquet"
    day_file(folder, "26-08-2025")
    index_closes.import_index_folder(folder, output)
    before = output.read_bytes()

    summary = index_closes.import_index_folder(folder, output)

    assert summary.files_read == 0
    assert output.read_bytes() == before


def test_other_files_are_ignored_and_counted(folder, tmp_path):
    day_file(folder, "26-08-2025")
    (folder / "notes.txt").write_text("hi")
    (folder / "ind_close_all_26082025.xlsx").write_text("x")

    summary = index_closes.import_index_folder(folder, tmp_path / "o.parquet")

    assert (summary.files_read, summary.files_ignored) == (1, 2)


def test_an_empty_or_missing_folder_is_an_error(folder, tmp_path):
    with pytest.raises(IndexCloseError, match="No ind_close_all"):
        index_closes.import_index_folder(folder, tmp_path / "o.parquet")
    with pytest.raises(IndexCloseError, match="not a folder"):
        index_closes.import_index_folder(tmp_path / "missing", tmp_path / "o.parquet")


def test_nothing_is_written_when_one_file_is_bad(folder, tmp_path):
    day_file(folder, "26-08-2025")
    (folder / "ind_close_all_27082025.csv").write_text("a,b\n1,2\n")
    output = tmp_path / "o.parquet"

    with pytest.raises(IndexCloseError):
        index_closes.import_index_folder(folder, output)

    assert not output.exists()


def test_an_output_not_made_by_this_command_is_never_changed(folder, tmp_path):
    day_file(folder, "26-08-2025")
    foreign = tmp_path / "foreign.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(foreign)
    before = foreign.read_bytes()

    with pytest.raises(IndexCloseError, match="not created by import-index-closes"):
        index_closes.import_index_folder(folder, foreign)

    assert foreign.read_bytes() == before


def test_a_failed_write_leaves_neither_a_file_nor_a_temp_file(
    folder, tmp_path, monkeypatch
):
    day_file(folder, "26-08-2025")
    output = tmp_path / "out" / "o.parquet"

    def fail(table, where):
        raise OSError("disk full")

    monkeypatch.setattr(pq, "write_table", fail)

    with pytest.raises(OSError, match="disk full"):
        index_closes.import_index_folder(folder, output)

    assert not output.exists()
    assert list(output.parent.iterdir()) == []


# --- Loading for the server -----------------------------------------------------------


@pytest.fixture
def imported(folder, tmp_path):
    day_file(folder, "26-08-2025")
    day_file(folder, "27-08-2025", rows=[("Nifty 50", 1, 2, 0.5, 24800.0)])
    output = tmp_path / "index_closes.parquet"
    index_closes.import_index_folder(folder, output)
    return output


def test_without_a_configured_file_nothing_is_loaded(monkeypatch):
    monkeypatch.delenv(INDEX_PATH_ENV, raising=False)
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)

    assert load_index_closes() is None


def test_closes_load_as_a_series_per_index_keyed_by_matching_name(
    imported, monkeypatch
):
    monkeypatch.setenv(INDEX_PATH_ENV, str(imported))

    loaded = load_index_closes()

    nifty = loaded[index_key("Nifty 50")]
    assert nifty.index.tolist() == [
        pd.Timestamp("2025-08-26"),
        pd.Timestamp("2025-08-27"),
    ]
    assert nifty.tolist() == [24712.05, 24800.0]
    assert loaded[index_key("nifty midcap 100")].tolist() == [56700.5]


def test_a_configured_file_that_is_missing_or_foreign_is_an_error(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(INDEX_PATH_ENV, str(tmp_path / "missing.parquet"))
    with pytest.raises(IndexCloseError, match="was not found"):
        load_index_closes()

    foreign = tmp_path / "foreign.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(foreign)
    monkeypatch.setenv(INDEX_PATH_ENV, str(foreign))
    with pytest.raises(IndexCloseError, match="not created by import-index-closes"):
        load_index_closes()

    garbage = tmp_path / "garbage.parquet"
    garbage.write_text("not parquet")
    monkeypatch.setenv(INDEX_PATH_ENV, str(garbage))
    with pytest.raises(IndexCloseError, match="could not be read"):
        load_index_closes()


# --- The command ----------------------------------------------------------------------


def test_the_command_imports_and_prints_a_summary(folder, tmp_path, capsys):
    day_file(folder, "26-08-2025")
    output = tmp_path / "index_closes.parquet"

    cli.main(["import-index-closes", str(folder), "--output", str(output)])

    printed = capsys.readouterr().out
    assert output.exists()
    for expected in (
        "Files read:      1",
        "Indices:         2",
        "2025-08-26 to 2025-08-26",
    ):
        assert expected in printed
    assert "INDIAN_STOCK_INDEX_PATH" in printed


def test_the_command_exits_with_a_message_on_an_error(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-index-closes", str(tmp_path / "missing")])

    assert "not a folder" in str(stopped.value)
