import json
from pathlib import Path

import pandas as pd
import pytest

from indian_stock_market_mcp import data
from indian_stock_market_mcp.data import (
    DATA_ISSUE_MESSAGES,
    MAX_LISTED_THIN_SYMBOLS,
    WEEKLY_SESSIONS,
    collect_data_issues,
    diagnose_dataset,
    get_available_universe,
    get_nifty50_universe,
    get_recent_price_history,
    get_weekly_performance,
    inspect_source,
    load_symbol_data,
    rank_weekly_performers,
    validate_price_data,
    validate_ticker,
)


def test_loads_ohlc_data_without_volume(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", "2026-07-23"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1271.0, 1265.0],
            "high": [1284.0, 1275.0],
            "low": [1268.0, 1258.0],
            "close": [1278.0, 1272.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    result = load_symbol_data("reliance")

    assert len(result) == 2
    assert result["symbol"].tolist() == ["RELIANCE", "RELIANCE"]
    assert result["date"].is_monotonic_increasing
    assert "volume" in result.columns
    assert result["volume"].isna().all()


def test_load_symbol_data_normalizes_mixed_case_parquet_symbol(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", "2026-07-23"],
            "symbol": [" reliance ", " reliance "],
            "open": [1271.0, 1265.0],
            "high": [1284.0, 1275.0],
            "low": [1268.0, 1258.0],
            "close": [1278.0, 1272.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    result = load_symbol_data("reliance")

    assert len(result) == 2
    assert result["symbol"].tolist() == ["RELIANCE", "RELIANCE"]


def test_missing_data_file_raises_error(tmp_path, monkeypatch):
    missing_path = tmp_path / "missing.parquet"
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(missing_path))

    with pytest.raises(
        FileNotFoundError,
        match="Configured market-data file was not found",
    ):
        load_symbol_data("RELIANCE")


def test_missing_close_raises_error(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", "2026-07-23"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1271.0, 1265.0],
            "high": [1284.0, 1275.0],
            "low": [1268.0, 1258.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(ValueError, match=r"Missing required column\(s\): \['close'\]"):
        load_symbol_data("reliance")


def test_invalid_symbol_raises_error(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", "2026-07-23"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1271.0, 1265.0],
            "high": [1284.0, 1275.0],
            "low": [1268.0, 1258.0],
            "close": [1278.0, 1272.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(ValueError, match="NOTREAL"):
        load_symbol_data("notreal")


@pytest.mark.parametrize("sessions", [0, -1, 101, "5", None])
def test_get_recent_price_history_rejects_invalid_sessions(sessions):
    with pytest.raises(
        ValueError,
        match="session must be an integer between 1 and 100",
    ):
        get_recent_price_history("RELIANCE", sessions=sessions)


def test_load_symbol_data_rejects_duplicate_dates(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1271.0, 1272.0],
            "high": [1284.0, 1285.0],
            "low": [1268.0, 1269.0],
            "close": [1278.0, 1279.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(ValueError, match="duplicate symbol-date records"):
        load_symbol_data("RELIANCE")


def test_weekly_performance_calculates_five_session_return(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ],
            "symbol": ["RELIANCE"] * 5,
            "open": [100.0, 102.0, 104.0, 106.0, 108.0],
            "high": [101.0, 103.0, 105.0, 107.0, 111.0],
            "low": [99.0, 101.0, 103.0, 105.0, 107.0],
            "close": [100.0, 102.0, 104.0, 106.0, 110.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    result = get_weekly_performance("reliance")

    assert result["symbol"] == "RELIANCE"
    assert result["session_count"] == 5
    assert result["start_close"] == 100.0
    assert result["end_close"] == 110.0
    assert result["return_percent"] == pytest.approx(10.0)


def test_weekly_performance_requires_five_sessions(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-22", "2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE"] * 3,
            "open": [100.0, 102.0, 104.0],
            "high": [101.0, 103.0, 105.0],
            "low": [99.0, 101.0, 103.0],
            "close": [100.0, 102.0, 104.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(
        ValueError,
        match="Not enough data: need 5 sessions to calculate weekly performance",
    ):
        get_weekly_performance("reliance")


def test_weekly_performance_rejects_missing_close_prices(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ],
            "symbol": ["RELIANCE"] * 5,
            "open": [100.0, 102.0, 104.0, 106.0, 108.0],
            "high": [101.0, 103.0, 105.0, 107.0, 111.0],
            "low": [99.0, 101.0, 103.0, 105.0, 107.0],
            "close": [100.0, 102.0, float("nan"), 106.0, 110.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(
        ValueError,
        match="missing or non-numeric OHLC prices",
    ):
        get_weekly_performance("reliance")


def test_weekly_performance_rejects_zero_starting_close(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ],
            "symbol": ["RELIANCE"] * 5,
            "open": [0.0, 102.0, 104.0, 106.0, 108.0],
            "high": [1.0, 103.0, 105.0, 107.0, 111.0],
            "low": [0.0, 101.0, 103.0, 105.0, 107.0],
            "close": [0.0, 102.0, 104.0, 106.0, 110.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(
        ValueError,
        match="non-positive OHLC prices",
    ):
        get_weekly_performance("reliance")


def test_weekly_performance_rejects_non_finite_return(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ],
            "symbol": ["RELIANCE"] * 5,
            "open": [1e-308, 102.0, 104.0, 106.0, 1e308],
            "high": [1.0, 103.0, 105.0, 107.0, 1e308],
            "low": [1e-308, 101.0, 103.0, 105.0, 1e307],
            "close": [1e-308, 102.0, 104.0, 106.0, 1e308],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(
        ValueError,
        match="Calculated return is not a finite number",
    ):
        get_weekly_performance("reliance")


def test_rank_weekly_performers_returns_best_symbols(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ],
            "symbol": (["TCS"] * 5 + ["RELIANCE"] * 5 + ["INFY"] * 5),
            "open": [100.0] * 15,
            "high": [115.0] * 15,
            "low": [95.0] * 15,
            "close": [
                100.0,
                102.0,
                105.0,
                108.0,
                110.0,  # TCS: +10%
                100.0,
                101.0,
                102.0,
                103.0,
                105.0,  # RELIANCE: +5%
                100.0,
                99.0,
                98.0,
                97.0,
                95.0,  # INFY: -5%
            ],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    nifty_path = tmp_path / "nifty50.json"
    nifty_path.write_text(json.dumps(["TCS", "RELIANCE", "INFY"]))
    monkeypatch.setattr(data, "NIFTY50_PATH", nifty_path)

    result = rank_weekly_performers(top_n=2)

    assert len(result["rankings"]) == 2
    assert result["rankings"][0]["symbol"] == "TCS"
    assert result["rankings"][1]["symbol"] == "RELIANCE"
    assert result["skipped"] == []


def test_rank_weekly_performers_breaks_ties_alphabetically(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ]
            * 2,
            "symbol": ["TCS"] * 5 + ["INFY"] * 5,
            "open": [100.0] * 10,
            "high": [111.0] * 10,
            "low": [99.0] * 10,
            "close": [100.0, 100.0, 100.0, 100.0, 110.0] * 2,
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    nifty_path = tmp_path / "nifty50.json"
    nifty_path.write_text(json.dumps(["TCS", "INFY"]))
    monkeypatch.setattr(data, "NIFTY50_PATH", nifty_path)

    result = rank_weekly_performers(top_n=2)

    assert [item["symbol"] for item in result["rankings"]] == ["INFY", "TCS"]
    assert result["rankings"][0]["return_percent"] == pytest.approx(10.0)
    assert result["rankings"][1]["return_percent"] == pytest.approx(10.0)


def test_rank_weekly_performers_records_unavailable_symbol(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ],
            "symbol": ["TCS"] * 5 + ["RELIANCE"] * 5,
            "open": [100.0] * 10,
            "high": [115.0] * 10,
            "low": [95.0] * 10,
            "close": [
                100.0,
                102.0,
                105.0,
                108.0,
                110.0,
                100.0,
                101.0,
                102.0,
                103.0,
                105.0,
            ],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    nifty_path = tmp_path / "nifty50.json"
    nifty_path.write_text(json.dumps(["TCS", "RELIANCE", "NOT_AVAILABLE"]))
    monkeypatch.setattr(data, "NIFTY50_PATH", nifty_path)

    result = rank_weekly_performers(top_n=5)

    assert len(result["rankings"]) == 2
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["symbol"] == "NOT_AVAILABLE"


@pytest.mark.parametrize("invalid_top_n", [0, -1, 51, "5", None])
def test_rank_weekly_performers_rejects_invalid_top_n(invalid_top_n):
    with pytest.raises(
        ValueError,
        match="top_n must be an integer between 1 and 50",
    ):
        rank_weekly_performers(invalid_top_n)


def test_validate_ticker_accepts_lowercase_valid_symbol(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    result = validate_ticker("reliance")

    assert result == {
        "valid": True,
        "ticker": "RELIANCE",
        "message": "Ticker is available",
    }


def test_validate_ticker_rejects_invalid_symbol(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    result = validate_ticker("notreal")

    assert result["valid"] is False
    assert result["ticker"] == "NOTREAL"
    assert "not found" in result["message"]


def test_validate_ticker_raises_when_data_path_is_missing(monkeypatch):

    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", "")

    with pytest.raises(ValueError, match="INDIAN_STOCK_DATA_PATH is not configured"):
        validate_ticker("RELIANCE")


def test_validate_ticker_raises_when_data_file_is_missing(tmp_path, monkeypatch):

    parquet_path = tmp_path / "prices.parquet"
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))
    with pytest.raises(
        FileNotFoundError, match="Configured market-data file was not found"
    ):
        validate_ticker("RELIANCE")


def test_validate_ticker_rejects_empty_symbol():
    with pytest.raises(ValueError, match="Symbol must be a non-empty string"):
        validate_ticker("")


def test_validate_ticker_rejects_non_string_symbol():
    with pytest.raises(ValueError, match="Symbol must be a non-empty string"):
        validate_ticker(123)


def test_load_symbol_data_rejects_empty_symbol():
    with pytest.raises(ValueError, match="non-empty string"):
        load_symbol_data("   ")


def test_load_symbol_data_csv(monkeypatch):
    csv_path = Path(__file__).resolve().parents[1] / "data" / "sample_equity_daily.csv"
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(csv_path))

    result = load_symbol_data("reliance")

    assert len(result) == 5
    assert result["symbol"].tolist() == ["RELIANCE"] * 5
    assert result["date"].is_monotonic_increasing
    assert "volume" in result.columns


def test_load_symbol_data_rejects_unsupported_format(tmp_path, monkeypatch):
    unsupported_path = tmp_path / "prices.txt"
    unsupported_path.write_text("not market data")
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(unsupported_path))

    with pytest.raises(ValueError, match="Unsupported data format"):
        load_symbol_data("RELIANCE")


@pytest.mark.parametrize("file_extension", ["csv", "parquet"])
def test_get_dataset_symbols_normalizes_and_deduplicates(
    tmp_path, monkeypatch, file_extension
):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", "2026-07-23", "2026-07-24"],
            "symbol": [" reliance ", "RELIANCE", " tcs "],
            "open": [1271.0, 1265.0, 2251.1],
            "high": [1284.0, 1275.0, 2260.0],
            "low": [1268.0, 1258.0, 2240.0],
            "close": [1278.0, 1272.0, 2254.3],
        }
    )
    data_path = tmp_path / f"prices.{file_extension}"
    if file_extension == "csv":
        source_data.to_csv(data_path, index=False)
    else:
        source_data.to_parquet(data_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(data_path))

    assert get_available_universe() == ["RELIANCE", "TCS"]


def _write_dataset(tmp_path, source_data, file_extension):
    data_path = tmp_path / f"prices.{file_extension}"
    if file_extension == "csv":
        source_data.to_csv(data_path, index=False)
    else:
        source_data.to_parquet(data_path, index=False)
    return data_path


@pytest.mark.parametrize("file_extension", ["csv", "parquet"])
def test_dataset_rejects_missing_symbol_values(tmp_path, monkeypatch, file_extension):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", ""],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
        }
    )
    data_path = _write_dataset(tmp_path, source_data, file_extension)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(data_path))

    with pytest.raises(ValueError, match="missing or empty symbol values"):
        load_symbol_data("RELIANCE")


@pytest.mark.parametrize("file_extension", ["csv", "parquet"])
def test_dataset_rejects_non_numeric_ohlc_prices(tmp_path, monkeypatch, file_extension):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": ["1272.0", "not-a-number"],
        }
    )
    data_path = _write_dataset(tmp_path, source_data, file_extension)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(data_path))

    with pytest.raises(ValueError, match="missing or non-numeric OHLC prices"):
        load_symbol_data("RELIANCE")


def test_dataset_rejects_non_finite_ohlc_prices(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, float("inf")],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(ValueError, match="non-finite OHLC prices"):
        load_symbol_data("RELIANCE")


@pytest.mark.parametrize("file_extension", ["csv", "parquet"])
@pytest.mark.parametrize("bad_price_column", ["open", "high", "low", "close"])
def test_dataset_rejects_non_positive_ohlc_prices(
    tmp_path, monkeypatch, file_extension, bad_price_column
):
    prices = {
        "open": [1265.0, 1271.0],
        "high": [1275.0, 1284.0],
        "low": [1258.0, 1268.0],
        "close": [1272.0, 1278.0],
    }
    prices[bad_price_column][1] = 0.0
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            **prices,
        }
    )
    data_path = _write_dataset(tmp_path, source_data, file_extension)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(data_path))

    with pytest.raises(ValueError, match="non-positive OHLC prices"):
        load_symbol_data("RELIANCE")


