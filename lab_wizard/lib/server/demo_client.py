"""Tiny smoke-test client for the Phase 1 wire.

Phase 2 replaces this with the real client package (RemoteResources + typed proxies).
For now this is just enough to verify the wire moves real data.

Examples:
    python -m lab_wizard.lib.server.demo_client \\
        --server tcp://127.0.0.1:12300 list_paths

    python -m lab_wizard.lib.server.demo_client \\
        --server tcp://127.0.0.1:12300 call \\
        inst://2da0863e/a0da5bfa/channel/1 set_voltage --args '[0.5]'
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any, Optional

import zmq

from pyleco.core.message import Message
from pyleco.core.serialization import MessageTypes


def _send_request(
    server: str,
    method: str,
    params: dict[str, Any] | list[Any] | None,
    timeout_ms: int = 5000,
) -> Any:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.connect(server)

    request: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
    }
    if params is not None:
        request["params"] = params

    msg = Message(
        receiver=b"lab_wizard_server",
        sender=b"demo_client",
        data=request,
        message_type=MessageTypes.JSON,
    )
    sock.send_multipart(msg.to_frames())

    try:
        frames = sock.recv_multipart()
    finally:
        sock.close(linger=0)

    reply = Message.from_frames(*frames)
    return reply.data


def _parse_json_arg(text: str, name: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--{name}: invalid JSON ({exc}): {text!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo_client")
    parser.add_argument("--server", default="tcp://127.0.0.1:12300")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list_paths", help="List all inst:// paths exposed by the server.")
    sub.add_parser("list_attributes", help="List all attribute_name -> path mappings.")

    call_p = sub.add_parser("call", help="Invoke method on inst:// path.")
    call_p.add_argument("path", help="inst:// path of the target instrument.")
    call_p.add_argument("method", help="Method name to invoke on the instrument.")
    call_p.add_argument("--args", default="[]", help="JSON array of positional args.")
    call_p.add_argument("--kwargs", default="{}", help="JSON object of keyword args.")

    args = parser.parse_args(argv)

    params: Optional[dict[str, Any]]
    if args.cmd == "list_paths":
        params = None
        method = "list_paths"
    elif args.cmd == "list_attributes":
        params = None
        method = "list_attributes"
    elif args.cmd == "call":
        params = {
            "path": args.path,
            "method": args.method,
            "args": _parse_json_arg(args.args, "args"),
            "kwargs": _parse_json_arg(args.kwargs, "kwargs"),
        }
        method = "call"
    else:
        parser.error(f"Unknown subcommand {args.cmd!r}")
        return 2

    reply = _send_request(args.server, method, params)
    print(json.dumps(reply, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
