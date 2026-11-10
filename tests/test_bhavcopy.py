"""Tests of bhavcopy reading, folder import and the import-bhavcopy command.

The files are small synthetic ones that follow the real exchange layout; no
real market data is committed.
"""

import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from indian_stock_market_mcp import bhavcopy, cli, data
from indian_stock_market_mcp.bhavcopy import (
    EXCHANGE_METADATA_KEY,
    BhavcopyError,
    find_bhavcopy_files,
    import_bhavcopy_folder,
    read_bhavcopy,
)

# The real common layout, so the reader is tested against what it will receive.
HEADER = [
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN",
    "TckrSymb", "SctySrs", "XpryDt", "FininstrmActlXpryDt", "StrkPric", "OptnTp",
    "FinInstrmNm", "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric",
    "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst", "ChngInOpnIntrst",
    "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty",
    "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
]  # fmt: skip


def row(exchange, symbol, series, close=100.0, *, code="1", isin="INE000A01010",
        date="2026-03-02", volume=1000):  # fmt: skip
    values = dict.fromkeys(HEADER, "")
    values.update(
        TradDt=date, BizDt=date, Sgmt="CM", Src=exchange, FinInstrmTp="STK",
        FinInstrmId=code, ISIN=isin, TckrSymb=symbol, SctySrs=series,
        FinInstrmNm=f"{symbol} LTD", OpnPric=close, HghPric=close, LwPric=close,
        ClsPric=close, LastPric=close, PrvsClsgPric=close, TtlTradgVol=volume,
    )  # fmt: skip
    return values


def write_csv(path: Path, rows) -> Path:
    pd.DataFrame(rows, columns=HEADER).to_csv(path, index=False)
    return path