@pytest.mark.parametrize(
    "high, low",
    [
        pytest.param(1265.0, 1258.0, id="high_below_close"),
        pytest.param(1275.0, 1280.0, id="low_above_open"),
    ],
)
def test_dataset_rejects_inconsistent_ohlc_relationships(
    tmp_path, monkeypatch, high, low
):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24"],
            "symbol": ["RELIANCE"],
            "open": [1271.0],
            "high": [high],
            "low": [low],
            "close": [1278.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(ValueError, match="inconsistent OHLC relationships"):
        load_symbol_data("RELIANCE")


@pytest.mark.parametrize("file_extension", ["csv", "parquet"])
def test_dataset_rejects_negative_volume(tmp_path, monkeypatch, file_extension):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
            "volume": [1000, -5],
        }
    )
    data_path = _write_dataset(tmp_path, source_data, file_extension)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(data_path))

    with pytest.raises(ValueError, match="negative volume values"):
        load_symbol_data("RELIANCE")


def test_dataset_rejects_non_numeric_volume(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
            "volume": [1000, "not-a-number"],
        }
    )
    csv_path = tmp_path / "prices.csv"
    source_data.to_csv(csv_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(csv_path))

    with pytest.raises(ValueError, match="non-numeric volume values"):
        load_symbol_data("RELIANCE")


