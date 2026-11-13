"""Verify an installed copy of indian-stock-market-mcp.

Run this with the Python of a clean virtual environment that has the built
wheel or sdist installed. It checks that:

* the package is imported from the installed copy, not the source tree;
* the bundled universes resource loads;
* the installed console command starts the MCP server and completes the
  protocol handshake;
* the server offers exactly the expected tools and answers real calls.

It exits 0 on success and 1 on the first failure.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from importlib import metadata
from importlib.resources import files
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PACKAGE = "indian_stock_market_mcp"
DISTRIBUTION = "indian-stock-market-mcp"
COMMAND = "indian-stock-market-mcp"
SERVER_NAME = "Indian Stock Market MCP"
TIMEOUT_SECONDS = 60
EXPECTED_UNIVERSES = 31

EXPECTED_TOOLS = {
    "get_price_history",
    "rank_weekly_performers",
    "validate_ticker",
    "get_stock_weekly_return",
    "get_available_universe",
    "get_nifty50_universe",
    "get_data_capabilities",
    "get_data_quality_report",
    "list_universes",
    "create_watchlist",
    "get_watchlists",
    "update_watchlist",
    "delete_watchlist",
    "get_stock_indicators",
    "get_indicator_settings",
    "get_risk_snapshot",
    "get_relative_strength",
    "rank_relative_strength",
    "get_52_week_range",
    "find_near_52_week_high",
    "find_new_52_week_extremes",
    "update_indicator_settings",
    "reset_indicator_settings",
    "rank_performers",
    "screen_momentum",
    "find_volume_breakouts",
    "list_screen_presets",
    "run_screen_preset",
    "compare_stocks",
    "get_market_breadth",
}


class VerificationError(Exception):
    """Raised when an installed-package check fails."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)
    print(f"ok  {message}")


SAMPLE_SESSIONS = 70
SAMPLE_SYMBOLS = ["AAA", "BBB", "MOMO", "DOWN", "JUMP"]


def sample_days() -> list[date]:
    """Seventy consecutive weekdays from 1 January 2026."""
    days, day = [], date(2026, 1, 1)
    while len(days) < SAMPLE_SESSIONS:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def write_sample_dataset(directory: Path) -> Path:
    """Write a small deterministic dataset: five symbols, seventy sessions each.

    AAA and BBB rise every day, MOMO rises with regular dips, DOWN falls every
    day, and JUMP is flat until a final session that closes above its range on
    three times its usual volume.
    """
    days = sample_days()
    lines = ["date,symbol,open,high,low,close,volume"]

    def add(symbol: str, day: date, close: float, high: float, volume: int) -> None:
        lines.append(
            f"{day.isoformat()},{symbol},{close:.2f},{high:.2f},"
            f"{close - 1:.2f},{close:.2f},{volume}"
        )

    momo = 100.0
    for at, day in enumerate(days):
        last = at == SAMPLE_SESSIONS - 1
        add("AAA", day, 100.0 + at, 101.0 + at, 1_000_000)
        add("BBB", day, 200.0 + at, 201.0 + at, 1_000_000)
        momo *= 0.99 if at % 3 == 2 else 1.01
        add("MOMO", day, momo, momo + 1, 1_000_000)
        add("DOWN", day, 300.0 * 0.99**at, 300.0 * 0.99**at + 1, 1_000_000)
        add(
            "JUMP",
            day,
            105.0 if last else 100.0,
            106.0 if last else 101.0,
            3_000_000 if last else 1_000_000,
        )
    path = directory / "sample.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def result_data(result) -> dict:
    """Return a tool result as a dict, from structured or text content."""
    check(not result.isError, "tool call did not report an error")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def envelope_data(result, tool: str) -> dict:
    """Return a tool's `data` after checking it came in the standard envelope."""
    payload = result_data(result)
    check(
        {"envelope_version", "data", "source", "as_of", "parameters", "warnings"}
        == set(payload),
        f"{tool} returns the standard response envelope",
    )
    return payload["data"]


