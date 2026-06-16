"""Load the graph schema from a JSON file."""
from __future__ import annotations

import json
from pathlib import Path

from lorekeep.models import Schema


def load_schema(path: Path) -> Schema:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Schema.load(data)