def test_dataset_allows_partially_missing_volume(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
            "volume": [1000, None],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    result = load_symbol_data("RELIANCE")

    assert result["volume"].tolist()[0] == 1000
    assert pd.isna(result["volume"].tolist()[1])


def test_load_symbol_data_identical_between_csv_and_parquet(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-23", "2026-07-24"],
            "symbol": [" reliance ", "RELIANCE"],
            "open": [1265.0, 1271.0],
            "high": [1275.0, 1284.0],
            "low": [1258.0, 1268.0],
            "close": [1272.0, 1278.0],
            "volume": [9000000, 9300000],
        }
    )

    csv_path = tmp_path / "prices.csv"
    source_data.to_csv(csv_path, index=False)
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)

    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(csv_path))
    csv_result = load_symbol_data("reliance")

    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))
    parquet_result = load_symbol_data("reliance")

    pd.testing.assert_frame_equal(
        csv_result.reset_index(drop=True),
        parquet_result.reset_index(drop=True),
        check_dtype=False,
    )


def test_get_nifty50_universe_returns_normalized_symbols():
    symbols = get_nifty50_universe()

    assert symbols
    assert all(symbol == symbol.strip().upper() for symbol in symbols)
    assert len(symbols) == len(set(symbols))


def test_missing_data_configuration_raises_error(monkeypatch):
    monkeypatch.delenv("INDIAN_STOCK_DATA_PATH", raising=False)
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)

    with pytest.raises(
        ValueError,
        match="INDIAN_STOCK_DATA_PATH is not configured",
    ):
        load_symbol_data("RELIANCE")


