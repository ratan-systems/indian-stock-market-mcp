"""Tests that the bundled sample is synthetic, deterministic, and valid."""

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd

from indian_stock_market_mcp.data import collect_data_issues

ROOT = Path(__file__).parents[1]
SAMPLE_CSV = ROOT / "data" / "sample_equity_daily.csv"
GENERATOR = ROOT / "scripts" / "generate_sample_data.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_sample_data", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_sample_matches_the_generator_output():
    assert SAMPLE_CSV.read_text() == load_generator().render_csv()


def test_generator_is_deterministic():
    generator = load_generator()

    assert generator.render_csv() == generator.render_csv()


def test_sample_has_three_symbols_with_five_weekday_sessions_each():
    frame = pd.read_csv(SAMPLE_CSV)

    assert sorted(frame["symbol"].unique()) == ["INFY", "RELIANCE", "TCS"]
    assert frame.groupby("symbol").size().tolist() == [5, 5, 5]
    weekdays = [date.fromisoformat(value).weekday() for value in frame["date"]]
    assert max(weekdays) <= 4


def test_sample_passes_every_data_quality_check():
    _, issues = collect_data_issues(pd.read_csv(SAMPLE_CSV))

    assert not any(issues.values())


def test_sample_is_documented_as_synthetic():
    for readme in (ROOT / "README.md", ROOT / "data" / "README.md"):
        assert "synthetic" in readme.read_text().lower(), readme
