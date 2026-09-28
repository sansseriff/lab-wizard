"""Which configured instruments can fill a measurement's role.

A role asks for a behavior (``VSource``, ``Counter``, ...); every instrument
class that has it is a match.
"""

import logging
from pathlib import Path
from typing import List

from lab_wizard.lib.utilities.resource_catalog import get_instrument_metadata
from lab_wizard.wizard.backend.models import Env, MatchingReq

logger = logging.getLogger("lab_wizard.wizard.backend.get_measurements")


def discover_matching_instruments(
    env: Env, base_type: type, verbose: bool = False
) -> List[MatchingReq]:
    """Match registered instruments/channels using catalog behavior metadata.

    The old implementation imported every Python file beneath ``instruments``
    for every request.  Runtime catalog auditing has already performed the
    authoritative ``issubclass`` checks and persisted their wire-safe result.
    """
    behavior_name = getattr(base_type, "__name__", str(base_type))
    found: dict[str, MatchingReq] = {}
    for info in get_instrument_metadata().values():
        for prefix, behavior_key in (
            ("resource", "behavior_abc"),
            ("channel", "channel_behavior_abc"),
        ):
            if info.get(behavior_key) != behavior_name:
                continue
            module_name = info.get(f"{prefix}_module")
            class_name = info.get(f"{prefix}_class_name")
            if not isinstance(module_name, str) or not isinstance(class_name, str):
                continue
            module_prefix = "lab_wizard.lib.instruments."
            if module_name.startswith(module_prefix):
                relative = Path(*module_name[len(module_prefix):].split(".")).with_suffix(".py")
                file_path = env.instruments_dir / relative
            else:
                file_path = env.instruments_dir
            qualname = f"{module_name}.{class_name}"
            found.setdefault(
                qualname,
                MatchingReq(
                    module=module_name,
                    class_name=class_name,
                    qualname=qualname,
                    file_path=file_path,
                    friendly_name=class_name,
                ),
            )

    matches = sorted(found.values(), key=lambda match: match.qualname)

    logger.info("Discovered %d matches for base type %s", len(matches), base_type)
    if verbose:
        logger.debug("Matches for base type %s: %s", base_type, matches)
    return matches