def test_missing_date_values_raise_error(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", None],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1271.0, 1265.0],
            "high": [1284.0, 1275.0],
            "low": [1268.0, 1258.0],
            "close": [1278.0, 1272.0],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(ValueError, match="missing or invalid date values"):
        load_symbol_data("RELIANCE")


def test_missing_nifty50_file_raises_error(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "NIFTY50_PATH", tmp_path / "missing.json")

    with pytest.raises(
        FileNotFoundError,
        match="Bundled Nifty 50 symbol file was not found. Reinstall the package",
    ):
        get_nifty50_universe()


@pytest.mark.parametrize(
    "content, expected_message",
    [
        ('{"symbols": ["RELIANCE"]}', "must contain a JSON list"),
        ('["RELIANCE", 10]', "must contain only strings"),
        ('["RELIANCE", ""]', "cannot contain empty symbols"),
        ("[]", "cannot be empty"),
    ],
)
def test_invalid_nifty50_file_content_raises_error(
    tmp_path, monkeypatch, content, expected_message
):
    nifty_path = tmp_path / "nifty50.json"
    nifty_path.write_text(content)
    monkeypatch.setattr(data, "NIFTY50_PATH", nifty_path)

    with pytest.raises((TypeError, ValueError), match=expected_message):
        get_nifty50_universe()


def test_nifty50_universe_deduplicates_preserving_order(tmp_path, monkeypatch):
    nifty_path = tmp_path / "nifty50.json"
    nifty_path.write_text('["RELIANCE", "TCS", "reliance", "INFY", "TCS"]')
    monkeypatch.setattr(data, "NIFTY50_PATH", nifty_path)

    symbols = get_nifty50_universe()

    assert symbols == ["RELIANCE", "TCS", "INFY"]


def test_nifty50_universe_ordering_is_deterministic(tmp_path, monkeypatch):
    nifty_path = tmp_path / "nifty50.json"
    nifty_path.write_text('["TCS", "RELIANCE", "INFY"]')
    monkeypatch.setattr(data, "NIFTY50_PATH", nifty_path)

    first_call = get_nifty50_universe()
    second_call = get_nifty50_universe()

    assert first_call == second_call == ["TCS", "RELIANCE", "INFY"]


@pytest.mark.parametrize("file_extension", ["csv", "parquet"])
def test_empty_dataset_universe_raises_error(tmp_path, monkeypatch, file_extension):
    source_data = pd.DataFrame(
        columns=["date", "symbol", "open", "high", "low", "close"]
    )
    data_path = tmp_path / f"prices.{file_extension}"
    if file_extension == "csv":
        source_data.to_csv(data_path, index=False)
    else:
        source_data.to_parquet(data_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(data_path))

    with pytest.raises(ValueError, match="contains no records"):
        get_available_universe()


# --- Dataset diagnostics -----------------------------------------------------


def _good_rows(symbols=("AAA", "BBB"), sessions=WEEKLY_SESSIONS):
    return [
        {
            "date": f"2026-01-{day:02d}",
            "symbol": symbol,
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 1000,
        }
        for symbol in symbols
        for day in range(1, sessions + 1)
    ]


def _configure_csv(tmp_path, monkeypatch, rows, name="prices.csv", columns=None):
    frame = pd.DataFrame(rows, columns=columns)
    path = tmp_path / name
    frame.to_csv(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    return path


def _unset_data_path(monkeypatch):
    monkeypatch.delenv("INDIAN_STOCK_DATA_PATH", raising=False)
    monkeypatch.setattr(data, "_DOTENV_LOADED", True)


def _codes(entries):
    return [entry["code"] for entry in entries]


def test_collect_data_issues_reports_zero_for_clean_data():
    frame, issues = collect_data_issues(pd.DataFrame(_good_rows()))

    assert set(issues) == set(DATA_ISSUE_MESSAGES)
    assert not any(issues.values())
    assert frame["symbol"].tolist()[0] == "AAA"


def test_collect_data_issues_counts_every_problem_together():
    rows = _good_rows(("AAA",))
    rows += [
        {**rows[0]},  # duplicate symbol-date
        {**rows[0], "date": "not-a-date", "symbol": "BBB"},
        {**rows[0], "symbol": " ", "date": "2026-02-01"},
        {**rows[0], "symbol": "CCC", "open": "x"},
        {**rows[0], "symbol": "DDD", "open": float("inf")},
        {**rows[0], "symbol": "EEE", "low": -1.0},
        {**rows[0], "symbol": "FFF", "high": 5.0},
        {**rows[0], "symbol": "GGG", "volume": "lots"},
        {**rows[0], "symbol": "HHH", "volume": -5},
    ]

    _, issues = collect_data_issues(pd.DataFrame(rows))

    assert issues["duplicate_symbol_date"] == 1
    assert issues["invalid_date"] == 1
    assert issues["invalid_symbol"] == 1
    assert issues["invalid_ohlc_price"] == 1
    assert issues["non_finite_ohlc_price"] == 1
    assert issues["non_positive_ohlc_price"] == 1
    assert issues["inconsistent_ohlc"] >= 1
    assert issues["non_numeric_volume"] == 1
    assert issues["negative_volume"] == 1
    assert issues["empty_dataset"] == 0


def test_collect_data_issues_does_not_count_duplicates_with_invalid_dates():
    rows = _good_rows(("AAA",), sessions=1)
    rows += [{**rows[0], "date": "bad"}, {**rows[0], "date": "bad"}]

    _, issues = collect_data_issues(pd.DataFrame(rows))

    assert issues["invalid_date"] == 2
    assert issues["duplicate_symbol_date"] == 0


def test_collect_data_issues_flags_empty_frame():
    _, issues = collect_data_issues(pd.DataFrame(_good_rows()).iloc[0:0])

    assert issues["empty_dataset"] == 1


def test_collect_data_issues_works_without_volume_column():
    frame = pd.DataFrame(_good_rows()).drop(columns=["volume"])

    _, issues = collect_data_issues(frame)

    assert not any(issues.values())


def test_validate_price_data_raises_first_issue_in_documented_order():
    rows = _good_rows(("AAA",))
    rows[0]["symbol"] = " "
    rows[1]["date"] = "bad"

    with pytest.raises(ValueError, match="missing or empty symbol values"):
        validate_price_data(pd.DataFrame(rows))


def test_inspect_source_reports_unset_data_path(monkeypatch):
    _unset_data_path(monkeypatch)

    report = inspect_source()

    assert report["configured"] is False
    assert _codes(report["errors"]) == ["dataset_not_configured"]


def test_inspect_source_reports_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(tmp_path / "missing.csv"))

    report = inspect_source()

    assert report["configured"] is True
    assert report["available"] is False
    assert report["file_name"] is None
    assert _codes(report["errors"]) == ["file_not_found"]


def test_inspect_source_reports_unsupported_format(tmp_path, monkeypatch):
    path = tmp_path / "prices.txt"
    path.write_text("a,b\n1,2\n")
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))

    report = inspect_source()

    assert report["available"] is True
    assert report["readable"] is False
    assert report["format"] is None
    assert _codes(report["errors"]) == ["unsupported_format"]


