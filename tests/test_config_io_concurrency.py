"""The config reader must survive being called from several threads at once.

FastAPI dispatches sync endpoints to a threadpool, so a page that issues
several config-reading requests in parallel — the Overview page fans out to
seven — has every one of them land on `config_io` simultaneously.

`ruamel.yaml.YAML(typ="rt")` keeps parser, composer and constructor state on the
instance, so a shared instance used concurrently interleaves that state. The
symptom is not a lock-up but a *lie*: a half-constructed mapping surfaces as
`Missing/invalid 'type' in <file>`, or a well-formed file raises
`DuplicateKeyError`. Both look exactly like a corrupt config, which is what
makes the bug expensive to diagnose in the field.

Without serialisation this test fails within a few iterations; the failure is
probabilistic, so it runs enough rounds to make a regression loud.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lab_wizard.lib.utilities.config_io import get_configured_tree


REPO_CONFIG = Path(__file__).resolve().parent.parent / "config"


def _write_rack(config_dir: Path) -> None:
    """A parent with several children, mirroring the real directory layout."""
    instruments = config_dir / "instruments"
    parent_dir = instruments / "dbay_key_2da0863e"
    parent_dir.mkdir(parents=True)

    (instruments / "dbay_key_2da0863e.yml").write_text(
        "type: dbay\n"
        "enabled: true\n"
        "ip_address: 127.0.0.1\n"
        "ip_port: 8345\n"
        "attribute_name: rack\n",
        encoding="utf-8",
    )

    # Several children, because the reader walks them in one pass and a race
    # needs more than one document in flight to show itself.
    for slot, key in enumerate(["8773e759", "98ae61f6", "d0c24131", "cb90d442"]):
        (parent_dir / f"dac4D_key_{key}.yml").write_text(
            f"type: dac4D\n"
            f"enabled: true\n"
            f"slot: {slot}\n"
            f"attribute_name: dac_{slot}\n",
            encoding="utf-8",
        )


def test_concurrent_reads_do_not_corrupt_each_other(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_rack(config_dir)

    expected = get_configured_tree(str(config_dir))
    assert expected, "fixture should produce a non-empty tree"

    errors: list[BaseException] = []
    results: list[object] = []

    def read() -> None:
        try:
            results.append(get_configured_tree(str(config_dir)))
        except BaseException as exc:  # noqa: BLE001 - the point is to catch any
            errors.append(exc)

    # Sixteen threads over several rounds: enough overlap that an unserialised
    # parser reliably trips, without making the suite slow.
    with ThreadPoolExecutor(max_workers=16) as pool:
        for _ in range(12):
            list(pool.map(lambda _: read(), range(16)))

    assert not errors, f"concurrent reads raised: {errors[:3]}"
    assert all(r == expected for r in results), "a concurrent read returned a different tree"


@pytest.mark.skipif(not REPO_CONFIG.is_dir(), reason="repo config/ not present")
def test_concurrent_reads_of_the_repo_config(tmp_path: Path) -> None:
    """The same, against this workspace's real multi-rack config.

    Nested racks (prologix → sim900 → sim928) put more documents in flight per
    call than the synthetic fixture, which is what the wizard actually reads.
    """
    expected = get_configured_tree(str(REPO_CONFIG))

    errors: list[BaseException] = []

    def read() -> None:
        try:
            assert get_configured_tree(str(REPO_CONFIG)) == expected
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=12) as pool:
        for _ in range(8):
            list(pool.map(lambda _: read(), range(12)))

    assert not errors, f"concurrent reads raised: {errors[:3]}"
