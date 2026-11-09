"""MCP server (stdio, no dependencies) exposing the desk agent to AI clients.

Runs unelevated like the CLI and forwards every call to the elevated agent, so the agent's
allow-list and action policy apply to MCP clients too.

Register (Claude Code):
  claude mcp add desk -- C:\\Dev\\desk-agent\\.venv\\Scripts\\python.exe C:\\Dev\\desk-agent\\desk.py mcp
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile

PROTOCOL = "2025-06-18"

INSTRUCTIONS = (
    "Drives Windows desktop apps (the agent's profile decides which) without moving the user's mouse. "
    "Read desk_notes once first (app-specific names and pitfalls). Use desk_find for one element or desk_snapshot (interactive=true, root=@eN to narrow) for the tree; "
    "act with desk_click/desk_type/desk_key and diff=true to get what changed in the same call; "
    "use desk_wait instead of sleeping. Errors start with a code and retry=safe|unsafe|never; retry only "
    "when safe (STALE_REF: snapshot again first). Use desk_shot when labels are unclear (ribbon items show "
    "internal control names). POLICY_DENIED: ask the user, then retry with confirm=true only if they agree."
)

RO = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
ACT = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


S = {"type": "string"}
B = {"type": "boolean"}
I = {"type": "integer"}
CONFIRM = {"type": "boolean", "description": "Set only after the user explicitly approved this exact action."}

TOOLS = [
    {"name": "desk_snapshot", "annotations": RO,
     "description": "Compact tree of the allowed app windows with @eN refs (win32 controls, web view, ribbons, grids).",
     "inputSchema": _obj({"window": {**S, "description": "w1, hwnd or part of the title; all windows if omitted"},
                          "diff": {**B, "description": "only lines changed since the previous snapshot"},
                          "no_web": {**B, "description": "skip the embedded web view (faster)"},
                          "max": {**I, "description": "max items per web view / accessible window (default 300)"},
                          "root": {**S, "description": "only this element (@eN) and its subtree"},
                          "interactive": {**B, "description": "only lines that carry a ref"}})},
    {"name": "desk_find", "annotations": RO,
     "description": "Only the elements whose text/name/value contains query (cheaper than a full snapshot). Returns refs.",
     "inputSchema": _obj({"query": S, "role": {**S, "description": "button, tab, edit, link, row, combobox, ..."},
                          "window": S, "no_web": B}, ["query"])},
    {"name": "desk_wait", "annotations": RO,
     "description": "Block until exactly one condition holds: text appears, text is gone, a window title appears, or the UI stops changing.",
     "inputSchema": _obj({"text": S, "gone": S, "title": S, "stable": B, "timeout": {"type": "number"},
                          "window": S, "no_web": B})},
    {"name": "desk_click", "annotations": ACT,
     "description": "Click an element (@e12), or a window/element at x,y in the pixel space of desk_shot of the same ref. "
                    "diff=true waits until the window settles and returns what changed.",
     "inputSchema": _obj({"ref": S, "at": {"type": "array", "items": I, "minItems": 2, "maxItems": 2},
                          "confirm": CONFIRM, "diff": B}, ["ref"])},
    {"name": "desk_type", "annotations": ACT,
     "description": "Set the text of an input/edit/combo, or pick a select option by text or value.",
     "inputSchema": _obj({"ref": S, "text": S, "diff": B}, ["ref", "text"])},
    {"name": "desk_key", "annotations": ACT,
     "description": "Send a key (enter, tab, esc, f5, ...) to an element.",
     "inputSchema": _obj({"ref": S, "key": S, "confirm": CONFIRM, "diff": B}, ["ref", "key"])},
    {"name": "desk_read", "annotations": RO,
     "description": "Full text of an element: table rows tab-separated, input value, grid row cells.",
     "inputSchema": _obj({"ref": S}, ["ref"])},
    {"name": "desk_shot", "annotations": RO,
     "description": "PNG of one window (works when covered, not when minimized) or of one element.",
     "inputSchema": _obj({"target": {**S, "description": "w1 / title / @eN; first window if omitted"}})},
    {"name": "desk_acc", "annotations": RO,
     "description": "Full accessibility (MSAA) tree of a window or element, including every grid row.",
     "inputSchema": _obj({"target": S, "max": I}, ["target"])},
    {"name": "desk_windows", "annotations": RO, "description": "List the allowed app windows.",
     "inputSchema": _obj({})},
    {"name": "desk_notes", "annotations": RO,
     "description": "Notes for the app profile the agent runs with: layout, internal names, pitfalls. Read before the first action.",
     "inputSchema": _obj({})},
    {"name": "desk_doctor", "annotations": RO,
     "description": "Diagnose problems: agent privileges, target app running, minimized windows, policy, recent errors.",
     "inputSchema": _obj({})},
]


def _desk():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    import desk  # noqa: PLC0415  (the CLI module holds the client side of the protocol)
    return desk


def call_tool(name: str, args: dict) -> tuple[list[dict], bool]:
    desk = _desk()
    if name == "desk_doctor":
        st = desk.load_state()
        if not st or not desk.alive():
            return [{"type": "text", "text": "agent is not running. The user has to run `desk start` "
                                             "(one UAC prompt) in C:\\Dev\\desk-agent."}], True
    cmd = name.removeprefix("desk_")
    req = {"cmd": cmd, **{k: v for k, v in args.items() if v is not None}}
    shot_path = None
    if cmd == "shot":
        shot_path = os.path.join(tempfile.gettempdir(), f"desk-mcp-shot-{os.getpid()}.png")
        req["path"] = shot_path
    try:
        resp = desk.send(req, timeout=max(120, float(args.get("timeout") or 0) + 30))
    except SystemExit as e:  # desk.send reports "agent not running" this way
        return [{"type": "text", "text": f"NOT_RUNNING retry=never: {e}\nhint: the user has to run `desk start` "
                                         "(one UAC prompt)."}], True
    out = desk.clean(resp.get("out", ""))
    if not resp.get("ok"):
        return [{"type": "text", "text": out}], True
    if shot_path and os.path.exists(shot_path):
        with open(shot_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return [{"type": "image", "data": data, "mimeType": "image/png"}, {"type": "text", "text": out}], False
    return [{"type": "text", "text": out}], False


def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method", "")
    if mid is None:  # notification
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "desk-agent", "version": "0.2.0"}, "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        name = p.get("name", "")
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            content, is_err = call_tool(name, p.get("arguments") or {})
        except Exception as e:  # noqa: BLE001
            content, is_err = [{"type": "text", "text": f"{type(e).__name__}: {e}"}], True
        result = {"content": content, "isError": is_err}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8"))
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(msg)
        if resp is not None:
            stdout.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()


if __name__ == "__main__":
    main()