@pytest.mark.parametrize(
    ("name", "content"),
    [("empty.csv", b""), ("corrupt.parquet", b"not a parquet file")],
)
def test_inspect_source_reports_unreadable_file(tmp_path, monkeypatch, name, content):
    path = tmp_path / name
    path.write_bytes(content)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))

    report = inspect_source()

    assert report["available"] is True
    assert report["readable"] is False
    assert _codes(report["errors"]) == ["unreadable_file"]
    assert str(tmp_path) not in json.dumps(report)


def test_inspect_source_reports_missing_required_columns(tmp_path, monkeypatch):
    _configure_csv(
        tmp_path,
        monkeypatch,
        _good_rows(),
        columns=["date", "symbol", "open", "high"],
    )

    report = inspect_source()

    assert report["readable"] is True
    assert report["missing_required_columns"] == ["low", "close"]
    assert report["optional_columns_present"] == []
    assert _codes(report["errors"]) == ["missing_required_columns"]


def test_inspect_source_reads_parquet_columns(tmp_path, monkeypatch):
    path = tmp_path / "prices.parquet"
    pd.DataFrame(_good_rows()).to_parquet(path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))

    report = inspect_source()

    assert report["format"] == "parquet"
    assert report["errors"] == []
    assert report["optional_columns_present"] == ["volume"]
    assert report["file_name"] == "prices.parquet"


