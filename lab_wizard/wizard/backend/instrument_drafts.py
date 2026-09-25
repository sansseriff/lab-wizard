"""Temporary add chains. Staging and abandonment never write instrument YAML."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from lab_wizard.lib.utilities.config_io import (
    _node_to_tree_dict,
    load_instruments,
    prepare_instrument_chain,
)


@dataclass
class Draft:
    config: Path
    chain: list[dict] = field(default_factory=list)
    result: dict | None = None
    touched: float = field(default_factory=monotonic)
    lock: Any = field(default_factory=RLock)
    cancelled: bool = False


class InstrumentDrafts:
    """Workspace-scoped, expiring drafts with serialized, retryable commits."""

    ttl = 3600

    def __init__(self):
        self._drafts: dict[str, Draft] = {}
        self._lock = RLock()

    def create(self, config_dir: str | Path) -> str:
        with self._lock:
            for key, draft in list(self._drafts.items()):
                if monotonic() - draft.touched > self.ttl and draft.lock.acquire(
                    blocking=False
                ):
                    try:
                        del self._drafts[key]
                    finally:
                        draft.lock.release()
            if len(self._drafts) >= 256:
                raise ValueError(
                    "Too many open instrument drafts; close an existing wizard"
                )
            key = uuid4().hex
            self._drafts[key] = Draft(Path(config_dir).resolve())
            return key

    def get(self, config_dir: str | Path, key: str) -> Draft:
        with self._lock:
            draft = self._drafts.get(key)
            if draft is None or draft.config != Path(config_dir).resolve():
                raise ValueError(
                    "Instrument draft expired or is unavailable; reopen Add instrument"
                )
            return draft

    def stage(self, config_dir: str | Path, key: str, chain: list[dict]) -> dict:
        draft = self.get(config_dir, key)
        with draft.lock:
            self._check(draft)
            if draft.result is not None:
                raise ValueError("This draft has already been added")
            instruments = load_instruments(config_dir, include_disabled=True)
            result = prepare_instrument_chain(instruments, chain)
            draft.chain = deepcopy(chain)
            draft.touched = monotonic()
            return {
                **result,
                "tree": [_node_to_tree_dict(k, v) for k, v in instruments.items()],
            }

    def chain(self, config_dir: str | Path, key: str) -> list[dict]:
        draft = self.get(config_dir, key)
        with draft.lock:
            self._check(draft)
            draft.touched = monotonic()
            return deepcopy(draft.chain)

    def commit(self, config_dir: str | Path, key: str, save: Callable) -> dict:
        draft = self.get(config_dir, key)
        with draft.lock:
            self._check(draft)
            if draft.result is None:
                # Validation is repeated against current config by the guarded writer.
                draft.result = save(deepcopy(draft.chain))
            draft.touched = monotonic()
            return deepcopy(draft.result)

    def cancel(self, config_dir: str | Path, key: str) -> None:
        with self._lock:
            if key not in self._drafts:
                return  # Retrying an already-discarded draft is harmless.
        draft = self.get(config_dir, key)
        with draft.lock:
            if draft.result is not None:
                raise ValueError(
                    "This draft has already been added; refresh the instrument tree"
                )
            draft.cancelled = True
        with self._lock:
            self._drafts.pop(key, None)

    def _check(self, draft: Draft) -> None:
        if draft.cancelled or monotonic() - draft.touched > self.ttl:
            raise ValueError("Instrument draft expired; reopen Add instrument")


drafts = InstrumentDrafts()
