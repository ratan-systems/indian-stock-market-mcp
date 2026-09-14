import json
from pathlib import Path

import pytest

from indian_stock_market_mcp import server


@pytest.fixture(autouse=True)
def sample_dataset(monkeypatch):
    csv_path = Path(__file__).parents[1] / "data" / "sample_equity_daily.csv"
    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(csv_path))


def _sample_symbol() -> str:
    return server.get_available_universe_data()[0]


def test_get_price_history_is_json_safe():
    response = server.get_price_history(_sample_symbol())
    json.dumps(response, allow_nan=False)


def test_rank_weekly_performers_is_json_safe():
    response = server.rank_weekly_performers()
    json.dumps(response, allow_nan=False)


def test_validate_ticker_valid_is_json_safe():
    response = server.validate_ticker(_sample_symbol())
    json.dumps(response, allow_nan=False)


def test_validate_ticker_invalid_is_json_safe():
    response = server.validate_ticker("NOTREAL")
    json.dumps(response, allow_nan=False)


def test_get_stock_weekly_return_is_json_safe():
    response = server.get_stock_weekly_return(_sample_symbol())
    json.dumps(response, allow_nan=False)


def test_get_available_universe_is_json_safe():
    response = server.get_available_universe()
    json.dumps(response, allow_nan=False)


def test_get_nifty50_universe_is_json_safe():
    response = server.get_nifty50_universe()
    json.dumps(response, allow_nan=False)

