"""lab_wizard server — host an instrument tree and expose user-facing methods via RPC.

Phase 1: a single RPC `call(path, method, args, kwargs)` that resolves an
``inst://...`` path and invokes the named method on the resolved object.
No proxies, permissions, or concurrency yet.
"""
