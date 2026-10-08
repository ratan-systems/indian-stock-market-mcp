"""Verify an installed copy of indian-stock-market-mcp.

Run this with the Python of a clean virtual environment that has the built
wheel or sdist installed. It checks that:

* the package is imported from the installed copy, not the source tree;
* the bundled Nifty 50 resource loads;
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

EXPECTED_TOOLS = {
    "get_price_history",
    "rank_weekly_performers",
    "validate_ticker",
    "get_stock_weekly_return",
    "get_available_universe",
    "get_nifty50_universe",
    "get_data_capabilities",
}


class VerificationError(Exception):
    """Raised when an installed-package check fails."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)
    print(f"ok  {message}")


def write_sample_dataset(directory: Path) -> Path:
    """Write a small deterministic dataset: two symbols, five sessions each."""
    lines = ["date,symbol,open,high,low,close,volume"]
    for symbol, base in (("AAA", 100.0), ("BBB", 200.0)):
        for day in range(5):
            close = base + day
            lines.append(
                f"2026-01-{day + 1:02d},{symbol},{close},{close + 1},"
                f"{close - 1},{close},{1000 + day}"
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
    resource = files(PACKAGE).joinpath("resources/nifty50.json")
    check(resource.is_file(), "bundled nifty50.json resource is present")
    symbols = json.loads(resource.read_text())
    check(len(symbols) == 50, "bundled universe holds 50 symbols")


def find_console_command() -> str:
    """Find the console script installed next to this Python interpreter."""
    scripts_dir = str(Path(sys.executable).parent)
    command = shutil.which(COMMAND, path=scripts_dir)
    check(command is not None, f"console command '{COMMAND}' is installed")
    return command


async def check_server(command: str, data_path: Path) -> None:
    parameters = StdioServerParameters(
        command=command,
        env={**os.environ, "INDIAN_STOCK_DATA_PATH": str(data_path)},
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
                    "server offers exactly the 7 expected tools",
                )

                universe = result_data(
                    await session.call_tool("get_nifty50_universe", {})
                )
                check(
                    universe["count"] == 50 and len(universe["symbols"]) == 50,
                    "get_nifty50_universe returns the 50 bundled symbols over MCP",
                )

                capabilities = result_data(
                    await session.call_tool("get_data_capabilities", {})
                )
                check(
                    capabilities["status"] == "healthy",
                    "get_data_capabilities reports the sample dataset as healthy",
                )

                history = result_data(
                    await session.call_tool(
                        "get_price_history", {"symbol": "AAA", "sessions": 3}
                    )
                )
                check(
                    history["session_returned"] == 3,
                    "get_price_history returns data from the sample dataset",
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
