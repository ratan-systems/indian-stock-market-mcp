import pandas as pd
import pytest

from indian_stock_market_mcp.data import load_symbol_data


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
