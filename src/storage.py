"""Persistent user data shared by source and packaged entry points."""
import json
import os
import tempfile
from pathlib import Path


DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TennisBooking"


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