def check_installed_package(allow_source: bool, expected_version: str | None) -> None:
    module = __import__(PACKAGE)
    location = Path(module.__file__).resolve()
    if not allow_source:
        check(
            "site-packages" in location.parts or "dist-packages" in location.parts,
            "package is imported from the installed copy, not the source tree",
        )
    installed_version = metadata.version(DISTRIBUTION)
    print(f"ok  installed version is {installed_version}")
    if expected_version is not None:
        check(
            installed_version == expected_version,
            f"installed version matches expected {expected_version}",
        )


def check_bundled_universe() -> None:
    resource = files(PACKAGE).joinpath("resources/universes.json")
    check(resource.is_file(), "bundled universes.json resource is present")
    universes = json.loads(resource.read_text())["universes"]
    check(len(universes["NIFTY_50"]["symbols"]) == 50, "Nifty 50 holds 50 symbols")
    check(
        len(universes) == EXPECTED_UNIVERSES,
        f"{EXPECTED_UNIVERSES} bundled universes are present",
    )


def find_console_command() -> str:
    """Find the console script installed next to this Python interpreter."""
    scripts_dir = str(Path(sys.executable).parent)
    command = shutil.which(COMMAND, path=scripts_dir)
    check(command is not None, f"console command '{COMMAND}' is installed")
    return command


