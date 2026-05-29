from __future__ import annotations

import logging

logger = logging.getLogger("lab_wizard.wizard.backend.python_formatting")


def format_python_code(code: str) -> str:
    """Format generated Python while keeping generation non-fatal."""
    try:
        import black
    except Exception as exc:  # pragma: no cover - dependency issue fallback
        logger.warning("Skipping generated Python formatting: %s", exc)
        return code

    try:
        return black.format_str(
            code,
            mode=black.FileMode(
                line_length=88,
                string_normalization=False,
                target_versions={black.TargetVersion.PY312},
            ),
        )
    except Exception:
        logger.exception("Generated Python formatting failed")
        return code
