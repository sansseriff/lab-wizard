"""Write the wizard API's OpenAPI schema, for the frontend's generated types.

    python -m lab_wizard.wizard.backend.openapi > lab_wizard/wizard/frontend/src/lib/api/openapi.json

``bun run api`` in the frontend does this and regenerates ``schema.d.ts`` from
it; ``tests/test_api_schema.py`` fails when the committed schema is stale.
"""

from __future__ import annotations

import json
import sys
from typing import Any

__all__ = ["schema"]


def schema() -> dict[str, Any]:
    from lab_wizard.wizard.backend.main import app

    return app.openapi()


if __name__ == "__main__":
    json.dump(schema(), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
