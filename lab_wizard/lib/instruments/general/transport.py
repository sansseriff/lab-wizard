"""How a root instrument's transport may be shared, and who owns its state.

Two independent facts the server cannot infer and only the instrument knows.
They are declared here rather than hard-coded server-side for the same reason
``_state_methods_`` is: the instrument layer owns the knowledge, and the server
consumes it without importing anything about specific hardware.

**transport_sharing** — can a second OS process talk to this hardware at all?

    "exclusive"  one process may hold it. Serial and VISA sockets: the OS gives
                 one handle, and a stateful controller makes interleaving
                 impossible even if it didn't. A Prologix query is
                 ``++addr N`` then the command; two processes interleaving
                 means one's command lands on the other's instrument, and with
                 ``++auto 1`` the reply carries no address to tell them apart.
    "shared"     the hardware sits behind a server that already multiplexes,
                 so any number of clients may connect. No arbitration needed.

**state_authority** — where does the truth about this instrument's state live?

    "inferred"   we know the state because we set it. The permission gate
                 records what it sent (see ``_state_methods_``).
    "subscribed" something else can change this hardware and reports that it
                 did. Recording only our own writes would leave the gate
                 believing a fiction, so state must be read from that authority
                 instead of inferred.

The two are orthogonal: an instrument with a front panel is exclusive *and*
externally mutated.

Defaults are the conservative pair (``exclusive`` / ``inferred``), so a new
instrument is safe until someone thinks about it.
"""

from __future__ import annotations

from typing import Literal


TransportSharing = Literal["exclusive", "shared"]
StateAuthority = Literal["inferred", "subscribed"]

DEFAULT_TRANSPORT_SHARING: TransportSharing = "exclusive"
DEFAULT_STATE_AUTHORITY: StateAuthority = "inferred"


__all__ = [
    "TransportSharing",
    "StateAuthority",
    "DEFAULT_TRANSPORT_SHARING",
    "DEFAULT_STATE_AUTHORITY",
]
