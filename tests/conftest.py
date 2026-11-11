"""Shared test setup."""

import pytest

from indian_stock_market_mcp.watchlists import WATCHLIST_PATH_ENV


@pytest.fixture(autouse=True)
def isolated_watchlists(tmp_path_factory, monkeypatch):
    """Keep every test away from the real watchlists file in the home folder."""
    folder = tmp_path_factory.mktemp("watchlists")
    monkeypatch.setenv(WATCHLIST_PATH_ENV, str(folder / "watchlists.json"))
