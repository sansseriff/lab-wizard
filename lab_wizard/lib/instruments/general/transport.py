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

**children_claimable** — may a run claim one child or channel of this node
without claiming the node itself? Declared on any params node, not only roots.

    False  a claim on anything beneath this node claims the whole node. The
           default: correct for any instrument nobody has reasoned about.
    True   each direct child (or channel) is an independent claim unit. Only
           declare this when two things hold: every operation on a child
           re-establishes, within one call, all the shared state it depends on;
           and that shared state can only be changed through this node itself.
           The 53220A qualifies — ``count()`` re-arms from its settings in one
           call, and trigger and gate are methods on the counter, not on an
           input — so two runs can hold its two inputs at once.

See ``plans/server_plan.md`` Phase 9.

Defaults are the conservative pair (``exclusive`` / ``inferred``), so a new
instrument is safe until someone thinks about it.
"""

from __future__ import annotations

from typing import Literal


TransportSharing = Literal["exclusive", "shared"]
StateAuthority = Literal["inferred", "subscribed"]

DEFAULT_TRANSPORT_SHARING: TransportSharing = "exclusive"
DEFAULT_STATE_AUTHORITY: StateAuthority = "inferred"
DEFAULT_CHILDREN_CLAIMABLE = False


__all__ = [
    "TransportSharing",
    "StateAuthority",
    "DEFAULT_TRANSPORT_SHARING",
    "DEFAULT_STATE_AUTHORITY",
    "DEFAULT_CHILDREN_CLAIMABLE",
]
