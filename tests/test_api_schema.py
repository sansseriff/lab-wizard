"""The frontend's generated API types are built from the backend as it is.

``src/lib/api/openapi.json`` (and ``schema.d.ts`` from it) is committed, so the
frontend builds without Python. When a route changes, regenerate them with
``bun run api`` in the frontend; this test says when that was forgotten.
"""

from __future__ import annotations

import json
from pathlib import Path

from lab_wizard.wizard.backend.openapi import schema

COMMITTED = Path(__file__).parents[1] / "lab_wizard" / "wizard" / "frontend" / "src" / "lib" / "api" / "openapi.json"


def test_the_committed_api_schema_is_current():
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(schema())), (
        "The API changed since the frontend's types were generated: run `bun run api` in lab_wizard/wizard/frontend."
    )
