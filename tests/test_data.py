import pandas as pd
import pytest

from indian_stock_market_mcp.data import get_weekly_performance, load_symbol_data


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
