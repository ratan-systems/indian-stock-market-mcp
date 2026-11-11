"""Write a file in one step, so a failure never leaves half a file behind."""

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


def write_atomically(path: Path, write: Callable[[str], None]) -> None:
    """Have `write` fill a temporary file beside `path`, then move it into place.

    The temporary file is removed if anything goes wrong, and `path` is only
    replaced once the new content is complete.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(handle)
    try:
        write(temp_name)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def write_json_atomically(path: Path, content: Any) -> None:
    """Write `content` as indented JSON ending in a newline."""
    write_atomically(
        path, lambda temp: Path(temp).write_text(json.dumps(content, indent=2) + "\n")
    )