def test_diagnose_dataset_healthy(tmp_path, monkeypatch):
    _configure_csv(tmp_path, monkeypatch, _good_rows())

    report = diagnose_dataset()

    assert report["status"] == "healthy"
    assert report["errors"] == []
    assert _codes(report["warnings"]) == ["price_adjustment_unknown"]
    assert report["summary"] == {
        "row_count": 10,
        "symbol_count": 2,
        "date_range": {"start": "2026-01-01", "end": "2026-01-05"},
    }
    assert report["capabilities"] == {
        "price_history": True,
        "weekly_return": True,
        "volume_analysis": True,
    }
    assert not any(report["data_quality"]["issue_counts"].values())


def test_diagnose_dataset_without_volume_is_incomplete(tmp_path, monkeypatch):
    rows = [{k: v for k, v in row.items() if k != "volume"} for row in _good_rows()]
    _configure_csv(tmp_path, monkeypatch, rows)

    report = diagnose_dataset()

    assert report["status"] == "incomplete"
    assert "volume_column_missing" in _codes(report["warnings"])
    assert report["capabilities"]["volume_analysis"] is False
    assert report["capabilities"]["weekly_return"] is True


def test_diagnose_dataset_with_all_missing_volume_is_incomplete(tmp_path, monkeypatch):
    rows = [{**row, "volume": None} for row in _good_rows()]
    _configure_csv(tmp_path, monkeypatch, rows)

    report = diagnose_dataset()

    assert report["status"] == "incomplete"
    assert "volume_all_missing" in _codes(report["warnings"])
    assert report["capabilities"]["volume_analysis"] is False