def write_zip(path: Path, rows) -> Path:
    inner = path.name.removesuffix(".zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(inner, pd.DataFrame(rows, columns=HEADER).to_csv(index=False))
    return path


def nse_day(date, closes=(100.0, 200.0)):
    return [
        row("NSE", "AAA", "EQ", closes[0], date=date),
        row("NSE", "BBB", "BE", closes[1], date=date),
    ]


def bse_day(date):
    return [row("BSE", "AAA", "A", 100.0, code="500001", date=date)]


def name(exchange, date, suffix):
    return f"BhavCopy_{exchange}_CM_0_0_0_{date.replace('-', '')}_F_0000{suffix}"


# --- Reading one file --------------------------------------------------------


@pytest.mark.parametrize(
    "writer, suffix", [(write_csv, ".csv"), (write_zip, ".csv.zip")]
)
def test_nse_file_is_read_into_the_standard_columns(tmp_path, writer, suffix):
    rows = [
        *nse_day("2026-03-02"),
        row("NSE", "GILT", "GS", 50.0),
        row("NSE", "SMEX", "SM", 60.0),
    ]
    path = writer(tmp_path / name("NSE", "2026-03-02", suffix), rows)

    result = read_bhavcopy(path)

    assert result.exchange == "NSE"
    assert list(result.data.columns) == bhavcopy.STANDARD_COLUMNS
    assert result.data["symbol"].tolist() == ["AAA", "BBB"]
    assert result.data["close"].tolist() == [100.0, 200.0]
    assert result.data["date"].iloc[0] == pd.Timestamp("2026-03-02")
    assert (result.rows_read, result.rows_dropped) == (4, 2)
    assert result.unmapped_bse_rows == 0


def test_bse_file_keeps_equity_groups_and_drops_the_rest(tmp_path):
    kept = ["A", "B", "T", "X", "XT"]
    dropped = ["Z", "M", "MT", "F", "G", "E", "IF"]
    rows = [row("BSE", f"K{g}", g, code=f"{i}") for i, g in enumerate(kept)]
    rows += [row("BSE", f"D{g}", g, code=f"9{i}") for i, g in enumerate(dropped)]
    path = write_csv(tmp_path / name("BSE", "2026-03-02", ".CSV"), rows)

    result = read_bhavcopy(path)

    assert result.exchange == "BSE"
    assert result.data["symbol"].tolist() == [f"K{g}" for g in kept]
    assert result.rows_dropped == len(dropped)


def test_bse_codes_in_the_mapping_become_nse_symbols_and_the_rest_are_counted(tmp_path):
    rows = [
        row("BSE", "RELIND", "A", code="500325"),
        row("BSE", "otherco", "B", code="999999"),
    ]
    path = write_csv(tmp_path / name("BSE", "2026-03-02", ".CSV"), rows)

    result = read_bhavcopy(path)

    assert result.data["symbol"].tolist() == ["RELIANCE", "OTHERCO"]
    assert result.unmapped_bse_rows == 1


def test_etfs_in_equity_series_are_kept(tmp_path):
    rows = [row("NSE", "GOLDETF", "EQ", isin="INF000A01010")]
    path = write_csv(tmp_path / name("NSE", "2026-03-02", ".csv"), rows)

    assert read_bhavcopy(path).data["symbol"].tolist() == ["GOLDETF"]


def test_the_exchange_comes_from_the_file_not_its_name(tmp_path):
    path = write_csv(
        tmp_path / name("BSE", "2026-03-02", ".CSV"), nse_day("2026-03-02")
    )

    assert read_bhavcopy(path).exchange == "NSE"


def make_file(tmp_path, rows):
    return write_csv(tmp_path / name("NSE", "2026-03-02", ".csv"), rows)


@pytest.mark.parametrize(
    "rows, message",
    [
        ([], "no rows"),
        ([row("MCX", "AAA", "EQ")], "unsupported exchange"),
        (
            [row("NSE", "AAA", "EQ"), row("BSE", "BBB", "A")],
            "unsupported exchange",
        ),
        (
            [row("NSE", "AAA", "EQ"), row("NSE", "BBB", "EQ", date="2026-03-03")],
            "single trading date",
        ),
        ([row("NSE", "AAA", "EQ", close="abc")], "not a valid date or number"),
    ],
)
def test_unusable_files_are_rejected_with_a_clear_message(tmp_path, rows, message):
    with pytest.raises(BhavcopyError, match=message):
        read_bhavcopy(make_file(tmp_path, rows))


def test_a_file_without_the_expected_columns_is_rejected(tmp_path):
    path = tmp_path / "BhavCopy_old.csv"
    path.write_text("SYMBOL,SERIES,CLOSE\nAAA,EQ,10\n")

    with pytest.raises(BhavcopyError, match="not a supported bhavcopy"):
        read_bhavcopy(path)


def test_a_broken_or_ambiguous_zip_is_rejected(tmp_path):
    broken = tmp_path / "BhavCopy_broken.csv.zip"
    broken.write_text("not a zip")
    two = tmp_path / "BhavCopy_two.csv.zip"
    with zipfile.ZipFile(two, "w") as archive:
        archive.writestr("a.csv", "x")
        archive.writestr("b.csv", "y")

    with pytest.raises(BhavcopyError, match="could not be read"):
        read_bhavcopy(broken)
    with pytest.raises(BhavcopyError, match="exactly one"):
        read_bhavcopy(two)


# --- Finding the files -------------------------------------------------------


def test_only_bhavcopy_files_directly_in_the_folder_are_found(tmp_path):
    write_csv(tmp_path / name("NSE", "2026-03-02", ".csv"), nse_day("2026-03-02"))
    write_zip(tmp_path / name("NSE", "2026-03-03", ".csv.zip"), nse_day("2026-03-03"))
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "other.csv").write_text("x")
    (tmp_path / "sub").mkdir()
    write_csv(
        tmp_path / "sub" / name("NSE", "2026-03-04", ".csv"), nse_day("2026-03-04")
    )

    files, ignored = find_bhavcopy_files(tmp_path)

    assert [f.name for f in files] == [
        name("NSE", "2026-03-02", ".csv"),
        name("NSE", "2026-03-03", ".csv.zip"),
    ]
    assert ignored == 2


