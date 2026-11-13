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


# --- The one-year sample ---------------------------------------------------------------

YEAR_CSV = ROOT / "data" / "sample_equity_year.csv"


def call_on_year_sample(monkeypatch, name, arguments=None):
    import asyncio
    import json

    from indian_stock_market_mcp import server

    monkeypatch.setenv("INDIAN_STOCK_DATA_PATH", str(YEAR_CSV))
    result = asyncio.run(server.mcp.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


def test_committed_year_sample_matches_the_generator_output():
    assert YEAR_CSV.read_text() == load_generator().render_year_csv()


def test_year_sample_has_twelve_symbols_with_a_year_of_weekday_sessions():
    frame = pd.read_csv(YEAR_CSV)

    assert frame["symbol"].nunique() == 12
    assert set(frame.groupby("symbol").size()) == {260}
    assert frame["date"].max() == "2026-07-31"
    assert max(date.fromisoformat(value).weekday() for value in frame["date"]) <= 4


def test_year_sample_passes_every_data_quality_check(monkeypatch):
    _, issues = collect_data_issues(pd.read_csv(YEAR_CSV))
    report = call_on_year_sample(monkeypatch, "get_data_quality_report")

    assert not any(issues.values())
    assert report["data"]["backtest_ready"] is True


def test_year_sample_gives_every_analysis_tool_enough_history(monkeypatch):
    def data_of(name, arguments=None):
        return call_on_year_sample(monkeypatch, name, arguments)["data"]

    indicators = data_of("get_stock_indicators", {"symbol": "TCS"})
    assert all(item["value"] is not None for item in indicators["ema"])  # 20, 50, 200
    assert data_of("get_52_week_range", {"symbol": "TCS"})["range"]["sessions"] == 252

    ranking = data_of("rank_performers", {"period": "1y", "top_n": 3})
    assert ranking["checked"] == 12
    assert ranking["sessions"] == 252
    assert (
        ranking["best"][0]["return_percent"] > 0 > ranking["worst"][0]["return_percent"]
    )

    momentum = data_of("screen_momentum")
    assert momentum["checked"] == 12
    assert momentum["total_matches"] + momentum["excluded_count"] == 12
    assert "too_little_history" not in [e["reason"] for e in momentum["excluded"]]

    breadth = data_of("get_market_breadth")
    assert [item["measured"] for item in breadth["above_ema"]] == [12, 12]
    moves = breadth["advance_decline"]
    assert moves["advancers"] + moves["decliners"] + moves["unchanged"] == 12

    comparison = data_of("compare_stocks", {"symbols": ["TCS", "MARUTI", "ITC"]})
    assert [stock["sessions"] for stock in comparison["stocks"]] == [260, 260, 260]
    assert all(stock["volatility"] for stock in comparison["stocks"])

    spikes = data_of("find_volume_breakouts", {"mode": "spike", "volume_multiple": 1})
    assert spikes["checked"] == 12
    assert spikes["matches"][0]["volume_ratio"] >= 1
