from mcp.server.fastmcp import FastMCP


mcp = FastMCP("Indian Stock Market MCP")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