def test_a_missing_folder_or_an_empty_one_is_an_error(tmp_path):
    with pytest.raises(BhavcopyError, match="not a folder"):
        find_bhavcopy_files(tmp_path / "missing")
    with pytest.raises(BhavcopyError, match="No bhavcopy files"):
        import_bhavcopy_folder(tmp_path, tmp_path / "out.parquet")


# --- Importing a folder ------------------------------------------------------


def nse_folder(tmp_path, dates=("2026-03-04", "2026-03-02", "2026-03-03")):
    folder = tmp_path / "files"
    folder.mkdir(exist_ok=True)
    for date in dates:
        write_zip(folder / name("NSE", date, ".csv.zip"), nse_day(date))
    return folder


def test_a_folder_is_merged_into_one_parquet_in_date_order(tmp_path):
    output = tmp_path / "out" / "nse.parquet"

    summary = import_bhavcopy_folder(nse_folder(tmp_path), output)

    merged = pd.read_parquet(output)
    assert merged["date"].is_monotonic_increasing
    assert merged["date"].dt.strftime("%Y-%m-%d").unique().tolist() == [
        "2026-03-02",
        "2026-03-03",
        "2026-03-04",
    ]
    assert len(merged) == 6
    assert (summary.exchange, summary.files_read, summary.files_skipped) == (
        "NSE",
        3,
        0,
    )
    assert (summary.rows_read, summary.rows_kept, summary.rows_dropped) == (6, 6, 0)
    assert (summary.total_rows, summary.symbol_count) == (6, 2)
    assert summary.first_date == pd.Timestamp("2026-03-02")
    assert summary.last_date == pd.Timestamp("2026-03-04")
    assert pq.read_schema(output).metadata[EXCHANGE_METADATA_KEY] == b"NSE"


def test_the_output_is_a_file_the_server_can_load(tmp_path, monkeypatch):
    output = tmp_path / "nse.parquet"
    import_bhavcopy_folder(nse_folder(tmp_path), output)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(output))

    assert data.get_available_universe() == ["AAA", "BBB"]
    assert len(data.get_recent_price_history("AAA", 3)) == 3
    assert data.diagnose_dataset()["errors"] == []


def test_rerunning_adds_only_new_days(tmp_path):
    folder = nse_folder(tmp_path, dates=("2026-03-02", "2026-03-03"))
    output = tmp_path / "nse.parquet"
    import_bhavcopy_folder(folder, output)
    before = pd.read_parquet(output)

    again = import_bhavcopy_folder(folder, output)
    assert (again.files_read, again.files_skipped, again.rows_kept) == (0, 2, 0)
    assert pd.read_parquet(output).equals(before)

    write_zip(folder / name("NSE", "2026-03-04", ".csv.zip"), nse_day("2026-03-04"))
    extended = import_bhavcopy_folder(folder, output)
    assert (extended.files_read, extended.files_skipped) == (1, 2)
    assert (extended.rows_kept, extended.total_rows) == (2, 6)


def test_two_files_for_the_same_date_are_imported_once(tmp_path):
    folder = tmp_path / "files"
    folder.mkdir()
    write_csv(folder / name("NSE", "2026-03-02", ".csv"), nse_day("2026-03-02"))
    write_zip(folder / name("NSE", "2026-03-02", ".csv.zip"), nse_day("2026-03-02"))

    summary = import_bhavcopy_folder(folder, tmp_path / "out.parquet")

    assert (summary.files_read, summary.files_skipped, summary.total_rows) == (1, 1, 2)


def test_dropped_rows_and_bse_ticker_use_are_counted_across_files(tmp_path):
    folder = tmp_path / "files"
    folder.mkdir()
    for date in ("2026-03-02", "2026-03-03"):
        rows = [*bse_day(date), row("BSE", "DEBT", "F", code="777", date=date)]
        write_csv(folder / name("BSE", date, ".CSV"), rows)

    summary = import_bhavcopy_folder(folder, tmp_path / "bse.parquet")

    assert summary.exchange == "BSE"
    assert (summary.rows_read, summary.rows_kept, summary.rows_dropped) == (4, 2, 2)
    assert summary.unmapped_bse_rows == 2


