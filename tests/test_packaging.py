"""Tests of the packaged resource and the installed entry point."""

import importlib
import json
import tomllib
from fnmatch import fnmatch
from importlib.resources import files
from pathlib import Path

from indian_stock_market_mcp import data

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def load_pyproject():
    return tomllib.loads(PYPROJECT.read_text())


def test_bundled_nifty50_resource_is_found_through_importlib_resources():
    resource = files("indian_stock_market_mcp").joinpath("resources/nifty50.json")

    assert resource.is_file()


def test_bundled_nifty50_resource_holds_fifty_unique_clean_symbols():
    resource = files("indian_stock_market_mcp").joinpath("resources/nifty50.json")

    symbols = json.loads(resource.read_text())

    assert isinstance(symbols, list)
    assert len(symbols) == 50
    assert len(set(symbols)) == 50
    assert all(isinstance(s, str) and s == s.strip().upper() and s for s in symbols)
    assert data.get_nifty50_universe() == symbols


def test_package_data_declaration_covers_the_nifty50_resource():
    package_data = load_pyproject()["tool"]["setuptools"]["package-data"]

    patterns = package_data["indian_stock_market_mcp"]

    assert any(fnmatch("resources/nifty50.json", pattern) for pattern in patterns)


def test_console_script_points_at_a_callable_entry_point():
    scripts = load_pyproject()["project"]["scripts"]

    module_name, function_name = scripts["indian-stock-market-mcp"].split(":")
    entry_point = getattr(importlib.import_module(module_name), function_name)

    assert callable(entry_point)


def test_missing_resource_file_reports_a_reinstall_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "NIFTY50_PATH", tmp_path / "missing.json")

    try:
        data.load_nifty50_symbols()
    except FileNotFoundError as error:
        assert "Reinstall the package" in str(error)
    else:
        raise AssertionError("expected FileNotFoundError")
