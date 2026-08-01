import json
from pathlib import Path

import pandas as pd
import pytest

from indian_stock_market_mcp import data
from indian_stock_market_mcp.data import (
    get_available_universe,
    get_nifty50_universe,
    get_recent_price_history,
    get_weekly_performance,
    load_symbol_data,
    rank_weekly_performers,
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


def test_load_symbol_data_normalizes_mixed_case_parquet_symbol(
    tmp_path, monkeypatch
):
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

    with pytest.raises(
        ValueError,
        match=r"Missing required column\(s\): \['close'\]"
    ):
        load_symbol_data("reliance")


def test_invalid_symbol_raises_error(tmp_path, monkeypatch):
    source_data = pd.DataFrame(
        {
            "date": ["2026-07-24", "2026-07-23"],
            "symbol": ["RELIANCE", "RELIANCE"],
            "open": [1271.0, 1265.0],
            "high": [1284.0, 1275.0],
            "low": [1268.0, 1258.0],
            "close": [1278.0, 1272.0]
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    with pytest.raises(
        ValueError,
        match="NOTREAL"
    ):
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

    with pytest.raises(ValueError, match="duplicate date values"):
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
        match="Cannot calculate return with missing close prices",
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
        match="Cannot calculate return when starting close price is zero",
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
              "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
              "2026-07-24",
              "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
              "2026-07-24",
              "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
              "2026-07-24",
          ],
          "symbol": (
              ["TCS"] * 5
              + ["RELIANCE"] * 5
              + ["INFY"] * 5
          ),
          "open": [100.0] * 15,
          "high": [115.0] * 15,
          "low": [95.0] * 15,
          "close": [
              100.0, 102.0, 105.0, 108.0, 110.0,  # TCS: +10%
              100.0, 101.0, 102.0, 103.0, 105.0,  # RELIANCE: +5%
              100.0, 99.0, 98.0, 97.0, 95.0,      # INFY: -5%
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


def test_rank_weekly_performers_breaks_ties_alphabetically(
    tmp_path, monkeypatch
):
    source_data = pd.DataFrame(
        {
            "date": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
                "2026-07-23",
                "2026-07-24",
            ] * 2,
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
                "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
                "2026-07-24",
                "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
                "2026-07-24",
            ],
            "symbol": ["TCS"] * 5 + ["RELIANCE"] * 5,
            "open": [100.0] * 10,
            "high": [115.0] * 10,
            "low": [95.0] * 10,
            "close": [
                100.0, 102.0, 105.0, 108.0, 110.0,
                100.0, 101.0, 102.0, 103.0, 105.0,
            ],
        }
    )
    parquet_path = tmp_path / "prices.parquet"
    source_data.to_parquet(parquet_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(parquet_path))

    nifty_path = tmp_path / "nifty50.json"
    nifty_path.write_text(
        json.dumps(["TCS", "RELIANCE", "NOT_AVAILABLE"])
    )
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


def test_load_symbol_data_rejects_empty_symbol():
    with pytest.raises(ValueError, match="non-empty string"):
        load_symbol_data("   ")

def test_load_symbol_data_csv(monkeypatch):
    csv_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "sample_equity_daily.csv"
    )
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

    with pytest.raises(ValueError, match="missing date values"):
        load_symbol_data("RELIANCE")


def test_missing_nifty50_file_raises_error(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "NIFTY50_PATH", tmp_path / "missing.json")

    with pytest.raises(FileNotFoundError, match="Nifty 50 symbol file not found"):
        get_nifty50_universe()


@pytest.mark.parametrize(
    "content, expected_message",
    [
        ('{"symbols": ["RELIANCE"]}', "must contain a JSON list"),
        ('["RELIANCE", 10]', "must contain only strings"),
        ('["RELIANCE", ""]', "cannot contain empty symbols"),
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


@pytest.mark.parametrize("file_extension", ["csv", "parquet"])
def test_empty_dataset_universe_raises_error(
    tmp_path, monkeypatch, file_extension
):
    source_data = pd.DataFrame(
        columns=["date", "symbol", "open", "high", "low", "close"]
    )
    data_path = tmp_path / f"prices.{file_extension}"
    if file_extension == "csv":
        source_data.to_csv(data_path, index=False)
    else:
        source_data.to_parquet(data_path, index=False)
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(data_path))

    with pytest.raises(ValueError, match="No symbols found"):
        get_available_universe()