def test_nothing_is_written_when_exchanges_are_mixed(tmp_path):
    folder = tmp_path / "files"
    folder.mkdir()
    write_csv(folder / name("NSE", "2026-03-02", ".csv"), nse_day("2026-03-02"))
    write_csv(folder / name("BSE", "2026-03-03", ".CSV"), bse_day("2026-03-03"))
    output = tmp_path / "out.parquet"

    with pytest.raises(BhavcopyError, match="one exchange per output"):
        import_bhavcopy_folder(folder, output)

    assert not output.exists()


def test_an_output_of_another_exchange_or_origin_is_never_changed(tmp_path):
    nse_output = tmp_path / "nse.parquet"
    import_bhavcopy_folder(nse_folder(tmp_path), nse_output)
    before = nse_output.read_bytes()
    bse_folder = tmp_path / "bse"
    bse_folder.mkdir()
    write_csv(bse_folder / name("BSE", "2026-03-09", ".CSV"), bse_day("2026-03-09"))
    foreign = tmp_path / "mine.parquet"
    pd.read_parquet(nse_output).to_parquet(foreign)  # no importer metadata

    with pytest.raises(BhavcopyError, match="but this import is NSE"):
        import_bhavcopy_folder(bse_folder, nse_output)
    with pytest.raises(BhavcopyError, match="not created by import-bhavcopy"):
        import_bhavcopy_folder(nse_folder(tmp_path), foreign)

    assert nse_output.read_bytes() == before


def test_data_the_server_would_reject_is_not_written(tmp_path):
    folder = tmp_path / "files"
    folder.mkdir()
    rows = [row("NSE", "AAA", "EQ", 100.0), row("NSE", "BAD", "EQ", 0.0)]
    write_csv(folder / name("NSE", "2026-03-02", ".csv"), rows)
    output = tmp_path / "out.parquet"

    with pytest.raises(BhavcopyError, match="non-positive"):
        import_bhavcopy_folder(folder, output)

    assert not output.exists()


def test_a_failed_write_leaves_neither_a_file_nor_a_temp_file(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bhavcopy.pq, "write_table", fail)
    output = tmp_path / "out" / "nse.parquet"

    with pytest.raises(OSError):
        import_bhavcopy_folder(nse_folder(tmp_path), output)

    assert list(output.parent.iterdir()) == []


# --- The command -------------------------------------------------------------


def test_the_command_imports_and_prints_a_summary(tmp_path, capsys):
    folder = nse_folder(tmp_path)
    output = tmp_path / "nse.parquet"

    cli.main(["import-bhavcopy", str(folder), "--output", str(output)])

    printed = capsys.readouterr().out
    assert output.exists()
    for expected in (
        "Exchange:        NSE",
        "Files read:      3",
        "Files skipped:   0",
        "Rows kept:       6",
        "Date range:      2026-03-02 to 2026-03-04",
        "Symbols:         2",
    ):
        assert expected in printed
    assert "BSE own ticker" not in printed


def test_the_summary_reports_bse_ticker_use_only_for_bse(tmp_path, capsys):
    folder = tmp_path / "files"
    folder.mkdir()
    write_csv(folder / name("BSE", "2026-03-02", ".CSV"), bse_day("2026-03-02"))

    cli.main(["import-bhavcopy", str(folder), "--output", str(tmp_path / "b.parquet")])

    assert "BSE own ticker:  1" in capsys.readouterr().out


def test_the_command_exits_with_a_message_on_an_import_error(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["import-bhavcopy", str(tmp_path / "missing")])

    assert "not a folder" in str(stopped.value)


def test_without_arguments_the_command_starts_the_server(monkeypatch):
    from indian_stock_market_mcp import server

    calls = []
    monkeypatch.setattr(server, "main", lambda: calls.append("served"))

    cli.main([])

    assert calls == ["served"]
