"""Save a tool's table as a CSV file the user asked for.

The file starts with comment lines (beginning with '#') that record where the
numbers came from, then a normal header row and the rows. Spreadsheets show
the comment lines as text; pandas skips them with `comment="#"`.
"""

import csv
import json
from pathlib import Path
from typing import Any

from .atomic import write_atomically

COMMENT = "#"
LIST_SEPARATOR = "; "


class ExportError(ValueError):
    """Raised when a CSV cannot be written where the user asked."""


def resolve_output(output_path: Any, overwrite: bool) -> Path:
    """Check the path before any work is done, so a bad path fails early."""
    if not isinstance(output_path, str) or not output_path.strip():
        raise ExportError("output_path must be a file path ending in .csv")
    path = Path(output_path.strip()).expanduser()
    if path.suffix.lower() != ".csv":
        raise ExportError(f"output_path must end in .csv, got '{path.name}'")
    if path.is_dir():
        raise ExportError(f"output_path '{path.name}' is a folder, not a file")
    if not path.parent.is_dir():
        raise ExportError(f"The folder for '{path.name}' does not exist")
    if path.exists() and not overwrite:
        raise ExportError(
            f"'{path.name}' already exists. Pass overwrite=true to replace it, "
            "or choose another name"
        )
    return path


def cell(value: Any) -> Any:
    """One CSV cell: lists are joined, nested objects become JSON, None is blank."""
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return LIST_SEPARATOR.join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return value


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    source: dict[str, Any],
    as_of: str | None,
    parameters: dict[str, Any],
    tool: str,
) -> dict[str, Any]:
    """Write the rows with a provenance header. Returns what was written."""
    columns = list(dict.fromkeys(key for row in rows for key in row))
    header = [
        f"{COMMENT} tool: {tool}",
        f"{COMMENT} source: {source.get('file_name')} ({source.get('format')})",
        f"{COMMENT} as_of: {as_of}",
        f"{COMMENT} parameters: {json.dumps(parameters, sort_keys=True, default=str)}",
    ]

    def write(temp_name: str) -> None:
        with open(temp_name, "w", newline="", encoding="utf-8") as handle:
            handle.write("\n".join(header) + "\n")
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([cell(row.get(column)) for column in columns])

    write_atomically(path, write)
    return {"file_name": path.name, "rows": len(rows), "columns": columns}
