"""Typed proxy classes for remote instrument behaviors."""

from lab_wizard.lib.client.proxies.base import RemoteOpaque, RemoteProxy
from lab_wizard.lib.client.proxies.counter import RemoteCounter
from lab_wizard.lib.client.proxies.registry import (
    PROXY_BY_BEHAVIOR,
    PROXY_BY_BEHAVIOR_ABC,
    PROXY_EXEMPT,
    proxy_class_for,
)
from lab_wizard.lib.client.proxies.vsense import RemoteVSense
from lab_wizard.lib.client.proxies.vsource import RemoteVSource

__all__ = [
    "RemoteVSource",
    "RemoteVSense",
    "RemoteCounter",
    "RemoteOpaque",
    "RemoteProxy",
    "PROXY_BY_BEHAVIOR",
    "PROXY_BY_BEHAVIOR_ABC",
    "PROXY_EXEMPT",
    "proxy_class_for",
]
