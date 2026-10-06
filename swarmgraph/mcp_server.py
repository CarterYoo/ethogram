"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0), standard library only."""
import json
import sys
import traceback

from . import __version__
from .tools import TOOLS, Session, call

PROTOCOL = "2025-06-18"


def handle(session, msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification (e.g. notifications/initialized)
        return None
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "swarmgraph", "version": __version__},
                  "instructions": "Investigate multi-agent datasets: start with overview and signals; every claim "
                                  "should cite event ids and be checked with get_event. Hypotheses are pre-registered "
                                  "with a metric plan, measured by code, and verdicts are gated."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]}
    elif method == "tools/call":
        p = msg.get("params", {})
        try:
            out = call(session, p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}
        except Exception as ex:  # tool errors go back to the model, not the transport
            result = {"content": [{"type": "text", "text": f"error: {ex}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(db_path=None):
    session = Session(db_path)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            reply = handle(session, msg)
        except Exception:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": traceback.format_exc(limit=1)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()
