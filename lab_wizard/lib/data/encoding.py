"""Turning recorded values into JSON the database can store and query."""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

__all__ = ["to_json", "jsonable"]


def jsonable(value: Any) -> Any:
    """``value`` as plain JSON data.

    NaN and infinity become ``None``: they are not JSON, and SQLite's JSON
    functions reject a document containing them. numpy scalars and arrays
    (``.item()`` / ``.tolist()``) become numbers and lists, so a histogram
    recorded as an array is stored as an array.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    tolist = getattr(value, "tolist", None)
    if callable(tolist):  # numpy arrays and scalars both have it
        return jsonable(tolist())
    item = getattr(value, "item", None)
    if callable(item):
        return jsonable(item())
    return str(value)


def to_json(value: Any) -> str:
    return json.dumps(jsonable(value), allow_nan=False, separators=(",", ":"))
