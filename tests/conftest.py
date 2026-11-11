"""Shared test setup."""

import pytest

from indian_stock_market_mcp import adjustments
from indian_stock_market_mcp.settings import SETTINGS_PATH_ENV
from indian_stock_market_mcp.watchlists import WATCHLIST_PATH_ENV


@pytest.fixture(autouse=True)
def isolated_watchlists(tmp_path_factory, monkeypatch):
    """Keep every test away from the real watchlists file in the home folder."""
    folder = tmp_path_factory.mktemp("watchlists")
    monkeypatch.setenv(WATCHLIST_PATH_ENV, str(folder / "watchlists.json"))


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    """Keep every test away from the real settings file in the home folder."""
    folder = tmp_path_factory.mktemp("settings")
    monkeypatch.setenv(SETTINGS_PATH_ENV, str(folder / "indicator_settings.json"))


@pytest.fixture(autouse=True)
def no_yahoo(monkeypatch):
    """Tests never reach Yahoo. A test that needs prices passes its own fetcher
    or replaces this one."""

    def refuse(ticker, start, end):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(adjustments, "fetch_yahoo_closes", refuse)
