"""Typed proxy classes for remote instrument behaviors."""

from lab_wizard.lib.client.proxies.base import RemoteOpaque, RemoteProxy
from lab_wizard.lib.client.proxies.registry import (
    PROXY_BY_BEHAVIOR_ABC,
    proxy_class_for,
)
from lab_wizard.lib.client.proxies.vsense import RemoteVSense
from lab_wizard.lib.client.proxies.vsource import RemoteVSource

__all__ = [
    "RemoteVSource",
    "RemoteVSense",
    "RemoteOpaque",
    "RemoteProxy",
    "PROXY_BY_BEHAVIOR_ABC",
    "proxy_class_for",
]