def test_diagnose_dataset_reports_insufficient_sessions(tmp_path, monkeypatch):
    rows = _good_rows(("AAA",)) + _good_rows(("THIN",), sessions=2)
    _configure_csv(tmp_path, monkeypatch, rows)

    report = diagnose_dataset()

    assert report["status"] == "incomplete"
    assert "insufficient_sessions" in _codes(report["warnings"])
    assert report["data_quality"]["symbols_with_insufficient_sessions"] == {
        "count": 1,
        "symbols": ["THIN"],
    }
    assert report["capabilities"]["weekly_return"] is True


def test_diagnose_dataset_without_any_weekly_capable_symbol(tmp_path, monkeypatch):
    _configure_csv(
        tmp_path, monkeypatch, _good_rows(("AAA",), sessions=WEEKLY_SESSIONS - 1)
    )

    report = diagnose_dataset()

    assert report["status"] == "incomplete"
    assert report["capabilities"]["price_history"] is True
    assert report["capabilities"]["weekly_return"] is False


def test_diagnose_dataset_caps_listed_insufficient_symbols(tmp_path, monkeypatch):
    symbols = [f"S{index:03d}" for index in range(MAX_LISTED_THIN_SYMBOLS + 5)]
    _configure_csv(tmp_path, monkeypatch, _good_rows(symbols, sessions=1))

    thin = diagnose_dataset()["data_quality"]["symbols_with_insufficient_sessions"]

    assert thin["count"] == MAX_LISTED_THIN_SYMBOLS + 5
    assert len(thin["symbols"]) == MAX_LISTED_THIN_SYMBOLS