async def check_server(command: str, data_path: Path) -> None:
    parameters = StdioServerParameters(
        command=command,
        env={
            **os.environ,
            "INDIAN_STOCK_DATA_PATH": str(data_path),
            # Keep the check away from any real watchlists or settings in the
            # home folder.
            "INDIAN_STOCK_WATCHLIST_PATH": str(data_path.parent / "watchlists.json"),
            "INDIAN_STOCK_SETTINGS_PATH": str(data_path.parent / "settings.json"),
        },
    )
    with anyio.fail_after(TIMEOUT_SECONDS):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                check(
                    initialized.serverInfo.name == SERVER_NAME,
                    "console command starts the MCP server and completes the handshake",
                )

                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                check(
                    names == EXPECTED_TOOLS,
                    f"server offers exactly the {len(EXPECTED_TOOLS)} expected tools",
                )

                universe = envelope_data(
                    await session.call_tool("get_nifty50_universe", {}),
                    "get_nifty50_universe",
                )
                check(
                    universe["count"] == 50 and len(universe["symbols"]) == 50,
                    "get_nifty50_universe returns the 50 bundled symbols over MCP",
                )

                capabilities = envelope_data(
                    await session.call_tool("get_data_capabilities", {}),
                    "get_data_capabilities",
                )
                check(
                    capabilities["status"] == "healthy",
                    "get_data_capabilities reports the sample dataset as healthy",
                )

                history = envelope_data(
                    await session.call_tool(
                        "get_price_history", {"symbol": "AAA", "sessions": 3}
                    ),
                    "get_price_history",
                )
                check(
                    history["session_returned"] == 3,
                    "get_price_history returns data from the sample dataset",
                )

                quality = envelope_data(
                    await session.call_tool("get_data_quality_report", {}),
                    "get_data_quality_report",
                )
                check(
                    quality["backtest_ready"] is True,
                    "get_data_quality_report passes the sample dataset",
                )

                created = envelope_data(
                    await session.call_tool(
                        "create_watchlist",
                        {"name": "check", "symbols": ["aaa.ns", "BBB"]},
                    ),
                    "create_watchlist",
                )
                check(
                    created["symbols"] == ["AAA", "BBB"],
                    "create_watchlist saves cleaned symbols",
                )
                ranked = envelope_data(
                    await session.call_tool(
                        "rank_weekly_performers", {"universe": "check", "top_n": 2}
                    ),
                    "rank_weekly_performers",
                )
                check(
                    ranked["universe"] == "CHECK" and len(ranked["rankings"]) == 2,
                    "rank_weekly_performers accepts a watchlist name",
                )
                deleted = envelope_data(
                    await session.call_tool("delete_watchlist", {"name": "check"}),
                    "delete_watchlist",
                )
                check(deleted["deleted"] is True, "delete_watchlist removes it again")

                async def data_of(tool: str, arguments: dict) -> dict:
                    return envelope_data(await session.call_tool(tool, arguments), tool)

                everything = {"symbols": SAMPLE_SYMBOLS}
                export_path = data_path.parent / "ranking.csv"
                ranking = await data_of(
                    "rank_performers",
                    {**everything, "period": "1m", "output_path": str(export_path)},
                )
                check(
                    ranking["best"][0]["symbol"] == "AAA"
                    and ranking["worst"][0]["symbol"] == "DOWN",
                    "rank_performers ranks the best and worst over a month",
                )
                check(
                    export_path.read_text().startswith("# tool: rank_performers")
                    and ranking["export"]["rows"] == len(SAMPLE_SYMBOLS),
                    "rank_performers saves the ranking as a CSV with its source",
                )

                momentum = await data_of("screen_momentum", everything)
                reasons = {entry["reason"] for entry in momentum["excluded"]}
                check(
                    "MOMO" in [row["symbol"] for row in momentum["matches"]]
                    and {"rsi_too_high", "below_ema"} <= reasons,
                    "screen_momentum finds the steady riser and explains the rest",
                )

                breakouts = await data_of("find_volume_breakouts", everything)
                check(
                    [row["symbol"] for row in breakouts["matches"]] == ["JUMP"],
                    "find_volume_breakouts finds the breakout on heavy volume",
                )

                presets = await data_of("list_screen_presets", {})
                check(presets["count"] == 4, "list_screen_presets lists four presets")
                oversold = await data_of(
                    "run_screen_preset", {"preset": "oversold", **everything}
                )
                check(
                    [row["symbol"] for row in oversold["matches"]] == ["DOWN"],
                    "run_screen_preset runs the oversold preset",
                )

                comparison = await data_of(
                    "compare_stocks", {"symbols": ["AAA", "DOWN"]}
                )
                check(
                    [stock["symbol"] for stock in comparison["stocks"]]
                    == ["AAA", "DOWN"],
                    "compare_stocks puts two stocks side by side",
                )

                breadth = await data_of("get_market_breadth", everything)
                moves = breadth["advance_decline"]
                check(
                    (moves["advancers"], moves["decliners"]) == (4, 1),
                    "get_market_breadth counts advancers and decliners",
                )

                indicators = await data_of("get_stock_indicators", {"symbol": "MOMO"})
                check(
                    indicators["rsi"]["value"] is not None
                    and indicators["ema"][1]["value"] is not None,
                    "get_stock_indicators calculates RSI and the 50-day EMA",
                )

                listed = envelope_data(
                    await session.call_tool("list_universes", {}),
                    "list_universes",
                )
                check(
                    len(listed["universes"]) == EXPECTED_UNIVERSES
                    and listed["dataset_checked"],
                    f"list_universes lists the {EXPECTED_UNIVERSES} universes "
                    "and checks the dataset",
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--expected-version",
        help="fail unless the installed package has exactly this version",
    )
    parser.add_argument(
        "--allow-source",
        action="store_true",
        help="skip the check that the package is imported from site-packages",
    )
    arguments = parser.parse_args()

    # Work outside the source tree so it cannot be imported by accident.
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            check_installed_package(arguments.allow_source, arguments.expected_version)
            check_bundled_universe()
            command = find_console_command()
            data_path = write_sample_dataset(Path(directory))
            anyio.run(check_server, command, data_path)
        except VerificationError as error:
            print(f"FAIL  {error}", file=sys.stderr)
            return 1
        except Exception as error:
            print(f"FAIL  {type(error).__name__}: {error}", file=sys.stderr)
            return 1

    print("All installed-package checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
