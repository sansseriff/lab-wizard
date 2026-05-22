"""lab_wizard client — connect to a remote server and use instruments via proxies.

Public surface:
    RemoteExp.connect(url) -> RemoteExp
    RemoteExp.from_attribute(name) -> proxy implementing the leaf's behavior ABC
    Session(url) -> low-level RPC client (used internally; also handy for diagnostics)

The proxies returned by ``from_attribute`` implement the same behavior ABCs
(``VSource``, ``VSense``) that local instruments do, so a measurement that
declares ``voltage_source: VSource`` accepts either a real instrument or a
``RemoteVSource`` without modification.
"""

from lab_wizard.lib.client.remote_exp import RemoteExp
from lab_wizard.lib.client.session import (
    PermissionDeniedError,
    RemoteCallError,
    Session,
)

__all__ = ["RemoteExp", "Session", "RemoteCallError", "PermissionDeniedError"]