def test_diagnose_dataset_reports_all_invalid_records_at_once(tmp_path, monkeypatch):
    rows = _good_rows()
    rows.append({**rows[0]})
    rows.append({**rows[1], "date": "bad"})
    rows.append({**rows[2], "high": 1.0})
    _configure_csv(tmp_path, monkeypatch, rows)

    report = diagnose_dataset()

    assert report["status"] == "invalid"
    assert {"invalid_date", "duplicate_symbol_date", "inconsistent_ohlc"} <= set(
        _codes(report["errors"])
    )
    assert report["capabilities"] == {
        "price_history": False,
        "weekly_return": False,
        "volume_analysis": False,
    }
    assert "price_adjustment_unknown" in _codes(report["warnings"])


def test_diagnose_dataset_unset_path_is_invalid(monkeypatch):
    _unset_data_path(monkeypatch)

    report = diagnose_dataset()

    assert report["status"] == "invalid"
    assert _codes(report["errors"]) == ["dataset_not_configured"]
    assert report["summary"]["row_count"] is None
    assert _codes(report["warnings"]) == ["price_adjustment_unknown"]


def test_diagnose_dataset_missing_columns_is_invalid(tmp_path, monkeypatch):
    _configure_csv(
        tmp_path, monkeypatch, _good_rows(), columns=["date", "symbol", "close"]
    )

    report = diagnose_dataset()

    assert report["status"] == "invalid"
    assert report["schema"]["missing_required_columns"] == ["open", "high", "low"]
    assert "missing_required_columns" in _codes(report["errors"])


def test_diagnose_dataset_empty_file_has_clean_error_and_no_noise(
    tmp_path, monkeypatch
):
    _configure_csv(tmp_path, monkeypatch, [], columns=list(_good_rows()[0]))

    report = diagnose_dataset()

    assert report["status"] == "invalid"
    assert report["errors"] == [
        {"code": "empty_dataset", "message": DATA_ISSUE_MESSAGES["empty_dataset"]}
    ]
    assert _codes(report["warnings"]) == ["price_adjustment_unknown"]
    assert report["summary"]["row_count"] == 0
    assert report["summary"]["date_range"] is None


@pytest.mark.parametrize("scenario", ["healthy", "invalid", "unset", "corrupt"])
def test_diagnose_dataset_is_json_safe_and_hides_private_paths(
    tmp_path, monkeypatch, scenario
):
    if scenario == "healthy":
        _configure_csv(tmp_path, monkeypatch, _good_rows())
    elif scenario == "invalid":
        rows = _good_rows()
        rows[0]["open"] = float("nan")
        _configure_csv(tmp_path, monkeypatch, rows)
    elif scenario == "corrupt":
        path = tmp_path / "corrupt.parquet"
        path.write_bytes(b"junk")
        monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(path))
    else:
        _unset_data_path(monkeypatch)

    report = diagnose_dataset()

    serialized = json.dumps(report, allow_nan=False)
    assert str(tmp_path) not in serialized
    assert report["status"] in {"healthy", "incomplete", "invalid"}


def test_diagnose_dataset_does_not_raise_when_loading_fails(tmp_path, monkeypatch):
    _configure_csv(tmp_path, monkeypatch, _good_rows())

    def broken_reader(*args, **kwargs):
        raise OSError(f"cannot read {tmp_path}")

    monkeypatch.setattr(data, "read_raw_dataset", broken_reader)

    report = diagnose_dataset()

    assert report["status"] == "invalid"
    assert _codes(report["errors"]) == ["data_load_failed"]
    assert str(tmp_path) not in json.dumps(report)
