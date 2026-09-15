"""ClaimTable: the claim rules, without a server or hardware."""

from __future__ import annotations

import pytest

from lab_wizard.lib.server.claims import ClaimConflict, ClaimTable, covers, overlaps


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def table(clock: Clock) -> ClaimTable:
    return ClaimTable(clock=clock, wallclock=clock)


def _claim(table: ClaimTable, *units: str, holder: str = "run", ttl_s: float = 30.0):
    return table.acquire(list(units), holder=holder, peer="test", ttl_s=ttl_s)


def test_covering_and_overlap_are_prefix_by_path_segment():
    assert covers("inst://c", "inst://c/channel/0")
    assert not covers("inst://c/channel/0", "inst://c")
    assert overlaps("inst://c/channel/0", "inst://c")
    # A shared prefix that is not a path segment is not ancestry.
    assert not covers("inst://c1", "inst://c10")
    assert not overlaps("inst://c/channel/0", "inst://c/channel/1")


def test_sibling_claims_coexist(table: ClaimTable):
    _claim(table, "inst://counter/channel/0", holder="calibration")
    _claim(table, "inst://counter/channel/1", holder="test detector")
    assert len(table.snapshot()) == 2


@pytest.mark.parametrize(
    ("held", "wanted"),
    [
        ("inst://counter/channel/0", "inst://counter"),  # ancestor of a held unit
        ("inst://counter", "inst://counter/channel/1"),  # descendant of a held unit
        ("inst://counter/channel/0", "inst://counter/channel/0"),
    ],
)
def test_overlapping_claims_are_refused_with_the_holder(table: ClaimTable, held, wanted):
    _claim(table, held, holder="calibration")
    with pytest.raises(ClaimConflict, match="claimed by calibration"):
        _claim(table, wanted, holder="second")


def test_acquisition_is_all_or_nothing(table: ClaimTable):
    _claim(table, "inst://counter/channel/1", holder="first")
    with pytest.raises(ClaimConflict):
        _claim(table, "inst://source", "inst://counter/channel/1", holder="second")
    # The unit that was free is still free.
    _claim(table, "inst://source", holder="third")


def test_nested_units_in_one_request_collapse_to_the_outer_one(table: ClaimTable):
    claim = _claim(table, "inst://counter/channel/0", "inst://counter", "inst://counter")
    assert claim.units == ("inst://counter",)


def test_a_claim_expires_without_renewal_and_renewal_keeps_it(table: ClaimTable, clock: Clock):
    kept = _claim(table, "inst://a", ttl_s=10)
    dropped = _claim(table, "inst://b", ttl_s=10)
    clock.now += 8
    table.renew(kept.token)
    clock.now += 8

    assert table.live(kept.token) is not None
    assert table.live(dropped.token) is None
    with pytest.raises(KeyError):
        table.renew(dropped.token)


def test_every_expiry_is_reported_once_however_it_was_noticed(table: ClaimTable, clock: Clock):
    """An expiry first seen by an unrelated query must still be handed over for
    its baseline restore, or its units would stay unavailable forever."""
    claim = _claim(table, "inst://a", ttl_s=1)
    clock.now += 5
    assert table.touching("inst://a")  # notices the expiry; still restoring

    reported = table.pop_expired()
    assert [c.token for c in reported] == [claim.token]
    assert table.pop_expired() == []


def test_released_units_stay_unavailable_until_restored(table: ClaimTable):
    """A reset that ran after the next holder started would wipe its settings."""
    claim = _claim(table, "inst://counter/channel/0", holder="first")
    table.release(claim.token)

    with pytest.raises(ClaimConflict, match="being reset to baseline after first"):
        _claim(table, "inst://counter", holder="second")

    table.restored(claim.token)
    _claim(table, "inst://counter", holder="second")


def test_tokens_are_never_listed(table: ClaimTable):
    claim = _claim(table, "inst://a")
    assert all(claim.token not in str(entry.values()) for entry in table.snapshot())


def test_an_empty_or_timeless_claim_is_rejected(table: ClaimTable):
    with pytest.raises(ValueError):
        _claim(table)
    with pytest.raises(ValueError):
        _claim(table, "inst://a", ttl_s=0)
